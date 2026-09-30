"""Local validation, performed before obtaining an HTTP client."""

import json
import math
import os
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import MissingApiKeyError, NoSpeechGeneratedError
from .types import PreparedRequest


def resolve_api_key(
    provider: str, env_var: str, *, explicit: str | None = None, configured: str | None = None
) -> str:
    key = explicit if explicit is not None else configured
    if key is None:
        key = os.environ.get(env_var)
    if not isinstance(key, str) or not key.strip():
        raise MissingApiKeyError(provider, env_var)
    if not key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in key):
        raise ValueError("API key must contain only visible ASCII characters")
    return key


def validate_model_id(value: object) -> str:
    if type(value) is not str:
        raise TypeError("Model identifier must be a string")
    if not value.strip():
        raise ValueError("Model identifier must be nonblank")
    return value


def validate_text_voice(text: object, voice: object, limit: int) -> int:
    if not isinstance(text, str) or not isinstance(voice, str):
        raise TypeError("Text and voice must be strings")
    if not text.strip():
        raise NoSpeechGeneratedError(provider="local", model="input")
    if not voice.strip():
        raise ValueError("Voice must be nonblank")
    if len(text) > limit:
        raise ValueError("Text exceeds model character limit")
    return len(text)


def validate_retries(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_retries must be an integer")
    if value < 0:
        raise ValueError("max_retries must be nonnegative")
    return value


def positive_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Timeout phases must be numeric")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout phases must be finite and positive")
    return float(value)


def validate_timeout(value: object) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return httpx.Timeout(
            connect=positive_seconds(value.connect),
            read=positive_seconds(value.read),
            write=positive_seconds(value.write),
            pool=positive_seconds(value.pool),
        )
    return httpx.Timeout(positive_seconds(value))


def _json_copy(value: object) -> object:
    if isinstance(value, Mapping):
        return copy_options(value)
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("Options must contain only finite JSON values")


def copy_options(value: Mapping[str, object] | None) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("Provider options must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError("JSON object keys must be strings")
    return {key: _json_copy(item) for key, item in value.items()}


def endpoint_url(base_url: str, path: str) -> str:
    if not isinstance(base_url, str) or re.search(r"[\s\x00-\x1f\x7f\\\\?#]", base_url):
        raise ValueError("Base URL must be an HTTP(S) URL without whitespace")
    try:
        url = urlsplit(base_url)
        port = url.port
    except ValueError:
        raise ValueError("Invalid base URL") from None
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("Base URL must have an HTTP(S) scheme and host")
    if url.username is not None or port == 0:
        raise ValueError("Base URL cannot contain credentials or an invalid port")
    _validate_path(path)
    return urlunsplit(
        (url.scheme, url.netloc, url.path.rstrip("/") + "/" + path.lstrip("/"), "", "")
    )


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path:
        raise ValueError("Endpoint must be a nonempty relative path")
    if re.search(r"[\s\x00-\x1f\x7f?#\\\\:]", path):
        raise ValueError("Endpoint must be a relative path")
    if any(part in (".", "..") for part in path.split("/")):
        raise ValueError("Endpoint cannot traverse the base URL prefix")


def request_headers(
    key: str,
    headers: Mapping[str, str] | None,
    auth_header: Literal["authorization", "api-key"] = "authorization",
) -> dict[str, str]:
    merged = {"user-agent": "HYBRD/speech-sdk-python"}
    if headers is not None:
        if not isinstance(headers, Mapping):
            raise TypeError("Headers must be a mapping")
        for name, value in headers.items():
            _validate_header(name, value)
            if name.lower() in ("host", "content-length", "transfer-encoding"):
                raise ValueError("Transport headers cannot be overridden")
            if name.lower().replace("-", "").replace("_", "") not in ("authorization", "apikey"):
                merged[name.lower()] = value
    merged[auth_header] = f"Bearer {key}" if auth_header == "authorization" else key
    merged["content-type"] = "application/json"
    return merged


def _validate_header(name: str, value: str) -> None:
    if not isinstance(name, str) or not isinstance(value, str):
        raise TypeError("Header names and values must be strings")
    if not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name):
        raise ValueError("Invalid header name")
    if not value.isascii() or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("Invalid header value")


def prepare_request(
    *,
    provider: str,
    model: str,
    base_url: str,
    path: str,
    api_key: str,
    body: Mapping[str, object],
    media_type: str,
    input_chars: int,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_retries: int = 2,
    auth_header: Literal["authorization", "api-key"] = "authorization",
) -> PreparedRequest:
    """Provider adapters call this only after validating their native fields."""
    key = resolve_api_key(provider, "configured API key", explicit=api_key)
    return PreparedRequest(
        provider=provider,
        model=model,
        url=endpoint_url(base_url, path),
        headers=request_headers(key, headers, auth_header),
        content=json.dumps(copy_options(body), allow_nan=False).encode("utf-8"),
        timeout=validate_timeout(timeout),
        max_retries=validate_retries(max_retries),
        media_type=media_type,
        input_chars=input_chars,
    )
