"""Independent Python speech SDK: typed audio contracts and safe errors."""

from .errors import MissingApiKeyError, NoSpeechGeneratedError, ProviderError, SpeechSDKError
from .types import (
    AudioData,
    AudioOutput,
    ResolvedModel,
    SpeechMetadata,
    SpeechResult,
    SpeechStream,
    StreamMetadata,
)

__all__ = [
    "AudioData",
    "AudioOutput",
    "MissingApiKeyError",
    "NoSpeechGeneratedError",
    "ProviderError",
    "ResolvedModel",
    "SpeechMetadata",
    "SpeechResult",
    "SpeechSDKError",
    "SpeechStream",
    "StreamMetadata",
]
