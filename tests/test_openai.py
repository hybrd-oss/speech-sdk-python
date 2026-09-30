"""Offline contracts for the request-only OpenAI adapter."""

import copy
import json
import unittest
from collections.abc import Mapping
from typing import Literal, cast
from unittest.mock import patch

import httpx

from speech_sdk._http import RetryTiming, buffered_audio
from speech_sdk.errors import MissingApiKeyError, NoSpeechGeneratedError, ProviderError
from speech_sdk.providers.openai import OpenAIProvider
from speech_sdk.types import AudioData, AudioOutput, PreparedRequest, Provider


class OpenAITests(unittest.TestCase):
    def prepare(
        self,
        *,
        provider: OpenAIProvider | None = None,
        model: str = "gpt-4o-mini-tts",
        text: str = "Hello from Python!",
        voice: str = "alloy",
        output: AudioOutput | None = None,
        instructions: str | None = None,
        options: Mapping[str, object] | None = None,
        key: str | None = "offline-call-key",
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout = 60.0,
        retries: int = 2,
    ) -> PreparedRequest:
        with patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("network")):
            return (provider or OpenAIProvider()).prepare(
                model_id=model,
                text=text,
                voice=voice,
                output=output,
                instructions=instructions,
                provider_options=options,
                api_key=key,
                headers=headers,
                timeout=timeout,
                max_retries=retries,
            )

    def body(self, request: PreparedRequest) -> dict[str, object]:
        return cast(dict[str, object], json.loads(request.content))

    def test_defaults_and_three_models(self) -> None:
        provider: Provider = OpenAIProvider(api_key="offline-config-key")
        self.assertEqual(provider.name, "openai")
        self.assertEqual(provider.model().model_id, "gpt-4o-mini-tts")
        for model in ("gpt-4o-mini-tts", "tts-1", "tts-1-hd"):
            with self.subTest(model=model):
                resolved = provider.model(model)
                self.assertIs(resolved.provider, provider)
                request = self.prepare(model=model)
                self.assertEqual(request.url, "https://api.openai.com/v1/audio/speech")
                self.assertEqual(request.provider, "openai")
                self.assertEqual(request.model, model)
                self.assertEqual(
                    self.body(request),
                    {
                        "model": model,
                        "input": "Hello from Python!",
                        "voice": "alloy",
                        "stream_format": "audio",
                    },
                )
                self.assertEqual(
                    request.headers,
                    {
                        "user-agent": "HYBRD/speech-sdk-python",
                        "authorization": "Bearer offline-call-key",
                        "content-type": "application/json",
                    },
                )
                self.assertEqual(request.media_type, "audio/mpeg")
                self.assertEqual(request.input_chars, 18)
                self.assertEqual(request.max_retries, 2)
                self.assertEqual(request.timeout.as_dict(), httpx.Timeout(60).as_dict())

    def test_bad_models(self) -> None:
        for model in (
            "",
            " \n\t",
            cast(str, 1),
            cast(str, True),
            cast(str, []),
            cast(str, {}),
        ):
            with self.subTest(model=model), self.assertRaises((ValueError, TypeError)):
                OpenAIProvider().model(model)
            with self.subTest(prepare=model), self.assertRaises((ValueError, TypeError)):
                self.prepare(model=model)

    def test_model_subclasses_rejected_before_client(self) -> None:
        class Overridden(str):
            def strip(self, _chars: str | None = None, /) -> str:
                return "nonblank"

            def partition(self, _sep: str, /) -> tuple[str, str, str]:
                return ("openai", "/", "rewritten")

        with patch("speech_sdk._http.httpx.AsyncClient") as factory:
            for model in (
                Overridden(" \n\t"),
                Overridden("unknown/original"),
                Overridden("future"),
            ):
                with self.subTest(model=model, entry="model"), self.assertRaises(TypeError):
                    OpenAIProvider().model(model)
                with self.subTest(model=model, entry="prepare"), self.assertRaises(TypeError):
                    self.prepare(model=model)
            factory.assert_not_called()

    def test_generic_models_preserved_with_native_options(self) -> None:
        for model in (
            "unknown",
            "openai/tts-1",
            "tts-1/",
            "gpt-4o-mini-tts-2025-12-15",
            "org/namespace/future-tts",
            " future\nmodel ",
            "/",
            " \t/ \n",
        ):
            with self.subTest(model=model):
                self.assertEqual(OpenAIProvider().model(model).model_id, model)
                request = self.prepare(
                    model=model,
                    instructions=" canonical ",
                    options={"model": "ignored", "instructions": " native ", "speed": 1.5},
                )
                self.assertEqual(request.model, model)
                self.assertEqual(
                    self.body(request),
                    {
                        "model": model,
                        "input": "Hello from Python!",
                        "voice": "alloy",
                        "instructions": " canonical \n\n native ",
                        "speed": 1.5,
                        "stream_format": "audio",
                    },
                )
        with self.assertRaises(TypeError):
            self.prepare(model=cast(str, None))

    def test_url_headers_and_shared_settings(self) -> None:
        headers = {"Authorization": "ignored", "CONTENT-TYPE": "ignored", "X-Trace": "fixture"}
        before = headers.copy()
        timeout = httpx.Timeout(connect=1, read=2, write=3, pool=4)
        request = self.prepare(
            provider=OpenAIProvider(base_url="https://example.test/custom/v1///"),
            headers=headers,
            timeout=timeout,
            retries=0,
        )
        self.assertEqual(request.url, "https://example.test/custom/v1/audio/speech")
        self.assertEqual(
            request.headers,
            {
                "user-agent": "HYBRD/speech-sdk-python",
                "authorization": "Bearer offline-call-key",
                "content-type": "application/json",
                "x-trace": "fixture",
            },
        )
        self.assertEqual(headers, before)
        self.assertEqual(request.timeout.as_dict(), timeout.as_dict())
        self.assertIsNot(request.timeout, timeout)
        self.assertEqual(request.max_retries, 0)
        for url in (
            "",
            "ftp://example.test",
            "https://user:pass@example.test",
            "https://example.test?x=1",
            "https://example.test#frag",
            "https://example.test/ bad",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAIProvider(base_url=url)
        for name in ("Host", "Content-Length", "Transfer-Encoding", "bad header"):
            with self.subTest(header=name), self.assertRaises(ValueError):
                self.prepare(headers={name: "value"})
        with self.assertRaises(ValueError):
            self.prepare(headers={"X-Trace": "bad\nvalue"})
        for timeout_value in (
            0,
            -1,
            float("inf"),
            float("nan"),
            cast(float, True),
            httpx.Timeout(None),
        ):
            with self.subTest(timeout=timeout_value), self.assertRaises((ValueError, TypeError)):
                self.prepare(timeout=timeout_value)
        for retries in (-1, cast(int, True), cast(int, 1.5)):
            with self.subTest(retries=retries), self.assertRaises((ValueError, TypeError)):
                self.prepare(retries=retries)

    def test_key_precedence_and_secret_repr(self) -> None:
        provider = OpenAIProvider(api_key="offline-config-key")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "offline-env-key"}, clear=True):
            self.assertEqual(
                self.prepare(provider=provider).headers["authorization"], "Bearer offline-call-key"
            )
            self.assertEqual(
                self.prepare(provider=provider, key=None).headers["authorization"],
                "Bearer offline-config-key",
            )
            self.assertEqual(
                self.prepare(key=None).headers["authorization"], "Bearer offline-env-key"
            )
            for key in ("", " "):
                with self.assertRaises(MissingApiKeyError):
                    self.prepare(provider=provider, key=key)
                with self.assertRaises(MissingApiKeyError):
                    self.prepare(provider=OpenAIProvider(api_key=key), key=None)
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(MissingApiKeyError):
            self.prepare(key=None)
        for key in ("bad\nkey", "bad key", "非ascii"):
            with self.assertRaises(ValueError) as caught:
                self.prepare(key=key)
            self.assertNotIn(key, str(caught.exception))
        request = self.prepare(
            provider=provider, text="private input", headers={"X-Secret": "private header"}
        )
        for representation in (repr(provider), repr(provider.model()), repr(request)):
            for secret in (
                "offline-config-key",
                "offline-call-key",
                "private input",
                "private header",
            ):
                self.assertNotIn(secret, representation)

    def test_verbatim_text_voice_and_unicode_limit(self) -> None:
        text = " \n[whisper] Héllo 😀!  "
        request = self.prepare(text=text, voice=" custom-voice ")
        self.assertEqual(self.body(request)["input"], text)
        self.assertEqual(self.body(request)["voice"], " custom-voice ")
        self.assertNotIn("instructions", self.body(request))
        for text in ("a" * 4096, "😀" * 4096):
            self.assertEqual(self.prepare(text=text).input_chars, 4096)
            with self.assertRaises(ValueError):
                self.prepare(text=text + "x")
        for text in ("", " \n\t"):
            with self.assertRaises(NoSpeechGeneratedError):
                self.prepare(text=text)
        for voice in ("", " \t"):
            with self.assertRaises(ValueError):
                self.prepare(voice=voice)
        with self.assertRaises(TypeError):
            self.prepare(text=cast(str, 1))
        with self.assertRaises(TypeError):
            self.prepare(voice=cast(str, {"id": "voice_123"}))

    def test_instructions(self) -> None:
        value: object
        request = self.prepare(instructions=" canonical ", options={"instructions": " native "})
        self.assertEqual(self.body(request)["instructions"], " canonical \n\n native ")
        for model in ("gpt-4o-mini-tts", "tts-1", "tts-1-hd"):
            self.assertNotIn(
                "instructions",
                self.body(
                    self.prepare(model=model, instructions=" \n", options={"instructions": "\t"})
                ),
            )
        self.assertEqual(
            self.body(self.prepare(instructions="a" * 4096))["instructions"], "a" * 4096
        )
        self.assertEqual(
            len(
                cast(
                    str,
                    self.body(
                        self.prepare(instructions="a" * 2047, options={"instructions": "b" * 2047})
                    )["instructions"],
                )
            ),
            4096,
        )
        with self.assertRaises(ValueError):
            self.prepare(instructions="a" * 2048, options={"instructions": "b" * 2047})
        for model in ("tts-1", "tts-1-hd"):
            with self.assertRaises(ValueError):
                self.prepare(model=model, instructions="talk softly")
            with self.assertRaises(ValueError):
                self.prepare(model=model, options={"instructions": "talk softly"})
        for value in (1, False, [], None):
            with self.subTest(native=value), self.assertRaises(TypeError):
                self.prepare(options={"instructions": value})
        with self.assertRaises(TypeError):
            self.prepare(instructions=cast(str, 1))
        self.assertEqual(
            self.body(self.prepare(options={"instructions": "😀" * 4096}))["instructions"],
            "😀" * 4096,
        )
        with self.assertRaises(ValueError):
            self.prepare(options={"instructions": "😀" * 4097})

    def test_formats_rates_and_precedence(self) -> None:
        value: object
        formats = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "pcm": "audio/pcm;rate=24000",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
        }
        for format_name, mime in formats.items():
            with self.subTest(format=format_name):
                request = self.prepare(options={"response_format": format_name})
                self.assertEqual(request.media_type, mime)
                self.assertEqual(self.body(request)["response_format"], format_name)
        for format_name in ("mp3", "wav", "pcm"):
            for rate in (None, 24000):
                output = AudioOutput(
                    format=cast(Literal["mp3", "wav", "pcm"], format_name), sample_rate=rate
                )
                request = self.prepare(
                    output=output, options={"response_format": "opus", "sample_rate": 24000}
                )
                self.assertEqual(request.media_type, formats[format_name])
                self.assertEqual(self.body(request)["response_format"], format_name)
                self.assertNotIn("sample_rate", self.body(request))
            with self.assertRaises(ValueError):
                self.prepare(
                    output=AudioOutput(
                        format=cast(Literal["mp3", "wav", "pcm"], format_name), sample_rate=48000
                    )
                )
        for value in ("unknown", "sse", "MP3", None, 1, {}, True):
            with self.subTest(format=value), self.assertRaises((TypeError, ValueError)):
                self.prepare(options={"response_format": value})
            with self.subTest(overridden=value), self.assertRaises((TypeError, ValueError)):
                self.prepare(output=AudioOutput(), options={"response_format": value})
        with self.assertRaises(TypeError):
            self.prepare(output=cast(AudioOutput, {"format": "wav"}))
        self.assertNotIn("sample_rate", self.body(self.prepare(options={"sample_rate": 24000})))
        for value in (8000, 48000, 0, -1, 24000.0, True, "24000"):
            with self.subTest(rate=value), self.assertRaises((TypeError, ValueError)):
                self.prepare(options={"sample_rate": value})

    def test_native_options_json_speed_and_protocol(self) -> None:
        speed: object
        value: object
        options: dict[str, object] = {
            "model": "wrong",
            "input": "wrong",
            "voice": {"id": "wrong"},
            "speed": 1,
            "future_option": {"items": [1, True, None, "value"]},
        }
        before = copy.deepcopy(options)
        request = self.prepare(options=options)
        self.assertEqual(options, before)
        expected = {
            **before,
            "model": "gpt-4o-mini-tts",
            "input": "Hello from Python!",
            "voice": "alloy",
            "stream_format": "audio",
        }
        self.assertEqual(self.body(request), expected)
        cast(dict[str, object], options["future_option"])["items"] = []
        self.assertEqual(self.body(request), expected)
        for speed in (0.25, 4, 1.5):
            self.assertEqual(self.body(self.prepare(options={"speed": speed}))["speed"], speed)
        for speed in (0.24, 4.01, 10**1000, float("nan"), float("inf"), True, "1", None, []):
            with self.subTest(speed=speed), self.assertRaises((TypeError, ValueError)):
                self.prepare(options={"speed": speed})
        self.assertEqual(
            self.body(self.prepare(options={"stream_format": "audio"}))["stream_format"], "audio"
        )
        for value in ("sse", True, None, {}, "Audio"):
            with self.subTest(stream=value), self.assertRaises((TypeError, ValueError)):
                self.prepare(options={"stream_format": value})
        for field in (
            "stream",
            "response_mode",
            "output_format",
            "headers",
            "authorization",
            "api_key",
            "base_url",
            "Response_Format",
            "streamFormat",
            "sampleRate",
            "Voice",
            "input ",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(options={field: "masquerade"})
        for value in (object(), {1: "bad"}, [float("nan")], {"bad": float("inf")}):
            with self.subTest(value=value), self.assertRaises(TypeError):
                self.prepare(options={"future_option": value})
        with self.assertRaises(TypeError):
            self.prepare(options=cast(Mapping[str, object], []))


class OpenAIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_post_binary_and_mime_mapping(self) -> None:
        payload = b"\x00\xff\x80fixture\x01"
        for format_name, mime in {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "pcm": "audio/pcm;rate=24000",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
        }.items():
            with self.subTest(format=format_name):
                prepared = OpenAITests().prepare(options={"response_format": format_name})
                requests: list[httpx.Request] = []

                def respond(
                    request: httpx.Request, recorded: list[httpx.Request] = requests
                ) -> httpx.Response:
                    recorded.append(request)
                    return httpx.Response(200, content=payload)

                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    self.assertEqual(
                        await buffered_audio(prepared, client=client), AudioData(payload, mime)
                    )
                    self.assertFalse(client.is_closed)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].method, "POST")
                self.assertEqual(str(requests[0].url), prepared.url)
                self.assertEqual(requests[0].content, prepared.content)
                for name, value in prepared.headers.items():
                    self.assertEqual(requests[0].headers[name], value)

    async def test_terminal_and_retryable_statuses(self) -> None:
        for status, attempts in ((401, 1), (429, 3), (503, 3), (501, 1)):
            requests: list[httpx.Request] = []
            delays: list[float] = []

            def respond(
                request: httpx.Request, recorded: list[httpx.Request] = requests, code: int = status
            ) -> httpx.Response:
                recorded.append(request)
                return httpx.Response(code, json={"error": {"message": "private input"}})

            async def sleep(delay: float, recorded: list[float] = delays) -> None:
                recorded.append(delay)

            timing = RetryTiming(sleep=sleep, random=lambda: 0.0)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises(ProviderError) as caught:
                    await buffered_audio(OpenAITests().prepare(), client=client, timing=timing)
                self.assertFalse(client.is_closed)
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn("private input", repr(caught.exception))
            self.assertEqual(len(requests), attempts)
            self.assertEqual(len(delays), attempts - 1)

    async def test_contract_mime_terminal(self) -> None:
        for mime in ("text/event-stream", "application/json", "text/html", "audio/wav"):
            requests: list[httpx.Request] = []

            def respond(
                request: httpx.Request,
                recorded: list[httpx.Request] = requests,
                content_type: str = mime,
            ) -> httpx.Response:
                recorded.append(request)
                return httpx.Response(
                    200, content=b"not mp3", headers={"content-type": content_type}
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises(ProviderError) as caught:
                    await buffered_audio(OpenAITests().prepare(), client=client)
                self.assertFalse(client.is_closed)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
