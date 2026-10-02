"""Typed audio contracts and the small provider/request seam."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

import httpx

from .pronunciations import SubstitutionResult

__all__ = [
    "AudioData",
    "AudioOutput",
    "PreparedRequest",
    "Provider",
    "RequestDetails",
    "ResolvedModel",
    "ResponseDetails",
    "SpeechMetadata",
    "SpeechResult",
    "SpeechStream",
    "StreamMetadata",
]


@dataclass(frozen=True)
class AudioOutput:
    format: Literal["mp3", "wav", "pcm"] = "mp3"
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        if self.format not in ("mp3", "wav", "pcm"):
            raise ValueError("Unsupported audio format")
        if self.sample_rate is not None:
            if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
                raise TypeError("Sample rate must be an integer")
            if self.sample_rate <= 0:
                raise ValueError("Sample rate must be positive")


@dataclass(frozen=True)
class AudioData:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class SpeechMetadata:
    input_chars: int = 0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class StreamMetadata:
    input_chars: int = 0
    setup_latency_ms: float = 0.0


@dataclass(frozen=True, repr=False)
class RequestDetails:
    """Private SDK-prepared JSON bytes, not caller hook/transport mutations."""

    method: str
    url: str
    content: bytes


@dataclass(frozen=True, repr=False)
class ResponseDetails:
    """Private selected headers from the final accepted response, copied read-only."""

    status_code: int
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True)
class SpeechResult:
    audio: AudioData
    provider: str
    model: str
    metadata: SpeechMetadata = field(default_factory=SpeechMetadata)
    provider_metadata: Mapping[str, object] | None = None
    warnings: tuple[str, ...] = ()
    request: RequestDetails | None = field(default=None, repr=False)
    response: ResponseDetails | None = field(default=None, repr=False)
    pronunciations: SubstitutionResult | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SpeechStream:
    """Single-pass audio iterator, valid only inside its response context."""

    audio: AsyncIterator[bytes]
    media_type: str
    provider: str
    model: str
    metadata: StreamMetadata
    provider_metadata: Mapping[str, object] | None = None
    warnings: tuple[str, ...] = ()
    request: RequestDetails | None = field(default=None, repr=False)
    response: ResponseDetails | None = field(default=None, repr=False)
    pronunciations: SubstitutionResult | None = field(default=None, repr=False)


@dataclass(frozen=True, repr=False)
class PreparedRequest:
    """Validated wire data. Never render the bearer key or the input body."""

    provider: str
    model: str
    url: str
    headers: Mapping[str, str]
    content: bytes
    timeout: httpx.Timeout
    max_retries: int
    media_type: str
    input_chars: int
    pronunciations: SubstitutionResult | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def model(self, model_id: str | None = None) -> "ResolvedModel": ...

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
    ) -> PreparedRequest: ...


@dataclass(frozen=True)
class ResolvedModel:
    provider: Provider = field(repr=False)
    model_id: str
