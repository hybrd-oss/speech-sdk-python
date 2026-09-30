"""Azure OpenAI v1 JSON speech, with deployment selectors and API-key auth."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode, urlsplit

import httpx

from .._validation import endpoint_url, prepare_request, resolve_api_key, validate_model_id
from ..types import AudioOutput, PreparedRequest, ResolvedModel
from .openai import _speech_body

__all__ = ["AzureOpenAIProvider"]


@dataclass(frozen=True)
class AzureOpenAIProvider:
    """Configuration only; custom destinations intentionally receive the API key."""

    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = field(default=None, repr=False)
    api_version: str = field(default="preview", repr=False)
    name: str = field(default="azure", init=False)

    def __post_init__(self) -> None:
        base = self.base_url
        if base is None:
            base = os.environ.get("AZURE_OPENAI_BASE_URL")
        if base is None:
            base = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if base is None:
            raise ValueError("Azure requires AZURE_OPENAI_BASE_URL or AZURE_OPENAI_ENDPOINT")
        endpoint_url(base, "audio/speech")
        if type(self.api_version) is not str:
            raise TypeError("API version must be a string")
        if not self.api_version.strip():
            raise ValueError("API version must be nonblank")
        object.__setattr__(self, "base_url", base)

    def model(self, model_id: str | None = None) -> ResolvedModel:
        selected = (
            model_id if model_id is not None else os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
        )
        if selected is None:
            raise ValueError(
                "Azure requires AZURE_OPENAI_DEPLOYMENT_NAME or an explicit deployment"
            )
        return ResolvedModel(self, validate_model_id(selected))

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
        body, media_type, input_chars = _speech_body(
            model=model,
            text=text,
            voice=voice,
            output=output,
            instructions=instructions,
            provider_options=provider_options,
            deployment=True,
        )
        key = resolve_api_key(
            self.name, "AZURE_OPENAI_API_KEY", explicit=api_key, configured=self.api_key
        )
        base = self.base_url
        if base is None:
            raise ValueError("Azure requires a base URL")
        path = (
            "audio/speech"
            if urlsplit(base).path.rstrip("/").endswith("/openai/v1")
            else "openai/v1/audio/speech"
        )
        request = prepare_request(
            provider=self.name,
            model=model,
            base_url=base,
            path=path,
            api_key=key,
            body=body,
            media_type=media_type,
            input_chars=input_chars,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
            auth_header="api-key",
        )
        return replace(
            request, url=request.url + "?" + urlencode({"api-version": self.api_version})
        )
