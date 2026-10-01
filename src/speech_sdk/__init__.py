"""Independent Python speech SDK: typed audio contracts and safe errors."""

from .api import generate_speech, stream_speech
from .errors import MissingApiKeyError, NoSpeechGeneratedError, ProviderError, SpeechSDKError
from .providers import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_XAI_MODEL,
    OPENAI_MODELS,
    XAI_MODELS,
    AzureOpenAIProvider,
    OpenAIProvider,
    XAIProvider,
)
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
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_XAI_MODEL",
    "OPENAI_MODELS",
    "XAI_MODELS",
    "AudioData",
    "AudioOutput",
    "AzureOpenAIProvider",
    "MissingApiKeyError",
    "NoSpeechGeneratedError",
    "OpenAIProvider",
    "ProviderError",
    "ResolvedModel",
    "SpeechMetadata",
    "SpeechResult",
    "SpeechSDKError",
    "SpeechStream",
    "StreamMetadata",
    "XAIProvider",
    "generate_speech",
    "stream_speech",
]
