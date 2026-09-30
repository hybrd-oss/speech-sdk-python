"""Offline checks for shared contracts and validation boundaries."""

import os
import unittest
from collections.abc import AsyncIterator, Mapping
from typing import cast
from unittest.mock import patch

import httpx

from speech_sdk import (
    AudioData,
    AudioOutput,
    MissingApiKeyError,
    NoSpeechGeneratedError,
    ProviderError,
    ResolvedModel,
    SpeechMetadata,
    SpeechResult,
    SpeechStream,
    StreamMetadata,
)
from speech_sdk._validation import (
    copy_options,
    prepare_request,
    resolve_api_key,
    validate_retries,
    validate_text_voice,
    validate_timeout,
)
from speech_sdk.types import PreparedRequest, Provider


class FixtureProvider:
    """Exercise the typed provider seam without shipping a provider adapter."""

    name = "fixture"

    def model(self, model_id: str | None = None) -> ResolvedModel:
        return ResolvedModel(self, model_id or "fixture-model")

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
        body = copy_options(provider_options)
        body.update({"text": text, "voice": voice, "instructions": instructions})
        return prepare_request(
            provider=self.name,
            model=model_id,
            base_url="https://fixture.example/v1",
            path="tts",
            api_key=api_key or "fixture-key",
            body=body,
            media_type="audio/wav" if output and output.format == "wav" else "audio/mpeg",
            input_chars=len(text),
            timeout=timeout,
            max_retries=max_retries,
            headers=headers,
        )


class CoreTests(unittest.TestCase):
    def test_provider_seam(self) -> None:
        provider: Provider = FixtureProvider()
        resolved = provider.model()
        request = resolved.provider.prepare(
            model_id=resolved.model_id,
            text="hi",
            voice="test",
            output=None,
            instructions="style",
            provider_options={"speed": 1},
            api_key="fixture-key",
            timeout=60,
            max_retries=2,
            headers=None,
        )
        self.assertEqual(request.model, "fixture-model")
        self.assertNotIn("fixture-key", repr(request))
        self.assertNotIn("fixture-key", repr(resolved))

    def test_stream_metadata(self) -> None:
        async def audio() -> AsyncIterator[bytes]:
            yield b"test"

        stream = SpeechStream(
            audio(), "audio/mpeg", "fixture", "fixture-model", StreamMetadata(2, 5)
        )
        self.assertEqual(stream.metadata.setup_latency_ms, 5)
        self.assertEqual(stream.warnings, ())

    def test_dataclasses(self) -> None:
        output = AudioOutput(format="pcm", sample_rate=24000)
        self.assertEqual(output.sample_rate, 24000)
        result = SpeechResult(AudioData(b"audio", "audio/mpeg"), "xai", "grok-tts")
        self.assertEqual(result.audio.data, b"audio")
        self.assertEqual(result.warnings, ())
        self.assertIsNone(result.provider_metadata)
        self.assertEqual(result.metadata.latency_ms, 0)
        self.assertEqual(SpeechMetadata(input_chars=3).input_chars, 3)

    def test_output_validation(self) -> None:
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                AudioOutput(sample_rate=cast(int, value))

    def test_key_precedence_and_secrecy(self) -> None:
        with patch.dict(os.environ, {"TEST_SPEECH_KEY": "environment"}):
            self.assertEqual(resolve_api_key("xai", "TEST_SPEECH_KEY"), "environment")
            self.assertEqual(
                resolve_api_key("xai", "TEST_SPEECH_KEY", configured="configured"), "configured"
            )
            self.assertEqual(
                resolve_api_key("xai", "TEST_SPEECH_KEY", explicit="explicit", configured="config"),
                "explicit",
            )
            with self.assertRaises(MissingApiKeyError) as caught:
                resolve_api_key("xai", "TEST_SPEECH_KEY", explicit=" ")
        self.assertEqual(caught.exception.env_var, "TEST_SPEECH_KEY")
        self.assertNotIn("explicit", repr(caught.exception))

    def test_errors_do_not_render_untrusted_details(self) -> None:
        error = ProviderError(
            provider="xai",
            model="grok-tts",
            status_code=429,
            code="echo-secret",
            request_id="echo-secret",
            details={"message": "echo-secret"},
            raw_response="echo-secret",
            retryable=True,
        )
        self.assertNotIn("echo-secret", str(error))
        self.assertNotIn("echo-secret", repr(error))
        self.assertEqual(error.details, {"message": "echo-secret"})
        self.assertTrue(error.retryable)
        self.assertEqual(error.stage, "synthesis")

    def test_strict_json_copy(self) -> None:
        original: dict[str, object] = {"nested": {"values": [1, "ok", None]}}
        copied = copy_options(original)
        self.assertEqual(copied, original)
        self.assertIsNot(copied["nested"], original["nested"])
        for value in (float("nan"), float("inf"), object(), {1: "bad"}):
            with self.subTest(value=type(value)), self.assertRaises((ValueError, TypeError)):
                copy_options({"bad": value})

    def test_text_is_verbatim(self) -> None:
        self.assertEqual(validate_text_voice(" 😀 ", " eve ", 3), 3)
        for text in ("", " \t", "abcd"):
            with self.subTest(text=text), self.assertRaises((ValueError, NoSpeechGeneratedError)):
                validate_text_voice(text, "eve", 3)
        with self.assertRaises(ValueError):
            validate_text_voice("hi", " ", 3)

    def test_timeout_and_retry_boundaries(self) -> None:
        self.assertEqual(validate_timeout(60).read, 60)
        self.assertEqual(validate_timeout(httpx.Timeout(3)).pool, 3)
        for value in (True, 0, -1, float("nan"), float("inf"), httpx.Timeout(None)):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                validate_timeout(value)
        for value in (True, -1, 0.5):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                validate_retries(value)
        self.assertEqual(validate_retries(0), 0)
        self.assertEqual(validate_retries(2), 2)
