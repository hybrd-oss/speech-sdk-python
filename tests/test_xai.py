"""Offline xAI wire contract and shared HTTP integration checks."""

import copy
import json
import os
import unittest
from collections.abc import Mapping
from typing import Literal, cast
from unittest.mock import patch

import httpx

from speech_sdk._http import RetryTiming, buffered_audio, open_response
from speech_sdk.errors import MissingApiKeyError, NoSpeechGeneratedError, ProviderError
from speech_sdk.providers.xai import XAIProvider
from speech_sdk.types import AudioOutput, PreparedRequest, Provider

RATES = (8000, 16000, 22050, 24000, 44100, 48000)
BIT_RATES = (32000, 64000, 96000, 128000, 192000)
CODECS = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "mulaw": "audio/basic",
    "alaw": "audio/alaw",
}


def prepared(
    *,
    provider: Provider | None = None,
    model_id: str = "grok-tts",
    text: str = "Hello!",
    voice: str = "eve",
    output: AudioOutput | None = None,
    instructions: str | None = None,
    options: Mapping[str, object] | None = None,
    api_key: str | None = None,
    timeout: float | httpx.Timeout = 60,
    max_retries: int = 2,
    headers: Mapping[str, str] | None = None,
) -> PreparedRequest:
    active = provider if provider is not None else XAIProvider(api_key="fixture-key")
    return active.prepare(
        model_id=model_id,
        text=text,
        voice=voice,
        output=output,
        instructions=instructions,
        provider_options=options,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        headers=headers,
    )


def body(request: PreparedRequest) -> dict[str, object]:
    return cast(dict[str, object], json.loads(request.content))


class XAIContractTests(unittest.TestCase):
    def test_default_exact_request_and_model(self) -> None:
        value: object
        provider: Provider = XAIProvider(api_key="fixture-key")
        default = provider.model()
        self.assertIs(default.provider, provider)
        self.assertEqual(default.model_id, "grok-tts")
        self.assertEqual(provider.model("grok-tts"), default)
        self.assertEqual(provider.name, "xai")
        request = prepared(provider=provider)
        self.assertEqual(request.url, "https://api.x.ai/v1/tts")
        self.assertEqual(body(request), {"text": "Hello!", "voice_id": "eve", "language": "auto"})
        self.assertEqual(
            dict(request.headers),
            {
                "authorization": "Bearer fixture-key",
                "content-type": "application/json",
                "user-agent": "HYBRD/speech-sdk-python",
            },
        )
        self.assertEqual(
            (request.provider, request.model, request.media_type, request.input_chars),
            ("xai", "grok-tts", "audio/mpeg", 6),
        )
        self.assertEqual(
            request.timeout.as_dict(), dict.fromkeys(("connect", "read", "write", "pool"), 60)
        )
        self.assertEqual(request.max_retries, 2)
        for value in ("", " \n\t", 1, True, [], {}):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                provider.model(cast(str, value))
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                prepared(model_id=cast(str, value))

    def test_model_subclasses_rejected_before_client(self) -> None:
        class Overridden(str):
            def strip(self, _chars: str | None = None, /) -> str:
                return "nonblank"

            def partition(self, _sep: str, /) -> tuple[str, str, str]:
                return ("xai", "/", "rewritten")

        with patch("speech_sdk._http.httpx.AsyncClient") as factory:
            for model in (
                Overridden(" \n\t"),
                Overridden("unknown/original"),
                Overridden("future"),
            ):
                with self.subTest(model=model, entry="model"), self.assertRaises(TypeError):
                    XAIProvider().model(model)
                with self.subTest(model=model, entry="prepare"), self.assertRaises(TypeError):
                    prepared(model_id=model)
            factory.assert_not_called()

    def test_generic_models_are_metadata_only(self) -> None:
        for model in (
            "unknown",
            "xai/grok-tts",
            "grok-tts-2026-10-01",
            "org/namespace/future-tts",
            " future\nmodel ",
            "/",
            " \t/ \n",
        ):
            with self.subTest(model=model):
                self.assertEqual(XAIProvider().model(model).model_id, model)
                request = prepared(model_id=model, options={"speed": 1.0})
                self.assertEqual(request.model, model)
                self.assertEqual(
                    body(request),
                    {"text": "Hello!", "voice_id": "eve", "language": "auto", "speed": 1.0},
                )
                with self.assertRaises(ValueError):
                    prepared(model_id=model, options={"model": model})
        with self.assertRaises(TypeError):
            prepared(model_id=cast(str, None))

    def test_verbatim_canonical_fields_and_immutable_options(self) -> None:
        options: dict[str, object] = {
            "text": "wrong",
            "voice_id": "wrong",
            "language": " pt-BR ",
            "future": {"items": [1, None]},
            "output_format": {
                "codec": "mp3",
                "sample_rate": 16000,
                "bit_rate": 64000,
                "future": ["ok"],
            },
        }
        original = copy.deepcopy(options)
        text = " 😀 [pause]<whisper>secret</whisper> "
        request = prepared(
            text=text, voice=" EVE ", options=options, output=AudioOutput(sample_rate=44100)
        )
        expected = copy.deepcopy(original)
        expected.update(text=text, voice_id=" EVE ")
        expected["output_format"] = {
            "codec": "mp3",
            "sample_rate": 44100,
            "bit_rate": 64000,
            "future": ["ok"],
        }
        self.assertEqual(body(request), expected)
        self.assertEqual(options, original)
        self.assertEqual(request.input_chars, len(text))
        cast(list[object], cast(dict[str, object], options["future"])["items"]).append("later")
        self.assertEqual(body(request), expected)

    def test_unicode_limits_and_invalid_inputs(self) -> None:
        for length in (15001, 60000):
            request = prepared(text="😀" * length)
            self.assertEqual(request.input_chars, length)
            self.assertEqual(body(request)["text"], "😀" * length)
        with self.assertRaises(ValueError):
            prepared(text="😀" * 60001)
        invalid_inputs: tuple[object, ...] = (None, True, 1, [], {})
        for value in invalid_inputs:
            with self.subTest(value=value), self.assertRaises(TypeError):
                prepared(text=cast(str, value))
            with self.subTest(value=value), self.assertRaises(TypeError):
                prepared(voice=cast(str, value))
        for blank in ("", " \t"):
            with self.subTest(value=blank), self.assertRaises(NoSpeechGeneratedError):
                prepared(text=blank)
            with self.subTest(value=blank), self.assertRaises(ValueError):
                prepared(voice=blank)

    def test_all_native_codecs_rates_and_mp3_bit_rates(self) -> None:
        for codec, mime in CODECS.items():
            for rate in RATES:
                native = {"codec": codec, "sample_rate": rate}
                with self.subTest(codec=codec, rate=rate):
                    request = prepared(options={"output_format": native})
                    self.assertEqual(body(request)["output_format"], native)
                    self.assertEqual(
                        request.media_type, f"{mime};rate={rate}" if codec == "pcm" else mime
                    )
            request = prepared(options={"output_format": {"codec": codec}})
            expected: dict[str, object] = {"codec": codec}
            if codec == "pcm":
                expected["sample_rate"] = 24000
            self.assertEqual(body(request)["output_format"], expected)
            self.assertEqual(request.media_type, "audio/pcm;rate=24000" if codec == "pcm" else mime)
        for rate in BIT_RATES:
            request = prepared(options={"output_format": {"bit_rate": rate}})
            self.assertEqual(body(request)["output_format"], {"bit_rate": rate})
            self.assertEqual(request.media_type, "audio/mpeg")
        self.assertEqual(body(prepared(options={"output_format": {}}))["output_format"], {})

    def test_explicit_output_precedence_and_defaults(self) -> None:
        for codec in ("mp3", "wav", "pcm"):
            for rate in RATES:
                native = {"codec": "alaw", "sample_rate": 8000, "future": {"ok": True}}
                request = prepared(
                    output=AudioOutput(format=codec, sample_rate=rate),
                    options={"output_format": native},
                )
                self.assertEqual(
                    body(request)["output_format"],
                    {"codec": codec, "sample_rate": rate, "future": {"ok": True}},
                )
            request = prepared(
                output=AudioOutput(format=codec),
                options={"output_format": {"codec": "wav", "sample_rate": 48000}},
            )
            expected: dict[str, object] = {"codec": codec}
            if codec in ("wav", "pcm"):
                expected["sample_rate"] = 24000
            self.assertEqual(body(request)["output_format"], expected)
        for codec in ("wav", "pcm"):
            with self.subTest(codec=codec), self.assertRaises(ValueError):
                prepared(
                    output=AudioOutput(format=cast(Literal["wav", "pcm"], codec)),
                    options={"output_format": {"codec": "mp3", "bit_rate": 128000}},
                )
        request = prepared(
            output=AudioOutput(), options={"output_format": {"codec": "mp3", "bit_rate": 192000}}
        )
        self.assertEqual(body(request)["output_format"], {"codec": "mp3", "bit_rate": 192000})
        with self.assertRaises(ValueError):
            prepared(output=AudioOutput(sample_rate=12000))
        with self.assertRaises(TypeError):
            prepared(output=cast(AudioOutput, {}))

    def test_explicit_output_without_native_options(self) -> None:
        for output in (AudioOutput(format="wav"), AudioOutput(format="pcm")):
            request = prepared(output=output)
            self.assertEqual(
                body(request)["output_format"], {"codec": output.format, "sample_rate": 24000}
            )
        self.assertEqual(body(prepared(output=AudioOutput()))["output_format"], {"codec": "mp3"})
        options = {
            "speed": 1.0,
            "optimize_streaming_latency": 0,
            "text_normalization": False,
            "with_timestamps": False,
        }
        self.assertEqual(
            body(prepared(options=options)),
            {**options, "text": "Hello!", "voice_id": "eve", "language": "auto"},
        )

    def test_native_output_validation_even_with_explicit_override(self) -> None:
        invalid: list[object] = [None, "mp3", [], True, 1]
        invalid.extend(
            {"codec": value}
            for value in cast(tuple[object, ...], (None, True, [], {}, "MP3", "ulaw", "ogg", ""))
        )
        invalid.extend(
            {"sample_rate": value} for value in (None, True, False, 24000.0, "24000", 0, -1, 12000)
        )
        invalid.extend(
            {"bit_rate": value} for value in (None, True, False, 128000.0, "128000", 0, 256000)
        )
        invalid.extend(
            {"codec": codec, "bit_rate": 128000} for codec in ("wav", "pcm", "mulaw", "alaw")
        )
        for native in invalid:
            for output in (None, AudioOutput()):
                with (
                    self.subTest(native=native, output=output),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    prepared(options={"output_format": native}, output=output)

    def test_valid_options_and_instruction_omission(self) -> None:
        for speed in (0.7, 1, 1.5):
            for latency in (0, 1):
                options = {
                    "speed": speed,
                    "optimize_streaming_latency": latency,
                    "text_normalization": True,
                    "with_timestamps": False,
                    "language": "zz-ZZ",
                    "future": [True, None],
                }
                self.assertEqual(
                    body(prepared(options=options)),
                    {**options, "text": "Hello!", "voice_id": "eve"},
                )
        for value in (None, "", " \t"):
            request = prepared(instructions=value, options={"instructions": value})
            self.assertNotIn("instructions", body(request))

    def test_bad_options_and_protected_protocol(self) -> None:
        invalid: list[dict[str, object]] = []
        invalid.extend(
            {"speed": value}
            for value in (True, False, None, "1", 0.69, 1.51, float("nan"), float("inf"))
        )
        invalid.extend(
            {"optimize_streaming_latency": value} for value in (True, False, None, 0.0, -1, 2, "1")
        )
        invalid.extend(
            {field: value}
            for field in ("with_timestamps", "text_normalization")
            for value in cast(tuple[object, ...], (None, 0, 1, "false", []))
        )
        invalid.extend(
            {"language": value} for value in cast(tuple[object, ...], (None, True, 1, [], "", " "))
        )
        invalid.extend(
            {"instructions": value} for value in cast(tuple[object, ...], ("style", 1, True, []))
        )
        invalid.extend(
            {field: value}
            for field in ("model", "replace")
            for value in cast(tuple[object, ...], (None, {}, "grok-tts"))
        )
        invalid.append({"with_timestamps": True})
        for options in invalid:
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                prepared(options=options)

    def test_protected_protocol_and_non_json_options(self) -> None:
        invalid: list[dict[str, object]] = []
        invalid.extend(
            {field: "sse"}
            for field in (
                "response_format",
                "stream_format",
                "stream",
                "authorization",
                "headers",
                "api_key",
                "base_url",
                "url",
                "method",
            )
        )
        invalid.extend({"future": value} for value in (object(), float("nan"), {1: "bad"}))
        for options in invalid:
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                prepared(options=options)
        for value in cast(tuple[object, ...], ("style", 1, True, [])):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                prepared(instructions=cast(str, value))
        with self.assertRaises(TypeError):
            prepared(options=cast(Mapping[str, object], []))

    def test_key_configuration_custom_base_and_shared_validation(self) -> None:
        provider = XAIProvider(api_key="configured", base_url="https://proxy.example/prefix/v1///")
        self.assertNotIn("configured", repr(provider))
        self.assertNotIn("configured", repr(provider.model()))
        with patch.dict(os.environ, {"XAI_API_KEY": "environment"}, clear=True):
            self.assertEqual(
                prepared(provider=XAIProvider()).headers["authorization"], "Bearer environment"
            )
            self.assertEqual(
                prepared(provider=provider).headers["authorization"], "Bearer configured"
            )
            request = prepared(
                provider=provider,
                api_key="explicit",
                headers={"Authorization": "wrong", "Content-Type": "wrong", "X-Custom": "test"},
                timeout=httpx.Timeout(5),
                max_retries=0,
            )
            self.assertEqual(request.headers["authorization"], "Bearer explicit")
            self.assertEqual(request.headers["content-type"], "application/json")
            self.assertEqual(request.headers["x-custom"], "test")
            self.assertEqual(request.url, "https://proxy.example/prefix/v1/tts")
            self.assertEqual(request.timeout.read, 5)
            self.assertEqual(request.max_retries, 0)
            self.assertNotIn("explicit", repr(request))
            with self.assertRaises(MissingApiKeyError):
                prepared(provider=provider, api_key=" ")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(MissingApiKeyError):
            prepared(provider=XAIProvider())
        for url in (
            "not-url",
            "ftp://host",
            "https://user:pass@host",
            "https://host?key=value",
            "https://host#fragment",
            "https://host/ bad",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                XAIProvider(base_url=url)
        for value in (True, -1, 0.5):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                prepared(max_retries=cast(int, value))
        for value in (True, 0, float("inf")):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                prepared(timeout=cast(float, value))
        with self.assertRaises(ValueError):
            prepared(headers={"HOST": "wrong"})


class XAIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_post_and_bytes_through_shared_seam(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200, content=b"unchanged-audio", headers={"content-type": "audio/x-wav"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = prepared(output=AudioOutput(format="wav"))
            audio = await buffered_audio(request, client=client)
            self.assertEqual(audio.data, b"unchanged-audio")
            self.assertEqual(audio.media_type, "audio/wav")
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(str(requests[0].url), request.url)
        self.assertEqual(requests[0].content, request.content)
        self.assertEqual(requests[0].headers["authorization"], "Bearer fixture-key")

    async def test_local_invalid_options_make_zero_requests(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"audio")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            invalid_options: tuple[dict[str, object], ...] = (
                {"with_timestamps": True},
                {"model": "grok-tts"},
                {"replace": {}},
                {"instructions": "style"},
                {"output_format": {"codec": "ogg"}},
            )
            for options in invalid_options:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    await buffered_audio(prepared(options=options), client=client)
        self.assertEqual(requests, [])

    async def test_status_retry_and_contract_integration(self) -> None:
        for status in (400, 401, 404, 429, 500, 503):
            calls: list[int] = []
            delays: list[float] = []

            def handler(
                request: httpx.Request, *, calls: list[int] = calls, status: int = status
            ) -> httpx.Response:
                calls.append(request.method == "POST")
                return (
                    httpx.Response(
                        status, json={"error": {"message": "unknown voice", "code": "unknown"}}
                    )
                    if len(calls) == 1
                    else httpx.Response(200, content=b"audio")
                )

            async def sleep(delay: float, *, delays: list[float] = delays) -> None:
                delays.append(delay)

            timing = RetryTiming(random=lambda: 0, sleep=sleep)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                if status in (429, 500, 503):
                    self.assertEqual(
                        (await buffered_audio(prepared(), client=client, timing=timing)).data,
                        b"audio",
                    )
                    self.assertEqual((len(calls), delays), (2, [1.0]))
                else:
                    with self.assertRaises(ProviderError) as caught:
                        await buffered_audio(prepared(), client=client, timing=timing)
                    self.assertEqual(caught.exception.status_code, status)
                    self.assertFalse(caught.exception.retryable)
                    self.assertEqual((len(calls), delays), (1, []))

    async def test_invalid_response_mime(self) -> None:
        for mime in (
            "application/json",
            "text/html",
            "text/event-stream",
            "audio/wav",
            "audio/pcm;rate=16000",
        ):

            def handler(request: httpx.Request, *, mime: str = mime) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                return httpx.Response(200, content=b"not-mp3", headers={"content-type": mime})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.subTest(mime=mime), self.assertRaises(ProviderError):
                    async with open_response(prepared(), client=client):
                        self.fail("invalid MIME published")

    async def test_pcm_mime_rate_is_validated_not_guessed(self) -> None:
        for mime in (
            "",
            "application/octet-stream",
            "audio/pcm",
            "audio/pcm;rate=24000",
            "audio/pcm;rate=16000",
        ):

            def handler(request: httpx.Request, *, mime: str = mime) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                return httpx.Response(200, content=b"\\x00\\x01", headers={"content-type": mime})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                request = prepared(output=AudioOutput(format="pcm"))
                if mime.endswith("16000"):
                    with self.assertRaises(ProviderError):
                        await buffered_audio(request, client=client)
                else:
                    audio = await buffered_audio(request, client=client)
                    self.assertEqual(audio.media_type, "audio/pcm;rate=24000")

    async def test_empty_buffered_retry_and_pcm_rate(self) -> None:
        calls: list[httpx.Request] = []

        async def sleep(delay: float) -> None:
            self.assertGreaterEqual(delay, 1)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                content=b"" if len(calls) == 1 else b"\x00\x01",
                headers={"content-type": "audio/pcm"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            audio = await buffered_audio(
                prepared(options={"output_format": {"codec": "pcm"}}),
                client=client,
                timing=RetryTiming(random=lambda: 0, sleep=sleep),
            )
            self.assertEqual((audio.data, audio.media_type), (b"\x00\x01", "audio/pcm;rate=24000"))
        self.assertEqual(len(calls), 2)
