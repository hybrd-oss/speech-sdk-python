"""Azure v1 contracts: synthetic configuration, MockTransport, no provider calls."""

import asyncio
import copy
import json
import os
import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
from typing import cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx

from speech_sdk import (
    AudioOutput,
    AzureOpenAIProvider,
    MissingApiKeyError,
    NoSpeechGeneratedError,
    OpenAIProvider,
    ProviderError,
    ResolvedModel,
    XAIProvider,
    generate_speech,
    stream_speech,
)
from speech_sdk._http import RetryTiming
from speech_sdk.providers import AzureOpenAIProvider as ExportedAzure
from speech_sdk.types import PreparedRequest

ROOT = "https://fixture.invalid/prefix"
FORMATS = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm;rate=24000",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
AUTH_HEADERS = {
    "AUTHORIZATION": "caller-secret",
    "Author_ization": "caller-secret",
    "author-ization": "caller-secret",
    "API-Key": "caller-secret",
    "ApiKey": "caller-secret",
    "api_key": "caller-secret",
    "CONTENT-TYPE": "ignored",
    "X-Trace": "kept",
}


def prepare(**overrides: object) -> PreparedRequest:
    values: dict[str, object] = {
        "model_id": "org/未来",
        "text": "Hello from Python!",
        "voice": "alloy",
        "api_key": "call-key",
    }
    values.update(overrides)
    provider = cast(AzureOpenAIProvider, values.pop("provider", AzureOpenAIProvider(base_url=ROOT)))
    return provider.prepare(
        model_id=cast(str, values["model_id"]),
        text=cast(str, values["text"]),
        voice=cast(str, values["voice"]),
        output=cast(AudioOutput | None, values.get("output")),
        instructions=cast(str | None, values.get("instructions")),
        provider_options=cast(Mapping[str, object] | None, values.get("options")),
        api_key=cast(str | None, values["api_key"]),
        timeout=cast(float | httpx.Timeout, values.get("timeout", 60.0)),
        max_retries=cast(int, values.get("max_retries", 2)),
        headers=cast(Mapping[str, str] | None, values.get("headers")),
    )


class SyntheticEnvironment:
    def setUp(self) -> None:
        self.stack = ExitStack()
        cast(unittest.TestCase, self).addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(
            patch.object(
                httpx.AsyncHTTPTransport,
                "handle_async_request",
                side_effect=AssertionError("Real network forbidden"),
            )
        )
        self.stack.enter_context(patch("asyncio.sleep", side_effect=AssertionError("Real sleep")))


class AzureConfigTests(SyntheticEnvironment, unittest.TestCase):
    def test_exports_frozen_configuration_and_private_repr(self) -> None:
        self.assertIs(AzureOpenAIProvider, ExportedAzure)
        provider = AzureOpenAIProvider(
            api_key="config-secret", base_url=ROOT, api_version="secret-v"
        )
        self.assertEqual(provider.name, "azure")
        with self.assertRaises(FrozenInstanceError):
            provider.__setattr__("api_version", "other")
        with self.assertRaises(TypeError):
            AzureOpenAIProvider(**cast(dict[str, str], {"base_url": ROOT, "name": "other"}))
        for representation in (repr(provider), repr(provider.model("deployment")), repr(prepare())):
            for secret in (ROOT, "config-secret", "secret-v", "call-key", "Hello from Python!"):
                self.assertNotIn(secret, representation)

    def test_url_and_version_normalization(self) -> None:
        for root in (ROOT, ROOT + "/", ROOT + "/openai/v1", ROOT + "/openai/v1///"):
            for version in ("preview", "future?&api-version=other# /未来", " version "):
                request = prepare(provider=AzureOpenAIProvider(base_url=root, api_version=version))
                url = urlsplit(request.url)
                self.assertEqual(url.path, "/prefix/openai/v1/audio/speech")
                self.assertEqual(parse_qs(url.query), {"api-version": [version]})
                self.assertEqual(url.fragment, "")
                self.assertNotIn("org", request.url)
        self.assertEqual(prepare().url, ROOT + "/openai/v1/audio/speech?api-version=preview")
        self.assertEqual(
            prepare(provider=AzureOpenAIProvider(base_url="http://fixture.invalid///")).url,
            "http://fixture.invalid/openai/v1/audio/speech?api-version=preview",
        )

    def test_url_suffix_uses_only_path(self) -> None:
        for root, expected in (
            ("https://openai/v1", "/v1/openai/v1/audio/speech"),
            ("https://openai/v1///", "/v1/openai/v1/audio/speech"),
            ("https://fixture.invalid/openai/v10", "/openai/v10/openai/v1/audio/speech"),
            ("https://fixture.invalid/openai/v1///", "/openai/v1/audio/speech"),
            (ROOT + "/openai/v1", "/prefix/openai/v1/audio/speech"),
            ("https://fixture.invalid", "/openai/v1/audio/speech"),
            (ROOT, "/prefix/openai/v1/audio/speech"),
        ):
            with self.subTest(root=root):
                request = prepare(provider=AzureOpenAIProvider(base_url=root))
                self.assertEqual(urlsplit(request.url).path, expected)

    def test_url_environment_precedence_and_fail_closed(self) -> None:
        with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": ROOT + "/endpoint"}):
            self.assertEqual(AzureOpenAIProvider().base_url, ROOT + "/endpoint")
            with patch.dict(os.environ, {"AZURE_OPENAI_BASE_URL": ROOT + "/base"}):
                self.assertEqual(AzureOpenAIProvider().base_url, ROOT + "/base")
                self.assertEqual(AzureOpenAIProvider(base_url=ROOT).base_url, ROOT)
                with self.assertRaises(ValueError):
                    AzureOpenAIProvider(base_url="")
            with (
                patch.dict(os.environ, {"AZURE_OPENAI_BASE_URL": ""}),
                self.assertRaises(ValueError),
            ):
                AzureOpenAIProvider()
        with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": ""}), self.assertRaises(ValueError):
            AzureOpenAIProvider()
        with patch.dict(os.environ, {"OPENAI_BASE_URL": ROOT, "OPENAI_API_VERSION": "other"}):
            with self.assertRaisesRegex(ValueError, "AZURE_OPENAI_BASE_URL"):
                AzureOpenAIProvider()
            self.assertEqual(AzureOpenAIProvider(base_url=ROOT).api_version, "preview")

    def test_invalid_configuration_safe_before_client(self) -> None:
        class Subclass(str):
            def strip(self, _chars: str | None = None, /) -> str:
                raise AssertionError("Subclass method called")

        class NonString:
            def __repr__(self) -> str:
                return "secret-nonstring"

        with patch("speech_sdk._http.httpx.AsyncClient") as factory:
            for url in (
                "",
                "secret-url",
                "ftp://secret.invalid",
                "https://user:secret@fixture.invalid",
                ROOT + "?api-version=preview",
                ROOT + "#secret",
                ROOT + "?",
                ROOT + "\\secret",
                ROOT + "\nsecret",
                "https://fixture.invalid:bad",
                "https://fixture.invalid:0",
                "https://fixture.invalid:99999",
                "https://[broken",
                cast(str, 1),
            ):
                with self.subTest(kind="url"), self.assertRaises(ValueError) as caught:
                    AzureOpenAIProvider(base_url=url)
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("secret", repr(caught.exception))
            for version in ("", " \t", Subclass("secret-version"), NonString(), 1, True, None):
                with (
                    self.subTest(kind="version"),
                    self.assertRaises((TypeError, ValueError)) as invalid_version,
                ):
                    AzureOpenAIProvider(base_url=ROOT, api_version=cast(str, version))
                self.assertNotIn("secret", str(invalid_version.exception))
                self.assertNotIn("secret", repr(invalid_version.exception))
            provider = AzureOpenAIProvider(base_url=ROOT)
            model: object
            for model in ("", " \n", Subclass("secret-model"), NonString(), 1, True, [], {}):
                with (
                    self.subTest(kind="model"),
                    self.assertRaises((TypeError, ValueError)) as invalid_model,
                ):
                    provider.model(cast(str, model))
                self.assertNotIn("secret", str(invalid_model.exception))
                self.assertNotIn("secret", repr(invalid_model.exception))
                with self.assertRaises((TypeError, ValueError)) as invalid_request:
                    prepare(model_id=model)
                self.assertNotIn("secret", str(invalid_request.exception))
                self.assertNotIn("secret", repr(invalid_request.exception))
            factory.assert_not_called()

    def test_deployment_environment_and_verbatim_names(self) -> None:
        provider = AzureOpenAIProvider(base_url=ROOT)
        with self.assertRaisesRegex(ValueError, "AZURE_OPENAI_DEPLOYMENT_NAME"):
            provider.model()
        for name in ("org/未来", "tts-1", "tts-1-hd", "/", " future\nmodel "):
            with patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT_NAME": name}):
                self.assertEqual(provider.model(None).model_id, name)
                self.assertEqual(provider.model("explicit").model_id, "explicit")
            request = prepare(
                model_id=name, instructions="canonical", options={"instructions": "native"}
            )
            self.assertEqual(request.model, name)
            self.assertEqual(json.loads(request.content)["model"], name)
            self.assertEqual(json.loads(request.content)["instructions"], "canonical\n\nnative")
        with (
            patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT_NAME": " "}),
            self.assertRaises(ValueError),
        ):
            provider.model()

    def test_key_precedence_blank_and_no_cross_provider_fallback(self) -> None:
        provider = AzureOpenAIProvider(base_url=ROOT, api_key="config-key")
        with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "env-key", "OPENAI_API_KEY": "wrong"}):
            self.assertEqual(prepare(provider=provider).headers["api-key"], "call-key")
            self.assertEqual(
                prepare(provider=provider, api_key=None).headers["api-key"], "config-key"
            )
            self.assertEqual(prepare(api_key=None).headers["api-key"], "env-key")
            for blank in ("", " "):
                with self.assertRaises(MissingApiKeyError):
                    prepare(provider=provider, api_key=blank)
                with self.assertRaises(MissingApiKeyError):
                    prepare(
                        provider=AzureOpenAIProvider(base_url=ROOT, api_key=blank), api_key=None
                    )
            with (
                patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": ""}),
                self.assertRaises(MissingApiKeyError),
            ):
                prepare(api_key=None)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "wrong"}):
            with self.assertRaises(MissingApiKeyError) as caught:
                prepare(api_key=None)
            self.assertEqual(caught.exception.env_var, "AZURE_OPENAI_API_KEY")
        for key in ("bad\nsecret", "bad secret", "秘密"):
            with self.assertRaises(ValueError) as invalid:
                prepare(api_key=key)
            self.assertNotIn(key, str(invalid.exception))

    def test_body_precedence_immutable_and_limits(self) -> None:
        options: dict[str, object] = {
            "model": "wrong",
            "input": "wrong",
            "voice": {"id": "wrong"},
            "instructions": " native ",
            "speed": 0.25,
            "future": {"values": [1, True, None]},
        }
        before = copy.deepcopy(options)
        request = prepare(
            text=" [tag] Hé🙂 ", voice=" unknown ", instructions=" canonical ", options=options
        )
        self.assertEqual(
            json.loads(request.content),
            {
                **before,
                "model": "org/未来",
                "input": " [tag] Hé🙂 ",
                "voice": " unknown ",
                "instructions": " canonical \n\n native ",
                "stream_format": "audio",
            },
        )
        self.assertEqual(options, before)
        cast(dict[str, object], options["future"])["values"] = []
        self.assertEqual(json.loads(request.content)["future"], before["future"])
        self.assertEqual(prepare(text="🙂" * 4096).input_chars, 4096)
        self.assertEqual(
            len(
                json.loads(
                    prepare(instructions="a" * 2047, options={"instructions": "b" * 2047}).content
                )["instructions"]
            ),
            4096,
        )
        self.assertNotIn(
            "instructions",
            json.loads(prepare(instructions=" \n", options={"instructions": "\t"}).content),
        )
        for values in (
            {"text": "a" * 4097},
            {"instructions": "a" * 4097},
            {"instructions": "a" * 2048, "options": {"instructions": "b" * 2047}},
        ):
            with self.assertRaises(ValueError):
                prepare(**values)
        with self.assertRaises(NoSpeechGeneratedError):
            prepare(text=" \t")

    def test_common_native_output_precedence_and_rate_metadata(self) -> None:
        for codec in ("mp3", "wav", "pcm"):
            for rate in (None, 24000):
                output = AudioOutput(codec, rate)
                options: dict[str, object] = {"response_format": "opus", "sample_rate": rate}
                request = prepare(output=output, options=options)
                self.assertEqual(request.media_type, FORMATS[codec])
                self.assertEqual(json.loads(request.content)["response_format"], codec)
                self.assertNotIn("sample_rate", json.loads(request.content))
                self.assertEqual(options, {"response_format": "opus", "sample_rate": rate})
                with self.assertRaises(ValueError):
                    prepare(output=output, options={"response_format": "invalid"})
        self.assertEqual(prepare().media_type, "audio/mpeg")
        self.assertNotIn("response_format", json.loads(prepare().content))

    def test_local_option_validation(self) -> None:
        invalid: tuple[dict[str, object], ...] = (
            {"voice": " "},
            {"text": 1},
            {"voice": {}},
            {"instructions": True},
            {"options": {"instructions": None}},
            {"options": {"response_format": "sse"}},
            {"options": {"response_format": "MP3"}},
            {"options": {"stream_format": "sse"}},
            {"options": {"future": [float("nan")]}},
            {"options": {"future": object()}},
            {"options": {"future": {1: "bad"}}},
            {"options": []},
            {"output": {}},
            {"output": AudioOutput("wav", 48000)},
            {"timeout": 0},
            {"timeout": True},
            {"timeout": httpx.Timeout(None)},
            {"max_retries": -1},
            {"max_retries": True},
            {"headers": {"Host": "wrong"}},
            {"headers": {"Content-Length": "0"}},
            {"headers": {"Transfer-Encoding": "chunked"}},
            {"headers": {"x": "bad\nsecret"}},
        )
        for values in invalid:
            with self.subTest(fields=tuple(values)), self.assertRaises((TypeError, ValueError)):
                prepare(**values)
        for field in (
            "api-version",
            "API_VERSION",
            "apiVersion",
            "apiversion",
            " api_version ",
            "API-Key",
            "apikey",
            "Author_ization",
            "stream",
            "response_mode",
            "with_timestamps",
            "timestamps",
            "streamFormat",
            "sampleRate",
            "headers",
            "url",
            "method",
            "body",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                prepare(options={field: "wrong"})
        for speed in (True, "1", None, 0.24, 4.01, float("inf"), float("nan"), 10**1000):
            with self.subTest(speed=type(speed)), self.assertRaises((TypeError, ValueError)):
                prepare(options={"speed": speed})
        for rate in (True, "24000", 24000.0, 0, 48000):
            with self.assertRaises((TypeError, ValueError)):
                prepare(options={"sample_rate": rate})
        self.assertEqual(json.loads(prepare(options={"speed": 4}).content)["speed"], 4)


class GatedBody(httpx.AsyncByteStream):
    def __init__(self, error: Exception | None = None) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.reads = 0
        self.closes = 0
        self.error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.reads += 1
        yield b"\x00"
        self.waiting.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        self.reads += 1
        yield b"\xff\x01"

    async def aclose(self) -> None:
        self.closes += 1


class AzurePublicTests(SyntheticEnvironment, unittest.IsolatedAsyncioTestCase):
    async def test_exact_configured_and_prefix_formats_both_modes(self) -> None:
        for configured in (False, True):
            for codec, mime in FORMATS.items():
                await self.exact_request(configured, codec, mime)

    async def exact_request(self, configured: bool, codec: str, mime: str) -> None:
        seen: list[httpx.Request] = []
        options: dict[str, object] = {
            "response_format": codec,
            "sample_rate": 24000,
            "model": "wrong",
        }
        before = options.copy()
        version = "preview?&other=1#未来"
        timeout = httpx.Timeout(connect=1, read=2, write=3, pool=4)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/prefix/openai/v1/audio/speech")
            self.assertEqual(
                parse_qs(request.url.query.decode()),
                {"api-version": [version if configured else "preview"]},
            )
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "org/未来",
                    "input": "Hello from Python!",
                    "voice": "alloy",
                    "response_format": codec,
                    "stream_format": "audio",
                },
            )
            self.assertEqual(request.headers["api-key"], "call-key")
            self.assertEqual(request.headers["content-type"], "application/json")
            self.assertEqual(request.headers["user-agent"], "HYBRD/speech-sdk-python")
            self.assertEqual(request.headers["x-trace"], "kept")
            for name in (
                "authorization",
                "apikey",
                "api_key",
                "author_ization",
                "author-ization",
                "cookie",
                "x-client",
            ):
                self.assertNotIn(name, request.headers)
            self.assertEqual(request.extensions["timeout"], timeout.as_dict())
            return httpx.Response(200, content=b"\x00\xff\x01", headers={"content-type": mime})

        with patch.dict(
            os.environ, {"AZURE_OPENAI_BASE_URL": ROOT, "AZURE_OPENAI_API_KEY": "env-key"}
        ):
            model: str | ResolvedModel = "azure/org/未来"
            if configured:
                model = AzureOpenAIProvider(
                    api_key="config-key", base_url=ROOT + "/openai/v1///", api_version=version
                ).model("org/未来")
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                auth=("bad", "bad"),
                cookies={"secret": "bad"},
                headers={"X-Client": "bad", "API-Key": "injected-secret"},
                follow_redirects=True,
            ) as client:
                defaults = dict(client.headers)
                result = await generate_speech(
                    model=model,
                    text="Hello from Python!",
                    voice="alloy",
                    provider_options=options,
                    api_key="call-key",
                    http_client=client,
                    timeout=timeout,
                    headers=AUTH_HEADERS,
                )
                async with stream_speech(
                    model=model,
                    text="Hello from Python!",
                    voice="alloy",
                    provider_options=options,
                    api_key="call-key",
                    http_client=client,
                    timeout=timeout,
                    headers=AUTH_HEADERS,
                ) as stream:
                    self.assertEqual(
                        b"".join([chunk async for chunk in stream.audio]), result.audio.data
                    )
                    self.assertEqual(stream.media_type, mime)
                    self.assertEqual((stream.provider, stream.model), ("azure", "org/未来"))
                    self.assertEqual(stream.metadata.input_chars, 18)
                    self.assertGreaterEqual(stream.metadata.setup_latency_ms, 0)
                self.assertFalse(client.is_closed)
                self.assertEqual(dict(client.headers), defaults)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].content, seen[1].content)
        self.assertEqual(options, before)
        self.assertEqual((result.provider, result.model), ("azure", "org/未来"))
        self.assertEqual(result.audio.media_type, mime)
        self.assertEqual(result.audio.data, b"\x00\xff\x01")
        self.assertIsNone(result.provider_metadata)
        self.assertEqual(result.warnings, ())

    async def test_default_and_common_outputs_bare_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_ENDPOINT": ROOT,
                "AZURE_OPENAI_DEPLOYMENT_NAME": "tts-1",
                "AZURE_OPENAI_API_KEY": "env-key",
            },
        ):
            for output in (None, AudioOutput("mp3"), AudioOutput("wav", 24000), AudioOutput("pcm")):
                seen: list[httpx.Request] = []

                def handler(
                    request: httpx.Request, seen: list[httpx.Request] = seen
                ) -> httpx.Response:
                    seen.append(request)
                    return httpx.Response(200, content=b"unchanged")

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await generate_speech(
                        model="azure",
                        text="hi",
                        voice="v",
                        output=output,
                        instructions="valid",
                        http_client=client,
                    )
                    async with stream_speech(
                        model="azure",
                        text="hi",
                        voice="v",
                        output=output,
                        instructions="valid",
                        http_client=client,
                    ) as stream:
                        self.assertEqual(
                            b"".join([chunk async for chunk in stream.audio]), b"unchanged"
                        )
                expected: dict[str, object] = {
                    "model": "tts-1",
                    "input": "hi",
                    "voice": "v",
                    "instructions": "valid",
                    "stream_format": "audio",
                }
                if output is not None:
                    expected["response_format"] = output.format
                self.assertEqual(len(seen), 2)
                self.assertEqual(json.loads(seen[0].content), expected)
                self.assertEqual(seen[0].content, seen[1].content)
                self.assertEqual(seen[0].headers["api-key"], "env-key")
                self.assertEqual(
                    result.audio.media_type, FORMATS[output.format if output else "mp3"]
                )

    async def test_invalid_public_calls_no_client_or_requests(self) -> None:
        class Subclass(str):
            pass

        with (
            patch.dict(os.environ, {"AZURE_OPENAI_BASE_URL": ROOT}),
            patch("speech_sdk._http.httpx.AsyncClient") as factory,
        ):
            for model in (
                "azure",
                "azure/",
                "azure/ \n",
                Subclass("azure/model"),
                ResolvedModel(AzureOpenAIProvider(base_url=ROOT), Subclass("model")),
            ):
                with self.assertRaises((TypeError, ValueError)):
                    await generate_speech(model=model, text="hi", voice="v", api_key="key")
                with self.assertRaises((TypeError, ValueError)):
                    async with stream_speech(model=model, text="hi", voice="v", api_key="key"):
                        self.fail("Invalid model exposed")
            for options in ({"apiVersion": "wrong"}, {"speed": True}, {"stream_format": "sse"}):
                with self.assertRaises((TypeError, ValueError)):
                    await generate_speech(
                        model="azure/model",
                        text="hi",
                        voice="v",
                        api_key="key",
                        provider_options=options,
                    )
                with self.assertRaises((TypeError, ValueError)):
                    async with stream_speech(
                        model="azure/model",
                        text="hi",
                        voice="v",
                        api_key="key",
                        provider_options=options,
                    ):
                        self.fail("Invalid options exposed")
            with self.assertRaises(MissingApiKeyError):
                await generate_speech(model="azure/model", text="hi", voice="v")
            factory.assert_not_called()

    async def test_auth_alias_stripping_all_providers(self) -> None:
        for provider in (
            AzureOpenAIProvider(api_key="sdk-key", base_url=ROOT),
            OpenAIProvider(api_key="sdk-key"),
            XAIProvider(api_key="sdk-key"),
        ):
            requests: list[httpx.Request] = []

            def handler(
                request: httpx.Request, requests: list[httpx.Request] = requests
            ) -> httpx.Response:
                requests.append(request)
                return httpx.Response(200, content=b"audio")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers={"ApiKey": "injected-secret"},
                auth=("bad", "bad"),
            ) as client:
                await generate_speech(
                    model=provider.model("deployment"),
                    text="hi",
                    voice="v",
                    headers=AUTH_HEADERS,
                    http_client=client,
                )
            self.assertEqual(len(requests), 1)
            auth = {
                name: value
                for name, value in requests[0].headers.items()
                if name.replace("_", "").replace("-", "") in ("authorization", "apikey")
            }
            self.assertEqual(
                auth,
                {"api-key": "sdk-key"}
                if provider.name == "azure"
                else {"authorization": "Bearer sdk-key"},
            )
            self.assertNotIn("caller-secret", str(requests[0].headers))
            self.assertNotIn("injected-secret", str(requests[0].headers))

    async def test_status_timeout_empty_mime_budgets_and_safe_errors(self) -> None:
        cases = (
            (401, "application/json", b'{"error":{"code":"secret-key","message":"secret-key"}}', 1),
            (429, "", b"malformed secret-key", 3),
            (503, "", b"secret-key", 3),
            (302, "", b"secret-key", 1),
            (200, "", b"", 3),
            (200, "text/event-stream", b"data: x", 1),
            (200, "text/html", b"bad", 1),
            (200, "application/json", b"{}", 1),
            (200, "audio/wav", b"audio", 1),
            (0, "", b"", 3),
        )
        for status, mime, payload, count in cases:
            for buffered in (False, True):
                await self.rejected_response(
                    status,
                    mime,
                    payload,
                    count if buffered or status != 200 or payload else 1,
                    buffered,
                )

    async def rejected_response(
        self, status: int, mime: str, payload: bytes, count: int, buffered: bool
    ) -> None:
        requests: list[httpx.Request] = []
        responses: list[httpx.Response] = []
        delays: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(all(response.is_closed for response in responses))
            requests.append(request)
            if status == 0:
                raise httpx.ReadTimeout("secret-key")
            response = httpx.Response(
                status,
                content=payload,
                headers={"content-type": mime, "x-request-id": "secret-key", "location": ROOT},
            )
            responses.append(response)
            return response

        async def sleep(seconds: float) -> None:
            self.assertTrue(all(response.is_closed for response in responses))
            delays.append(seconds)

        model = AzureOpenAIProvider(base_url=ROOT, api_key="secret-key").model(
            "secret-key/deployment"
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            with (
                patch(
                    "speech_sdk._http.RetryTiming",
                    return_value=RetryTiming(random=lambda: 0, sleep=sleep),
                ),
                self.assertRaises(ProviderError) as caught,
            ):
                if buffered:
                    await generate_speech(model=model, text="hi", voice="v", http_client=client)
                else:
                    async with stream_speech(
                        model=model, text="hi", voice="v", http_client=client
                    ) as stream:
                        async for _chunk in stream.audio:
                            self.fail("Invalid audio exposed")
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), count)
        self.assertEqual(delays, [1.0, 2.0] if count == 3 else [])
        self.assertEqual(
            (caught.exception.provider, caught.exception.model), ("azure", "secret-key/deployment")
        )
        self.assertNotIn("secret-key", repr(caught.exception))
        self.assertNotIn(ROOT, str(caught.exception))
        if status == 401:
            self.assertEqual(caught.exception.details, json.loads(payload))
            self.assertEqual(caught.exception.raw_response, payload.decode())

    async def test_retry_then_success_and_zero_budget(self) -> None:
        for budget, count in ((0, 1), (2, 2)):
            requests: list[httpx.Request] = []
            delays: list[float] = []

            def handler(
                request: httpx.Request, requests: list[httpx.Request] = requests
            ) -> httpx.Response:
                requests.append(request)
                return httpx.Response(429 if len(requests) == 1 else 200, content=b"audio")

            async def sleep(seconds: float, delays: list[float] = delays) -> None:
                delays.append(seconds)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch(
                    "speech_sdk._http.RetryTiming",
                    return_value=RetryTiming(random=lambda: 0, sleep=sleep),
                ):
                    model = AzureOpenAIProvider(base_url=ROOT, api_key="key").model("deployment")
                    if budget == 0:
                        with self.assertRaises(ProviderError):
                            await generate_speech(
                                model=model,
                                text="hi",
                                voice="v",
                                http_client=client,
                                max_retries=budget,
                            )
                    else:
                        self.assertEqual(
                            (
                                await generate_speech(
                                    model=model,
                                    text="hi",
                                    voice="v",
                                    http_client=client,
                                    max_retries=budget,
                                )
                            ).audio.data,
                            b"audio",
                        )
            self.assertEqual(len(requests), count)
            self.assertEqual(delays, [] if budget == 0 else [1.0])

    async def test_demand_driven_cancellation_no_replay_and_ownership(self) -> None:
        for owned in (False, True):
            for mode in ("early", "eof", "cancel", "error"):
                await self.streaming_lifecycle(owned, mode)

    async def streaming_lifecycle(self, owned: bool, mode: str) -> None:
        body = GatedBody(httpx.ReadError("private") if mode == "error" else None)
        response = httpx.Response(200, stream=body)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = AzureOpenAIProvider(base_url=ROOT, api_key="key").model("deployment")
        try:
            async with asyncio.timeout(2):
                with (
                    patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
                    patch.object(
                        response, "aread", side_effect=AssertionError("Buffered stream")
                    ) as read,
                ):
                    async with stream_speech(
                        model=model, text="hi", voice="v", http_client=None if owned else client
                    ) as stream:
                        self.assertEqual(body.reads, 0)
                        self.assertEqual(await anext(stream.audio), b"\x00")
                        self.assertEqual(body.reads, 1)
                        self.assertFalse(body.waiting.is_set())
                        if mode != "early":

                            async def next_chunk() -> bytes:
                                return await anext(stream.audio)

                            pending = asyncio.create_task(next_chunk())
                            await body.waiting.wait()
                            self.assertEqual(body.reads, 1)
                            if mode == "cancel":
                                pending.cancel()
                                with self.assertRaises(asyncio.CancelledError):
                                    await pending
                            elif mode == "error":
                                body.release.set()
                                with self.assertRaises(ProviderError) as caught:
                                    await pending
                                self.assertFalse(caught.exception.retryable)
                                self.assertEqual(caught.exception.provider, "azure")
                            else:
                                body.release.set()
                                self.assertEqual(await pending, b"\xff\x01")
                                with self.assertRaises(StopAsyncIteration):
                                    await anext(stream.audio)
                    read.assert_not_called()
                    self.assertEqual(factory.call_count, int(owned))
            self.assertEqual(body.closes, 1)
            self.assertTrue(response.is_closed)
            self.assertEqual(client.is_closed, owned)
            self.assertEqual(len(requests), 1)
        finally:
            body.release.set()
            await client.aclose()

    async def test_owned_buffered_success_error_and_cancel_request(self) -> None:
        model = AzureOpenAIProvider(base_url=ROOT, api_key="key").model("deployment")
        for status in (200, 401):
            response = httpx.Response(status, content=b"audio")
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            )
            with patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory:
                if status == 200:
                    await generate_speech(model=model, text="hi", voice="v")
                else:
                    with self.assertRaises(ProviderError):
                        await generate_speech(model=model, text="hi", voice="v")
                factory.assert_called_once_with()
            self.assertTrue(client.is_closed)
            self.assertTrue(response.is_closed)
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            waiting.set()
            await release.wait()
            return httpx.Response(200)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("speech_sdk._http.httpx.AsyncClient", return_value=client):
            task = asyncio.create_task(generate_speech(model=model, text="hi", voice="v"))
            async with asyncio.timeout(2):
                await waiting.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(client.is_closed)


if __name__ == "__main__":
    unittest.main()
