"""One HTTP ownership/retry path for buffered and future streaming adapters."""

import asyncio
import json
import math
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.message import Message
from email.utils import parsedate_to_datetime

import httpx

from .errors import NoSpeechGeneratedError, ProviderError
from .types import AudioData, PreparedRequest

_ELIGIBLE = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
)
# No verified terminal provider-code fixtures yet; unknown codes follow status rules.
_MIME_ALIASES = {
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-flac": "audio/flac",
    "audio/x-aac": "audio/aac",
    "audio/x-pcm": "audio/pcm",
}


def _random_fraction() -> float:
    return secrets.randbelow(1_000_001) / 1_000_000


@dataclass(frozen=True)
class RetryTiming:
    """Internal test injection, not a public retry engine."""

    clock: Callable[[], float] = time.time
    random: Callable[[], float] = _random_fraction
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class OpenedResponse:
    response: httpx.Response
    media_type: str
    setup_latency_ms: float | None
    latency_ms: float | None


def _retry_after(value: str | None, now: float) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = max(0.0, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, 60.0)


def retry_delay(attempt: int, header: str | None, timing: RetryTiming) -> float:
    backoff = min(float(2 ** min(attempt, 6)) * (1 + timing.random()), 60.0)
    server = _retry_after(header, timing.clock())
    if server is None:
        return backoff
    return max(backoff, server + timing.random() * 0.25)


def _string_field(value: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        item = value.get(name)
        if isinstance(item, str):
            return item
    return None


def _error_fields(details: object) -> tuple[str | None, str | None]:
    if not isinstance(details, dict):
        return None, None
    nested = details.get("error")
    code = _string_field(nested, "code") if isinstance(nested, dict) else None
    return code or _string_field(details, "code"), _string_field(details, "request_id", "requestId")


def _retryable_status(status: int) -> bool:
    return status == 429 or (500 <= status <= 599 and status != 501)


def _provider_error(
    request: PreparedRequest, response: httpx.Response, timing: RetryTiming
) -> ProviderError:
    raw = response.text
    try:
        details: object = json.loads(raw)
    except ValueError:
        details = None
    code, body_id = _error_fields(details)
    status = response.status_code
    return ProviderError(
        provider=request.provider,
        model=request.model,
        status_code=status,
        code=code,
        request_id=response.headers.get("request-id")
        or response.headers.get("x-request-id")
        or body_id,
        details=details,
        raw_response=raw,
        retryable=_retryable_status(status),
        retry_after=_retry_after(response.headers.get("retry-after"), timing.clock()),
    )


def network_error(
    request: PreparedRequest, *, retryable: bool, response: httpx.Response | None = None
) -> ProviderError:
    """After publication providers wrap eligible read failures with retryable=False."""
    return ProviderError(
        provider=request.provider,
        model=request.model,
        retryable=retryable,
        status_code=response.status_code if response is not None else None,
        request_id=(response.headers.get("request-id") or response.headers.get("x-request-id"))
        if response is not None
        else None,
    )


def _mime_parts(value: str) -> tuple[str, str | None]:
    message = Message()
    message["content-type"] = value
    mime = value.split(";", 1)[0].strip().lower()
    rate = message.get_param("rate")
    return _MIME_ALIASES.get(mime, mime), rate if isinstance(rate, str) else None


def audio_media_type(request: PreparedRequest, response: httpx.Response) -> str:
    """Check MIME only, never guess codecs by decoding arbitrary audio bytes."""
    actual, rate = _mime_parts(response.headers.get("content-type", ""))
    expected, expected_rate = _mime_parts(request.media_type)
    if actual in ("", "application/octet-stream"):
        return request.media_type
    if actual != expected or (
        expected == "audio/pcm" and rate is not None and rate != expected_rate
    ):
        raise ProviderError(
            provider=request.provider,
            model=request.model,
            status_code=response.status_code,
            request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
        )
    return request.media_type


def _wire_request(request: PreparedRequest) -> httpx.Request:
    # Do not build_request(): that merges injected auth/cookies/default headers.
    return httpx.Request(
        "POST",
        request.url,
        headers=request.headers,
        content=request.content,
        extensions={"timeout": request.timeout.as_dict()},
    )


async def _checked_response(
    request: PreparedRequest, response: httpx.Response, timing: RetryTiming, buffered: bool
) -> str:
    if not 200 <= response.status_code < 300:
        await response.aread()
        raise _provider_error(request, response, timing)
    media_type = audio_media_type(request, response)
    if buffered:
        await response.aread()
        if not response.content:
            raise NoSpeechGeneratedError(
                provider=request.provider,
                model=request.model,
                status_code=response.status_code,
                retryable=True,
                request_id=response.headers.get("request-id")
                or response.headers.get("x-request-id"),
            )
    return media_type


async def _attempt(
    request: PreparedRequest, client: httpx.AsyncClient, timing: RetryTiming, buffered: bool
) -> tuple[httpx.Response, str]:
    response: httpx.Response | None = None
    succeeded = False
    try:
        response = await client.send(
            _wire_request(request), stream=True, auth=None, follow_redirects=False
        )
        media_type = await _checked_response(request, response, timing, buffered)
        succeeded = True
        return response, media_type
    except _ELIGIBLE as error:
        retryable = (
            response is None
            or 200 <= response.status_code < 300
            or _retryable_status(response.status_code)
        )
        raise network_error(request, retryable=retryable, response=response) from error
    finally:
        # Close any failed attempt, including arbitrary errors/cancellation during body reads.
        # Success ownership moves to open_response; no BaseException translation.
        if response is not None and not succeeded:
            await response.aclose()


async def _with_retries(
    request: PreparedRequest, client: httpx.AsyncClient, timing: RetryTiming, buffered: bool
) -> tuple[httpx.Response, str]:
    for attempt in range(request.max_retries + 1):
        try:
            return await _attempt(request, client, timing, buffered)
        except ProviderError as error:
            if not error.retryable or attempt == request.max_retries:
                raise
            header = str(error.retry_after) if error.retry_after is not None else None
            await timing.sleep(retry_delay(attempt, header, timing))
    raise RuntimeError("Invalid retry budget")


@asynccontextmanager
async def open_response(
    request: PreparedRequest,
    *,
    client: httpx.AsyncClient | None = None,
    timing: RetryTiming | None = None,
    buffered: bool = False,
) -> AsyncIterator[OpenedResponse]:
    """Own response and (only if created here) client across setup and consumer exit."""
    clock = timing or RetryTiming()
    started = clock.monotonic()
    owned = client is None
    active = httpx.AsyncClient() if client is None else client
    response: httpx.Response | None = None
    try:
        response, media_type = await _with_retries(request, active, clock, buffered)
        elapsed = (clock.monotonic() - started) * 1000
        yield OpenedResponse(
            response,
            media_type,
            setup_latency_ms=None if buffered else elapsed,
            latency_ms=elapsed if buffered else None,
        )
    finally:
        try:
            if response is not None:
                await response.aclose()
        finally:
            if owned:
                await active.aclose()


async def buffered_audio(
    request: PreparedRequest,
    *,
    client: httpx.AsyncClient | None = None,
    timing: RetryTiming | None = None,
) -> AudioData:
    """Body reads/emptiness participate in the same pre-publication retry loop."""
    async with open_response(request, client=client, timing=timing, buffered=True) as opened:
        return AudioData(opened.response.content, opened.media_type)
