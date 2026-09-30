"""Buffered synthesis and single-pass, context-managed HTTP audio streaming."""

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx

from . import _http
from ._validation import validate_model_id
from .errors import NoSpeechGeneratedError
from .providers import AzureOpenAIProvider, OpenAIProvider, XAIProvider
from .types import (
    AudioData,
    AudioOutput,
    PreparedRequest,
    ResolvedModel,
    SpeechMetadata,
    SpeechResult,
    SpeechStream,
    StreamMetadata,
)

__all__ = ["generate_speech", "stream_speech"]


def _configured_model(model: ResolvedModel) -> ResolvedModel:
    if not isinstance(model.provider, (OpenAIProvider, XAIProvider, AzureOpenAIProvider)):
        raise ValueError("Unknown speech provider")
    return model.provider.model(validate_model_id(model.model_id))


def _resolve(model: str | ResolvedModel) -> ResolvedModel:
    if isinstance(model, ResolvedModel):
        return _configured_model(model)
    provider, separator, model_id = validate_model_id(model).partition("/")
    if provider == "openai":
        return OpenAIProvider().model(model_id if separator else None)
    if provider == "xai":
        return XAIProvider().model(model_id if separator else None)
    if provider == "azure":
        return AzureOpenAIProvider().model(model_id if separator else None)
    raise ValueError("Unknown speech provider")


async def generate_speech(
    *,
    model: str | ResolvedModel,
    text: str,
    voice: str,
    output: AudioOutput | None = None,
    instructions: str | None = None,
    provider_options: Mapping[str, object] | None = None,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_retries: int = 2,
    headers: Mapping[str, str] | None = None,
) -> SpeechResult:
    started = time.monotonic()
    resolved = _resolve(model)
    prepared = resolved.provider.prepare(
        model_id=resolved.model_id,
        text=text,
        voice=voice,
        output=output,
        instructions=instructions,
        provider_options=provider_options,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        headers=headers,
    )
    async with _http.open_response(prepared, client=http_client, buffered=True) as opened:
        return SpeechResult(
            audio=AudioData(opened.response.content, opened.media_type),
            provider=prepared.provider,
            model=prepared.model,
            metadata=SpeechMetadata(prepared.input_chars, (time.monotonic() - started) * 1000),
        )


class _AudioIterator(AsyncIterator[bytes]):
    """No reader task: each anext pulls only until the next nonempty chunk."""

    def __init__(self, prepared: PreparedRequest, response: httpx.Response) -> None:
        self.prepared = prepared
        self.response = response
        self.iterator = response.aiter_bytes()
        self.active = True
        self.busy = False
        self.done = False
        self.received = False

    async def __anext__(self) -> bytes:
        if not self.active:
            raise RuntimeError("Audio iterator is valid only inside its context")
        if self.busy:
            raise RuntimeError("Audio iterator does not support concurrent consumption")
        if self.done:
            raise StopAsyncIteration
        self.busy = True
        try:
            return await self._next_chunk()
        except BaseException:
            # Cleanup only; cancellation and programmer errors remain unchanged.
            self.done = True
            await self.response.aclose()
            raise
        finally:
            self.busy = False

    async def _next_chunk(self) -> bytes:
        try:
            async for chunk in self.iterator:
                if not self.active:
                    raise RuntimeError("Audio iterator is valid only inside its context")
                if chunk:
                    self.received = True
                    return chunk
        except _http._ELIGIBLE as error:
            raise _http.network_error(
                self.prepared, retryable=False, response=self.response
            ) from error
        if not self.received:
            context = _http.network_error(self.prepared, retryable=False, response=self.response)
            raise NoSpeechGeneratedError(
                provider=context.provider,
                model=context.model,
                status_code=context.status_code,
                request_id=context.request_id,
                retryable=False,
            )
        raise StopAsyncIteration


@asynccontextmanager
async def _stream(
    prepared: PreparedRequest, client: httpx.AsyncClient | None
) -> AsyncIterator[SpeechStream]:
    async with _http.open_response(prepared, client=client) as opened:
        audio = _AudioIterator(prepared, opened.response)
        try:
            setup = opened.setup_latency_ms
            if setup is None:
                raise RuntimeError("Streaming response missing setup timing")
            yield SpeechStream(
                audio=audio,
                media_type=opened.media_type,
                provider=prepared.provider,
                model=prepared.model,
                metadata=StreamMetadata(prepared.input_chars, setup),
            )
        finally:
            audio.active = False


def stream_speech(
    *,
    model: str | ResolvedModel,
    text: str,
    voice: str,
    output: AudioOutput | None = None,
    instructions: str | None = None,
    provider_options: Mapping[str, object] | None = None,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_retries: int = 2,
    headers: Mapping[str, str] | None = None,
) -> AbstractAsyncContextManager[SpeechStream]:
    """Return a context manager; audio is single-pass and valid only inside it."""

    @asynccontextmanager
    async def managed() -> AsyncIterator[SpeechStream]:
        resolved = _resolve(model)
        prepared = resolved.provider.prepare(
            model_id=resolved.model_id,
            text=text,
            voice=voice,
            output=output,
            instructions=instructions,
            provider_options=provider_options,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers,
        )
        async with _stream(prepared, http_client) as stream:
            yield stream

    return managed()
