"""Offline public request, validation and full-call timing contracts."""

import inspect
import json
import os
import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import ExitStack
from typing import cast
from unittest.mock import patch

import httpx

from speech_sdk import (
    AudioOutput,
    MissingApiKeyError,
    NoSpeechGeneratedError,
    OpenAIProvider,
    ProviderError,
    ResolvedModel,
    SpeechResult,
    XAIProvider,
    generate_speech,
    stream_speech,
)
from speech_sdk._http import RetryTiming
from speech_sdk.providers import OpenAIProvider as ExportedOpenAI
from speech_sdk.providers import XAIProvider as ExportedXAI


class APITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(
            patch.object(
                httpx.AsyncHTTPTransport,
                "handle_async_request",
                side_effect=AssertionError("Real network forbidden"),
            )
        )
        self.stack.enter_context(patch("asyncio.sleep", side_effect=AssertionError("Real sleep")))

    def test_explicit_matching_public_signatures_and_exports(self) -> None:
        buffered = inspect.signature(generate_speech)
        streamed = inspect.signature(stream_speech)
        self.assertEqual(buffered.parameters, streamed.parameters)
        self.assertEqual(
            list(buffered.parameters),
            [
                "model",
                "text",
                "voice",
                "output",
                "instructions",
                "provider_options",
                "api_key",
                "http_client",
                "timeout",
                "max_retries",
                "headers",
            ],
        )
        self.assertTrue(all(p.kind == p.KEYWORD_ONLY for p in buffered.parameters.values()))
        self.assertTrue(inspect.iscoroutinefunction(generate_speech))
        self.assertFalse(inspect.iscoroutinefunction(stream_speech))
        self.assertIs(ExportedOpenAI, OpenAIProvider)
        self.assertIs(ExportedXAI, XAIProvider)

    async def test_known_model_resolution(self) -> None:
        identifiers = (
            ("openai", "openai", "gpt-4o-mini-tts"),
            ("openai/gpt-4o-mini-tts", "openai", "gpt-4o-mini-tts"),
            ("openai/tts-1", "openai", "tts-1"),
            ("openai/tts-1-hd", "openai", "tts-1-hd"),
            ("xai", "xai", "grok-tts"),
            ("xai/grok-tts", "xai", "grok-tts"),
            (OpenAIProvider(api_key="configured").model(), "openai", "gpt-4o-mini-tts"),
            (XAIProvider(api_key="configured").model(), "xai", "grok-tts"),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"audio"))
        ) as client:
            for model, provider, name in identifiers:
                with self.subTest(provider=provider, name=name):
                    result = await generate_speech(
                        model=model,
                        text=" hi🙂 ",
                        voice=" voice ",
                        api_key="test-key",
                        http_client=client,
                    )
                    self.assertIsInstance(result, SpeechResult)
                    self.assertEqual((result.provider, result.model), (provider, name))
                    self.assertEqual(result.metadata.input_chars, 5)
                    self.assertIsNone(result.provider_metadata)
                    self.assertEqual(result.warnings, ())
                    self.assertGreaterEqual(result.metadata.latency_ms, 0)
                    self.assertFalse(client.is_closed)

    async def exact_request(self, provider: str, codec: str, mime: str) -> None:
        native: dict[str, object] = (
            {"response_format": codec, "model": "ignored", "input": "ignored", "voice": "ignored"}
            if provider == "openai"
            else {"output_format": {"codec": codec}, "text": "ignored", "voice_id": "ignored"}
        )
        original = json.loads(json.dumps(native))
        expected: dict[str, object] = (
            {
                "response_format": codec,
                "model": "gpt-4o-mini-tts",
                "input": " [tag] hi🙂 ",
                "voice": " voice ",
                "stream_format": "audio",
            }
            if provider == "openai"
            else {
                "output_format": {"codec": codec},
                "text": " [tag] hi🙂 ",
                "voice_id": " voice ",
                "language": "auto",
            }
        )
        if provider == "xai" and codec == "pcm":
            expected["output_format"] = {"codec": "pcm", "sample_rate": 24000}
        seen: list[httpx.Request] = []
        fixture = b"\x00\xff\x01partial\x00"

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(json.loads(request.content), expected)
            self.assertEqual(
                str(request.url),
                "https://custom.example/prefix/v1/"
                + ("audio/speech" if provider == "openai" else "tts"),
            )
            self.assertEqual(request.headers["authorization"], "Bearer call-key")
            self.assertEqual(request.headers["content-type"], "application/json")
            self.assertEqual(request.headers["user-agent"], "HYBRD/speech-sdk-python")
            self.assertEqual(request.headers["x-custom"], "kept")
            self.assertNotIn("x-client", request.headers)
            self.assertNotIn("cookie", request.headers)
            self.assertEqual(request.extensions["timeout"], httpx.Timeout(7).as_dict())
            return httpx.Response(200, content=fixture, headers={"content-type": mime})

        configured = (OpenAIProvider if provider == "openai" else XAIProvider)(
            api_key="config-key", base_url="https://custom.example/prefix/v1///"
        ).model()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            auth=("bad", "bad"),
            cookies={"bad": "bad"},
            headers={"X-Client": "bad"},
            follow_redirects=True,
        ) as client:
            defaults = dict(client.headers)
            result = await generate_speech(
                model=configured,
                text=" [tag] hi🙂 ",
                voice=" voice ",
                provider_options=native,
                api_key="call-key",
                http_client=client,
                timeout=7,
                headers={"Authorization": "ignored", "Content-Type": "ignored", "X-Custom": "kept"},
            )
            async with stream_speech(
                model=configured,
                text=" [tag] hi🙂 ",
                voice=" voice ",
                provider_options=native,
                api_key="call-key",
                http_client=client,
                timeout=7,
                headers={"Authorization": "ignored", "Content-Type": "ignored", "X-Custom": "kept"},
            ) as stream:
                chunks = [chunk async for chunk in stream.audio]
                self.assertEqual(b"".join(chunks), fixture)
                self.assertEqual(stream.media_type, result.audio.media_type)
                self.assertEqual((stream.provider, stream.model), (result.provider, result.model))
                self.assertIsNone(stream.provider_metadata)
                self.assertEqual(stream.warnings, ())
            self.assertEqual(dict(client.headers), defaults)
            self.assertFalse(client.is_closed)
        self.assertEqual(result.audio.data, fixture)
        self.assertEqual(result.audio.media_type, mime)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].content, seen[1].content)
        self.assertEqual(native, original)

    async def test_exact_public_requests_all_native_formats(self) -> None:
        formats = {
            "openai": {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "pcm": "audio/pcm;rate=24000",
                "opus": "audio/opus",
                "aac": "audio/aac",
                "flac": "audio/flac",
            },
            "xai": {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "pcm": "audio/pcm;rate=24000",
                "mulaw": "audio/basic",
                "alaw": "audio/alaw",
            },
        }
        for provider, codecs in formats.items():
            for codec, mime in codecs.items():
                with self.subTest(provider=provider, codec=codec):
                    await self.exact_request(provider, codec, mime)

    async def test_defaults_explicit_outputs_instructions_and_limits(self) -> None:
        for provider, limit in (("openai", 4096), ("xai", 60000)):
            for output in (None, AudioOutput("mp3"), AudioOutput("wav"), AudioOutput("pcm")):
                body: dict[str, object] = (
                    {
                        "model": "gpt-4o-mini-tts",
                        "input": "🙂" * limit,
                        "voice": " v ",
                        "stream_format": "audio",
                    }
                    if provider == "openai"
                    else {"text": "🙂" * limit, "voice_id": " v ", "language": "auto"}
                )
                if output is not None:
                    if provider == "openai":
                        body["response_format"] = output.format
                    else:
                        native: dict[str, object] = {"codec": output.format}
                        if output.format in ("wav", "pcm"):
                            native["sample_rate"] = 24000
                        body["output_format"] = native
                requests: list[httpx.Request] = []

                def handler(
                    request: httpx.Request, requests: list[httpx.Request] = requests
                ) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(200, content=b"\x00\xff")

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await generate_speech(
                        model=provider,
                        text="🙂" * limit,
                        voice=" v ",
                        output=output,
                        api_key="key",
                        http_client=client,
                    )
                    async with stream_speech(
                        model=provider,
                        text="🙂" * limit,
                        voice=" v ",
                        output=output,
                        api_key="key",
                        http_client=client,
                    ) as stream:
                        self.assertEqual(
                            b"".join([chunk async for chunk in stream.audio]), b"\x00\xff"
                        )
                    self.assertEqual(result.metadata.input_chars, limit)
                self.assertEqual(len(requests), 2)
                for request in requests:
                    self.assertEqual(json.loads(request.content), body)
        requests = []

        def instructions_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"audio")

        options: dict[str, object] = {"instructions": " native ", "response_format": "mp3"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(instructions_handler)) as client:
            await generate_speech(
                model="openai",
                text="hi",
                voice="v",
                instructions=" canonical ",
                output=AudioOutput("wav"),
                provider_options=options,
                api_key="key",
                http_client=client,
            )
            async with stream_speech(
                model="openai",
                text="hi",
                voice="v",
                instructions=" canonical ",
                output=AudioOutput("wav"),
                provider_options=options,
                api_key="key",
                http_client=client,
            ):
                pass
        self.assertEqual(requests[0].content, requests[1].content)
        self.assertEqual(json.loads(requests[0].content)["instructions"], " canonical \n\n native ")
        self.assertEqual(json.loads(requests[0].content)["response_format"], "wav")
        self.assertEqual(options, {"instructions": " native ", "response_format": "mp3"})

    async def test_buffered_owned_success_failure_empty_cleanup(self) -> None:
        for status, payload, expected in (
            (200, b"audio", None),
            (401, b"error", ProviderError),
            (200, b"", NoSpeechGeneratedError),
        ):
            response = httpx.Response(status, content=payload)
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            )
            with (
                patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
                patch.object(client, "aclose", wraps=client.aclose) as close,
            ):
                if expected is None:
                    result = await generate_speech(model="xai", text="hi", voice="v", api_key="key")
                    self.assertEqual(result.audio.data, payload)
                else:
                    with self.assertRaises(expected):
                        await generate_speech(
                            model="xai", text="hi", voice="v", api_key="key", max_retries=0
                        )
                factory.assert_called_once_with()
                close.assert_awaited_once_with()
            self.assertTrue(client.is_closed)
            self.assertTrue(response.is_closed)

    async def test_keys_precedence_and_blank_no_fallback(self) -> None:
        for provider_type, env in (
            (OpenAIProvider, "OPENAI_API_KEY"),
            (XAIProvider, "XAI_API_KEY"),
        ):
            with patch.dict(os.environ, {env: "env-key"}):
                for configured, explicit, expected in (
                    (None, None, "env-key"),
                    ("config-key", None, "config-key"),
                    ("config-key", "call-key", "call-key"),
                ):
                    requests: list[httpx.Request] = []

                    def handler(
                        request: httpx.Request, requests: list[httpx.Request] = requests
                    ) -> httpx.Response:
                        requests.append(request)
                        return httpx.Response(200, content=b"a")

                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        model = provider_type(api_key=configured).model()
                        await generate_speech(
                            model=model, text="hi", voice="v", api_key=explicit, http_client=client
                        )
                    self.assertEqual(requests[0].headers["authorization"], f"Bearer {expected}")
                    self.assertNotIn(expected, repr(model))
                    self.assertNotIn(expected, repr(model.provider))
                for blank in ("", " "):
                    with self.assertRaises(MissingApiKeyError):
                        await generate_speech(
                            model=provider_type(api_key="config").model(),
                            text="hi",
                            voice="v",
                            api_key=blank,
                        )
                    with self.assertRaises(MissingApiKeyError):
                        async with stream_speech(
                            model=provider_type(api_key=blank).model(), text="hi", voice="v"
                        ):
                            self.fail("Blank configuration used environment fallback")

    async def invalid_call(self, values: Mapping[str, object]) -> None:
        arguments: dict[str, object] = {
            "model": "xai",
            "text": "hi",
            "voice": "v",
            "api_key": "key",
        }
        arguments.update(values)
        # Invalid runtime types intentionally exercise the public trust boundary.
        model = cast(str | ResolvedModel, arguments["model"])
        text = cast(str, arguments["text"])
        voice = cast(str, arguments["voice"])
        output = cast(AudioOutput | None, arguments.get("output"))
        options = cast(Mapping[str, object] | None, arguments.get("provider_options"))
        instructions = cast(str | None, arguments.get("instructions"))
        timeout = cast(float | httpx.Timeout, arguments.get("timeout", 60.0))
        retries = cast(int, arguments.get("max_retries", 2))
        headers = cast(Mapping[str, str] | None, arguments.get("headers"))
        with patch(
            "speech_sdk._http.httpx.AsyncClient", side_effect=AssertionError("Client created")
        ):
            with self.assertRaises((ValueError, TypeError, MissingApiKeyError)) as caught:
                await generate_speech(
                    model=model,
                    text=text,
                    voice=voice,
                    output=output,
                    provider_options=options,
                    instructions=instructions,
                    timeout=timeout,
                    max_retries=retries,
                    headers=headers,
                    api_key="key",
                )
            self.assertNotIn("secret-identifier", str(caught.exception))
            with self.assertRaises((ValueError, TypeError, MissingApiKeyError)):
                async with stream_speech(
                    model=model,
                    text=text,
                    voice=voice,
                    output=output,
                    provider_options=options,
                    instructions=instructions,
                    timeout=timeout,
                    max_retries=retries,
                    headers=headers,
                    api_key="key",
                ):
                    self.fail("Invalid input published")

    async def test_invalid_resolution_and_inputs_before_client(self) -> None:
        for model in (
            "",
            "unknown",
            "secret-identifier",
            "/grok-tts",
            "xai/",
            "xai/nope",
            "xai/grok-tts/extra",
            "openai/secret-identifier",
            "OPENAI",
            " xai",
            3,
            ResolvedModel(XAIProvider(), "unknown"),
            ResolvedModel(XAIProvider(), cast(str, None)),
            ResolvedModel(XAIProvider(), ""),
        ):
            await self.invalid_call({"model": model})
        for values in (
            {"text": 3},
            {"voice": " "},
            {"voice": None},
            {"text": "🙂" * 60001},
            {"model": "openai", "text": "🙂" * 4097},
            {"output": "wav"},
            {"provider_options": {"nested": {"x": float("nan")}}},
            {"timeout": False},
            {"timeout": float("inf")},
            {"timeout": httpx.Timeout(None)},
            {"max_retries": True},
            {"max_retries": -1},
            {"headers": {"HOST": "bad"}},
            {"headers": {"x": "a\nb"}},
        ):
            await self.invalid_call(values)

    async def test_unsupported_instructions_rates_options_zero_http(self) -> None:
        cases: tuple[dict[str, object], ...] = (
            {"instructions": "speak slowly"},
            {"provider_options": {"instructions": "slow"}},
            {"provider_options": {"with_timestamps": True}},
            {"provider_options": {"model": "x"}},
            {"provider_options": {"replace": {}}},
            {"provider_options": {"speed": True}},
            {"provider_options": {"output_format": {"codec": "pcm", "bit_rate": 128000}}},
            {"output": AudioOutput("wav", 12345)},
            {"model": "openai/tts-1", "instructions": "slow"},
            {"model": "openai/tts-1-hd", "provider_options": {"instructions": "slow"}},
            {"model": "openai", "output": AudioOutput("wav", 48000)},
            {"model": "openai", "provider_options": {"stream_format": "sse"}},
            {"model": "openai", "provider_options": {"speed": float("inf")}},
            {"model": "openai", "provider_options": {"response_format": "json"}},
        )
        for values in cases:
            await self.invalid_call(values)

    async def test_buffered_full_timing_includes_validation_retry_body(self) -> None:
        now = [1.0]
        attempts = 0
        prepared = XAIProvider(api_key="key").prepare(model_id="grok-tts", text="hi", voice="v")

        def prepare(**kwargs: object) -> object:
            self.assertEqual(kwargs["model_id"], "grok-tts")
            now[0] += 0.2
            return prepared

        async def sleep(seconds: float) -> None:
            now[0] += 0.3

        class TimedBody(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                now[0] += 0.4
                yield b"audio"

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            now[0] += 0.5
            return (
                httpx.Response(429, content=b"error")
                if attempts == 1
                else httpx.Response(200, stream=TimedBody())
            )

        # Validation, retry and body read advance a fake clock independently.
        with (
            patch.object(XAIProvider, "prepare", side_effect=prepare),
            patch("speech_sdk.api.time.monotonic", side_effect=lambda: now[0]),
            patch(
                "speech_sdk._http.RetryTiming",
                return_value=RetryTiming(monotonic=lambda: now[0], random=lambda: 0.0, sleep=sleep),
            ),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await generate_speech(
                    model="xai", text="hi", voice="v", api_key="key", http_client=client
                )
        self.assertAlmostEqual(result.metadata.latency_ms, 1900)
        self.assertEqual(attempts, 2)
