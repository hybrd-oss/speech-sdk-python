"""Context-rich errors; untrusted response fields are explicit attributes only."""

__all__ = ["MissingApiKeyError", "NoSpeechGeneratedError", "ProviderError", "SpeechSDKError"]


class SpeechSDKError(Exception):
    """Base error; the SDK never logs requests or provider responses."""


class MissingApiKeyError(SpeechSDKError):
    def __init__(self, provider: str, env_var: str) -> None:
        self.provider = provider
        self.env_var = env_var
        super().__init__(f"Missing API key for {provider}; configure {env_var}")


class ProviderError(SpeechSDKError):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        details: object = None,
        raw_response: str | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.details = details
        self.raw_response = raw_response
        self.retryable = retryable
        self.retry_after = retry_after
        self.stage = "synthesis"
        # Codes and IDs can echo secrets too: only expose their presence automatically.
        super().__init__(
            f"Speech synthesis failed ({provider}/{model}, status={status_code}, "
            f"code={'present' if code else 'absent'}, "
            f"request_id={'present' if request_id else 'absent'})"
        )


class NoSpeechGeneratedError(ProviderError):
    """Empty input or body; buffered emptiness can be retried before publication."""
