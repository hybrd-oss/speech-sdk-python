"""xAI REST TTS request adapter; HTTP ownership stays in the shared seam."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

from .._validation import (
    copy_options,
    endpoint_url,
    prepare_request,
    resolve_api_key,
    validate_model_id,
    validate_text_voice,
)
from ..types import AudioOutput, PreparedRequest, ResolvedModel

__all__ = ["DEFAULT_XAI_MODEL", "XAI_MODELS", "XAIProvider"]

XAI_MODELS = ("grok-tts",)
DEFAULT_XAI_MODEL = XAI_MODELS[0]
_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
_BIT_RATES = (32000, 64000, 96000, 128000, 192000)
_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "mulaw": "audio/basic",
    "alaw": "audio/alaw",
}
_PROTECTED = {
    "model",
    "replace",
    "response_format",
    "stream_format",
    "stream",
    "authorization",
    "headers",
    "api_key",
    "base_url",
    "url",
    "method",
}


def _integer_option(value: object, allowed: tuple[int, ...], name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value not in allowed:
        raise ValueError(f"Unsupported {name}")


def _instructions(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError("Instructions must be a string")
    if value.strip():
        raise ValueError("xAI TTS does not support instructions")


def _validate_options(body: dict[str, object]) -> None:
    if _PROTECTED.intersection(body):
        raise ValueError("Unsupported or protected xAI TTS option")
    _instructions(body.pop("instructions", None))
    language = body.setdefault("language", "auto")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("Language must be a nonblank string")
    for name in ("text_normalization", "with_timestamps"):
        if name in body and not isinstance(body[name], bool):
            raise TypeError(f"{name} must be a boolean")
    if body.get("with_timestamps") is True:
        raise ValueError("Timestamp envelopes are not raw audio")
    if "optimize_streaming_latency" in body:
        _integer_option(body["optimize_streaming_latency"], (0, 1), "optimize_streaming_latency")
    if "speed" in body:
        _speed(body["speed"])


def _speed(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Speed must be numeric")
    if not 0.7 <= value <= 1.5 or not math.isfinite(value):
        raise ValueError("Speed must be finite and between 0.7 and 1.5")


def _validate_format(native: dict[str, object]) -> str:
    codec = native.get("codec", "mp3")
    if not isinstance(codec, str) or codec not in _MEDIA_TYPES:
        raise ValueError("Unsupported xAI audio codec")
    if "sample_rate" in native:
        _integer_option(native["sample_rate"], _RATES, "sample_rate")
    if "bit_rate" in native:
        _integer_option(native["bit_rate"], _BIT_RATES, "bit_rate")
        if codec != "mp3":
            raise ValueError("bit_rate only applies to MP3")
    return codec


def _native_format(body: dict[str, object]) -> dict[str, object]:
    value = body.get("output_format", {})
    if not isinstance(value, Mapping):
        raise TypeError("output_format must be an object")
    return copy_options(value)


def _output(body: dict[str, object], output: AudioOutput | None) -> str:
    native = _native_format(body)
    _validate_format(native)  # Validate native values even when canonical output wins.
    if output is not None:
        if not isinstance(output, AudioOutput):
            raise TypeError("Output must be AudioOutput")
        native["codec"] = output.format
        native.pop("sample_rate", None)
        if output.sample_rate is not None:
            native["sample_rate"] = output.sample_rate
        elif output.format in ("wav", "pcm"):
            native["sample_rate"] = 24000
    codec = _validate_format(native)
    if codec == "pcm":
        # Documented default, sent explicitly so raw PCM MIME never guesses a rate.
        native.setdefault("sample_rate", 24000)
    if output is not None or "output_format" in body:
        body["output_format"] = native
    mime = _MEDIA_TYPES[codec]
    return f"{mime};rate={native['sample_rate']}" if codec == "pcm" else mime


@dataclass(frozen=True)
class XAIProvider:
    """Configuration only; custom base URLs are intentional credential destinations."""

    api_key: str | None = field(default=None, repr=False)
    base_url: str = "https://api.x.ai/v1"
    name: ClassVar[str] = "xai"

    def __post_init__(self) -> None:
        endpoint_url(self.base_url, "tts")

    def model(self, model_id: str | None = None) -> ResolvedModel:
        """The identifier is metadata only; REST TTS has no model selector."""
        selected = validate_model_id(DEFAULT_XAI_MODEL if model_id is None else model_id)
        return ResolvedModel(self, selected)

    def prepare(
        self,
        *,
        model_id: str,
        text: str,
        voice: str,
        output: AudioOutput | None = None,
        instructions: str | None = None,
        provider_options: Mapping[str, object] | None = None,
        api_key: str | None = None,
        timeout: float | httpx.Timeout = 60.0,
        max_retries: int = 2,
        headers: Mapping[str, str] | None = None,
    ) -> PreparedRequest:
        model = validate_model_id(model_id)
        input_chars = validate_text_voice(text, voice, 60000)
        _instructions(instructions)
        body = copy_options(provider_options)
        _validate_options(body)
        media_type = _output(body, output)
        body.update(text=text, voice_id=voice)
        key = resolve_api_key(self.name, "XAI_API_KEY", explicit=api_key, configured=self.api_key)
        return prepare_request(
            provider=self.name,
            model=model,
            base_url=self.base_url,
            path="tts",
            api_key=key,
            body=body,
            media_type=media_type,
            input_chars=input_chars,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
        )
