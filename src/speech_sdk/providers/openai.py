"""OpenAI speech request construction; HTTP ownership lives in the shared core."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from .._validation import (
    copy_options,
    endpoint_url,
    prepare_request,
    resolve_api_key,
    validate_text_voice,
)
from ..types import AudioOutput, PreparedRequest, ResolvedModel

__all__ = ["OpenAIProvider"]

_MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")
_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm;rate=24000",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
_NATIVE_FIELDS = {
    "model",
    "input",
    "voice",
    "instructions",
    "response_format",
    "speed",
    "stream_format",
    "sample_rate",
}
_PROTECTED_FIELDS = {
    "text",
    "stream",
    "response_mode",
    "output_format",
    "output",
    "audio",
    "format",
    "headers",
    "authorization",
    "api_key",
    "base_url",
    "url",
    "method",
    "body",
    "content_type",
    "accept",
    "host",
    "content_length",
    "transfer_encoding",
}


def _validate_option_names(options: Mapping[str, object]) -> None:
    matching = {name.replace("_", "") for name in _NATIVE_FIELDS | _PROTECTED_FIELDS}
    for name in options:
        normalized = name.strip().lower().replace("-", "").replace("_", "")
        if normalized in matching and name not in _NATIVE_FIELDS:
            raise ValueError("Native options cannot override protected or protocol fields")


def _validate_speed(options: Mapping[str, object]) -> None:
    if "speed" not in options:
        return
    speed = options["speed"]
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise TypeError("Speed must be numeric")
    if not 0.25 <= speed <= 4.0 or not math.isfinite(speed):
        raise ValueError("Speed must be finite and between 0.25 and 4.0")


def _instructions(canonical: str | None, options: dict[str, object], model: str) -> None:
    values: list[str] = []
    if canonical is not None:
        if not isinstance(canonical, str):
            raise TypeError("Instructions must be strings")
        values.append(canonical)
    if "instructions" in options:
        native = options.pop("instructions")
        if not isinstance(native, str):
            raise TypeError("Native instructions must be strings")
        values.append(native)
    combined = "\n\n".join(value for value in values if value.strip())
    if len(combined) > 4096:
        raise ValueError("Instructions exceed 4096 characters")
    if combined:
        if model != "gpt-4o-mini-tts":
            raise ValueError("This model does not support instructions")
        options["instructions"] = combined


def _validate_rate(rate: object) -> None:
    if rate is None:
        return
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise TypeError("Sample rate must be an integer")
    if rate != 24000:
        raise ValueError("OpenAI supports only 24000 Hz; no local resampling")


def _output(options: dict[str, object], output: AudioOutput | None) -> str:
    native = options.get("response_format", "mp3")
    if not isinstance(native, str) or native not in _MEDIA_TYPES:
        raise ValueError("Unsupported native response format")
    _validate_rate(options.pop("sample_rate", None))
    if output is not None:
        if not isinstance(output, AudioOutput):
            raise TypeError("Output must be AudioOutput")
        _validate_rate(output.sample_rate)
        options["response_format"] = output.format
        native = output.format
    return _MEDIA_TYPES[native]


@dataclass(frozen=True)
class OpenAIProvider:
    """Configuration only; custom base URLs intentionally receive the bearer key."""

    api_key: str | None = field(default=None, repr=False)
    base_url: str = "https://api.openai.com/v1"
    name: str = field(default="openai", init=False)

    def __post_init__(self) -> None:
        endpoint_url(self.base_url, "audio/speech")

    def model(self, model_id: str | None = None) -> ResolvedModel:
        selected = _MODELS[0] if model_id is None else model_id
        if not isinstance(selected, str) or selected not in _MODELS:
            raise ValueError("Unknown OpenAI speech model")
        return ResolvedModel(provider=self, model_id=selected)

    def prepare(
        self,
        *,
        model_id: str,
        text: str,
        voice: str,
        output: AudioOutput | None,
        instructions: str | None,
        provider_options: Mapping[str, object] | None,
        api_key: str | None,
        timeout: float | httpx.Timeout,
        max_retries: int,
        headers: Mapping[str, str] | None,
    ) -> PreparedRequest:
        model = self.model(model_id).model_id
        input_chars = validate_text_voice(text, voice, 4096)
        body = copy_options(provider_options)
        _validate_option_names(body)
        _validate_speed(body)
        if "stream_format" in body and body["stream_format"] != "audio":
            raise ValueError("Only binary audio stream_format is supported")
        _instructions(instructions, body, model)
        media_type = _output(body, output)
        body.update(model=model, input=text, voice=voice, stream_format="audio")
        key = resolve_api_key("openai", "OPENAI_API_KEY", explicit=api_key, configured=self.api_key)
        return prepare_request(
            provider=self.name,
            model=model,
            base_url=self.base_url,
            path="audio/speech",
            api_key=key,
            body=body,
            media_type=media_type,
            input_chars=input_chars,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
        )
