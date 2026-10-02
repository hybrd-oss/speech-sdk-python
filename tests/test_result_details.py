"""Offline composite inspection contracts, including unread streaming bodies."""

import asyncio
import json
import unittest
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from types import MappingProxyType
from typing import TypedDict
from unittest.mock import patch

import httpx

import speech_sdk
from speech_sdk import (
    AudioData,
    AudioOutput,
    AzureOpenAIProvider,
    OpenAIProvider,
    Pronunciation,
    RequestDetails,
    ResolvedModel,
    ResponseDetails,
    SpeechMetadata,
    SpeechResult,
    SpeechStream,
    StreamMetadata,
    XAIProvider,
    api,
    generate_speech,
    stream_speech,
    types,
)
from speech_sdk._http import RetryTiming
from speech_sdk.pronunciations import Edit, SubstitutionResult, merge_rules, substitute

ALLOWED = (
    "content-type",
    "content-length",
    "request-id",
    "x-request-id",
    "apim-request-id",
    "x-ms-request-id",
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)
DENIED = (
    "Set-Cookie",
    "SETCOOKIE",
    "set_cookie",
    "Cookie",
    "Authorization",
    "Author-ization",
    "author_ization",
    "Api-Key",
    "APIKEY",
    "api_key",
    "X-Unknown",
    "X-Ratelimit-Private",
    "X-Ratelimit-Limit-Images",
)


def models() -> tuple[ResolvedModel, ...]:
    return (
        OpenAIProvider(api_key="config-key", base_url="https://private.invalid/prefix").model(),
        XAIProvider(api_key="config-key", base_url="https://private.invalid/prefix").model(),
        AzureOpenAIProvider(
            api_key="config-key", base_url="https://private.invalid/prefix", api_version="secret v1"
        ).model("deployment"),
    )


class Arguments(TypedDict):
    model: ResolvedModel
    text: str
    voice: str
    pronunciations: Sequence[Pronunciation] | None
    api_key: str
    http_client: httpx.AsyncClient | None
    max_retries: int


class WireArguments(Arguments):
    output: AudioOutput
    instructions: str | None
    provider_options: Mapping[str, object]
    timeout: float
    headers: Mapping[str, str]


async def invoke(
    streaming: bool,
    model: ResolvedModel,
    text: str,
    rules: Sequence[Pronunciation] | None,
    client: httpx.AsyncClient | None,
    *,
    retries: int = 0,
) -> SpeechResult | SpeechStream:
    arguments: Arguments = {
        "model": model,
        "text": text,
        "voice": "voice",
        "pronunciations": rules,
        "api_key": "call-key",
        "http_client": client,
        "max_retries": retries,
    }
    if streaming:
        async with stream_speech(**arguments) as stream:
            return stream
    return await generate_speech(**arguments)


class ControlledBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.iterations = 0
        self.reads = 0
        self.closes = 0
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterations += 1
        self.reads += 1
        yield b"first"
        self.waiting.set()
        await self.release.wait()
        self.reads += 1
        yield b"second"

    async def aclose(self) -> None:
        self.closes += 1


class DetailsTypeTests(unittest.TestCase):
    def test_public_exports_frozen_copied_mapping_and_safe_standalone_repr(self) -> None:
        for name, detail_type in (
            ("RequestDetails", RequestDetails),
            ("ResponseDetails", ResponseDetails),
        ):
            self.assertIs(getattr(types, name), detail_type)
            self.assertIs(getattr(speech_sdk, name), detail_type)
            self.assertIn(name, types.__all__)
            self.assertIn(name, speech_sdk.__all__)
        source = {"request-id": "private-header"}
        response = ResponseDetails(201, source)
        request = RequestDetails("POST", "https://private-url.invalid", b"private-body")
        source["request-id"] = "changed"
        source["new"] = "later"
        self.assertEqual(dict(response.headers), {"request-id": "private-header"})
        self.assertIsInstance(response.headers, MappingProxyType)
        with self.assertRaises(TypeError):
            response.headers["request-id"] = "changed"  # type: ignore[index]
        for detail, name in ((request, "url"), (response, "status_code")):
            with self.assertRaises(FrozenInstanceError):
                setattr(detail, name, "changed")
            self.assertNotIn("private", repr(detail))
        self.assertFalse(hasattr(request, "headers"))
        self.assertFalse(hasattr(response, "content"))

    def test_builder_retains_exact_prepared_bytes_without_parsing_or_body_capture(self) -> None:
        content = b' { "text" : "private", "native" : [1,2] }\n'
        prepared = replace(
            XAIProvider(api_key="private-key").prepare(model_id="grok-tts", text="hi", voice="v"),
            content=content,
        )
        response = httpx.Response(200, content=b"audio", headers={"X-Unknown": "private"})
        request_details, response_details = api._details(prepared, response)
        self.assertIs(request_details.content, prepared.content)
        self.assertEqual(request_details.content, content)
        self.assertEqual(
            [field.name for field in fields(request_details)], ["method", "url", "content"]
        )
        self.assertEqual(
            [field.name for field in fields(response_details)], ["status_code", "headers"]
        )
        self.assertEqual(dict(response_details.headers), {"content-length": "5"})
        self.assertIsNone(prepared.pronunciations)

    def test_old_positional_fields_defaults_and_new_nested_repr(self) -> None:
        async def audio() -> AsyncIterator[bytes]:
            yield b"audio"

        iterator = audio()
        metadata = SpeechMetadata(3, 1.0)
        stream_metadata = StreamMetadata(3, 2.0)
        provider_metadata: Mapping[str, object] = {"old": "visible"}
        result = SpeechResult(AudioData(b"audio", "audio/mpeg"), "xai", "model")
        stream = SpeechStream(iterator, "audio/mpeg", "xai", "model", stream_metadata)
        self.assertEqual(result.metadata, SpeechMetadata())
        for old in (result, stream):
            self.assertIsNone(old.provider_metadata)
            self.assertEqual(old.warnings, ())
            self.assertIsNone(old.request)
            self.assertIsNone(old.response)
            self.assertIsNone(old.pronunciations)
        report = SubstitutionResult("private-final-text", ())
        request = RequestDetails("POST", "private-url", b"private-request")
        response = ResponseDetails(200, {"request-id": "private-header"})
        full_result = SpeechResult(
            result.audio,
            "xai",
            "model",
            metadata,
            provider_metadata,
            ("warning",),
            request,
            response,
            report,
        )
        full_stream = SpeechStream(
            iterator,
            "audio/mpeg",
            "xai",
            "model",
            stream_metadata,
            provider_metadata,
            ("warning",),
            request,
            response,
            report,
        )
        for full in (full_result, full_stream):
            self.assertIs(full.provider_metadata, provider_metadata)
            self.assertEqual(full.warnings, ("warning",))
            self.assertIs(full.request, request)
            self.assertIs(full.response, response)
            self.assertIs(full.pronunciations, report)
            self.assertNotIn("private", repr(full))
            self.assertIn("provider='xai'", repr(full))
            self.assertIn("visible", repr(full))
            self.assertEqual(
                [field.name for field in fields(full)][-3:],
                ["request", "response", "pronunciations"],
            )
            self.assertTrue(all(not field.repr for field in fields(full)[-3:]))
        self.assertIn("audio", repr(full_result))
        self.assertIn("private-final-text", repr(report))


class ResultDetailsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(
                httpx.AsyncHTTPTransport,
                "handle_async_request",
                side_effect=AssertionError("Real network forbidden"),
            )
        )
        self.stack.enter_context(patch("asyncio.sleep", side_effect=AssertionError("Real sleep")))

    async def test_exact_wire_options_auth_and_all_response_headers_both_modes(self) -> None:
        for model in models():
            for streaming in (False, True):
                with self.subTest(provider=model.provider.name, streaming=streaming):
                    await self.wire_case(model, streaming)

    async def wire_case(self, model: ResolvedModel, streaming: bool) -> None:
        xai = model.provider.name == "xai"
        azure = model.provider.name == "azure"
        options: dict[str, object] = {"speed": 1.1}
        if not xai:
            options.update(
                model="ignored", input="ignored", voice="ignored", instructions="private-native"
            )
        before = options.copy()
        text = "🙂 LLM Apple [pause]"
        rules = [Pronunciation("LLM", "el el em"), Pronunciation("Apple", "A")]
        report = substitute(text, merge_rules(rules))
        prepared = model.provider.prepare(
            model_id=model.model_id,
            text=report.text,
            voice=" voice ",
            output=AudioOutput("wav"),
            instructions=None if xai else "private-canonical",
            provider_options=options,
            api_key="call-key",
            timeout=7,
            max_retries=0,
            headers={"X-Custom": "kept"},
        )
        self.assertIsNone(prepared.pronunciations)
        selected = {name: f"private-{index}" for index, name in enumerate(ALLOWED)}
        selected.update({"content-type": "audio/wav", "content-length": "5"})
        headers = {name.upper(): value for name, value in selected.items()}
        headers.update(dict.fromkeys(DENIED, "credential-cookie-secret"))
        response = httpx.Response(201, content=b"audio", headers=headers)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.content, prepared.content)
            self.assertEqual(str(request.url), prepared.url)
            self.assertEqual(request.method, "POST")
            self.assertEqual(
                request.headers["api-key" if azure else "authorization"],
                "call-key" if azure else "Bearer call-key",
            )
            self.assertNotIn("authorization" if azure else "api-key", request.headers)
            self.assertEqual(request.headers["x-custom"], "kept")
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("x-client", request.headers)
            self.assertEqual(request.extensions["timeout"], httpx.Timeout(7).as_dict())
            return response

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            cookies={"private": "cookie"},
            auth=("bad", "bad"),
            headers={"X-Client": "ignored"},
        ) as client:
            arguments: WireArguments = {
                "model": model,
                "text": text,
                "voice": " voice ",
                "output": AudioOutput("wav"),
                "instructions": None if xai else "private-canonical",
                "pronunciations": rules,
                "provider_options": options,
                "api_key": "call-key",
                "http_client": client,
                "timeout": 7.0,
                "max_retries": 0,
                "headers": {"X-Custom": "kept"},
            }
            result: SpeechResult | SpeechStream
            if streaming:
                async with stream_speech(**arguments) as stream:
                    self.assertEqual(b"".join([chunk async for chunk in stream.audio]), b"audio")
                    result = stream
            else:
                buffered = await generate_speech(**arguments)
                self.assertEqual(buffered.audio.data, b"audio")
                result = buffered
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), 1)
        self.assertEqual(options, before)
        self.assert_wire_details(
            result, prepared.url, prepared.content, response, selected, report, text
        )
        if result.request is None:
            self.fail("Missing request")
        self.assert_wire_body(result.request.content, model, report)

    def assert_wire_details(
        self,
        result: SpeechResult | SpeechStream,
        url: str,
        content: bytes,
        response: httpx.Response,
        selected: Mapping[str, str],
        report: SubstitutionResult,
        text: str,
    ) -> None:
        self.assertIsNotNone(result.request)
        self.assertIsNotNone(result.response)
        if result.request is None or result.response is None:
            self.fail("Successful result missing details")
        self.assertEqual(result.request, RequestDetails("POST", url, content))
        self.assertIsInstance(result.request.content, bytes)
        self.assertEqual(result.response.status_code, 201)
        self.assertEqual(dict(result.response.headers), selected)
        response.headers["request-id"] = "mutated"
        response.headers["x-new"] = "later"
        self.assertEqual(dict(result.response.headers), selected)
        self.assertEqual(result.pronunciations, report)
        self.assertEqual(
            report.edits,
            (
                Edit((2, 5), (2, 10), "LLM", "llm"),
                Edit((6, 11), (11, 12), "Apple", "apple"),
            ),
        )
        self.assertEqual(result.metadata.input_chars, len(text))
        self.assertIsNone(result.provider_metadata)
        self.assertEqual(result.warnings, ())
        for value in (result, result.request, result.response):
            for private in ("private", "call-key", "credential-cookie-secret", "LLM", "el el em"):
                self.assertNotIn(private, repr(value))

    def assert_wire_body(
        self, content: bytes, model: ResolvedModel, report: SubstitutionResult
    ) -> None:
        xai = model.provider.name == "xai"
        body = json.loads(content)
        self.assertEqual(body["text" if xai else "input"], report.text)
        self.assertEqual(body["voice_id" if xai else "voice"], " voice ")
        self.assertEqual(body["speed"], 1.1)
        if not xai:
            self.assertEqual(body["model"], model.model_id)
            self.assertEqual(body["instructions"], "private-canonical\n\nprivate-native")
            self.assertEqual(body["response_format"], "wav")
        else:
            self.assertNotIn("model", body)
            self.assertEqual(body["output_format"], {"codec": "wav", "sample_rate": 24000})

    async def test_omitted_empty_blank_miss_and_match_reports(self) -> None:
        text = "🙂 LLM e\u0301 [pause]"
        cases: tuple[Sequence[Pronunciation] | None, ...] = (
            None,
            [],
            [Pronunciation(" ", "skip"), Pronunciation("LLM", "\n")],
            [Pronunciation("absent", "unused")],
            [Pronunciation("LLM", "el"), Pronunciation("el", "not-chained")],
        )
        for model in models():
            for streaming in (False, True):
                for rules in cases:
                    with self.subTest(
                        provider=model.provider.name,
                        streaming=streaming,
                        supplied=rules is not None,
                    ):
                        requests: list[httpx.Request] = []

                        def handler(
                            request: httpx.Request, requests: list[httpx.Request] = requests
                        ) -> httpx.Response:
                            requests.append(request)
                            return httpx.Response(200, content=b"audio")

                        expected = None if rules is None else substitute(text, merge_rules(rules))
                        async with httpx.AsyncClient(
                            transport=httpx.MockTransport(handler)
                        ) as client:
                            result = await invoke(streaming, model, text, rules, client)
                        self.assertEqual(result.pronunciations, expected)
                        self.assertEqual(result.metadata.input_chars, len(text))
                        self.assertEqual(len(requests), 1)
                        self.assertIsNotNone(result.request)
                        if result.request is None:
                            self.fail("Missing prepared bytes")
                        self.assertEqual(
                            json.loads(result.request.content)[
                                "text" if model.provider.name == "xai" else "input"
                            ],
                            text if expected is None else expected.text,
                        )
                        if expected is not None:
                            self.assertIsInstance(expected.edits, tuple)
                            for edit in expected.edits:
                                self.assertEqual(
                                    text[slice(*edit.original_range)], edit.original_word
                                )

    async def test_retry_final_response_once_only_matcher_and_prepared_bytes(self) -> None:
        for model in models():
            for streaming in (False, True):
                with self.subTest(provider=model.provider.name, streaming=streaming):
                    requests: list[httpx.Request] = []
                    responses: list[httpx.Response] = []
                    delays: list[float] = []

                    async def sleep(seconds: float, delays: list[float] = delays) -> None:
                        delays.append(seconds)

                    def handler(
                        request: httpx.Request,
                        requests: list[httpx.Request] = requests,
                        responses: list[httpx.Response] = responses,
                    ) -> httpx.Response:
                        requests.append(request)
                        final = len(requests) == 2
                        response = httpx.Response(
                            202 if final else 429,
                            content=b"audio" if final else b"failed",
                            headers={
                                "X-Request-ID": "final-private" if final else "old-private",
                                "X-Unknown": "denied",
                            },
                        )
                        responses.append(response)
                        return response

                    rules = [Pronunciation("LLM", "el"), Pronunciation("el", "second")]
                    expected = substitute("LLM", merge_rules(rules))
                    reports: list[SubstitutionResult] = []

                    def retain(
                        text: str,
                        rule_map: Mapping[str, Pronunciation],
                        reports: list[SubstitutionResult] = reports,
                    ) -> SubstitutionResult:
                        report = substitute(text, rule_map)
                        reports.append(report)
                        return report

                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        with (
                            patch.object(api, "substitute", side_effect=retain) as matcher,
                            patch.object(api, "merge_rules", wraps=merge_rules) as merge,
                            patch(
                                "speech_sdk._http.RetryTiming",
                                return_value=RetryTiming(
                                    random=lambda: 0,
                                    sleep=sleep,
                                ),
                            ),
                        ):
                            result = await invoke(streaming, model, "LLM", rules, client, retries=1)
                        matcher.assert_called_once()
                        merge.assert_called_once_with(rules)
                        self.assertIs(result.pronunciations, reports[0])
                        self.assertFalse(client.is_closed)
                    self.assertEqual(delays, [1])
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(requests[0].content, requests[1].content)
                    self.assertEqual(result.pronunciations, expected)
                    if result.request is None or result.response is None:
                        self.fail("Missing final snapshots")
                    self.assertEqual(result.request.content, requests[-1].content)
                    self.assertEqual(result.response.status_code, 202)
                    self.assertEqual(
                        dict(result.response.headers),
                        {"content-length": "5", "x-request-id": "final-private"},
                    )
                    self.assertTrue(all(response.is_closed for response in responses))

    async def test_final_limits_and_original_counts_with_retained_report(self) -> None:
        for model in models():
            limit = 60000 if model.provider.name == "xai" else 4096
            for streaming in (False, True):
                with self.subTest(provider=model.provider.name, streaming=streaming):
                    requests: list[httpx.Request] = []

                    def handler(
                        request: httpx.Request, requests: list[httpx.Request] = requests
                    ) -> httpx.Response:
                        requests.append(request)
                        return httpx.Response(200, content=b"audio")

                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        result = await invoke(
                            streaming, model, "a", [Pronunciation("a", "🙂" * limit)], client
                        )
                        self.assertEqual(
                            result.pronunciations,
                            SubstitutionResult("🙂" * limit, (Edit((0, 1), (0, limit), "a", "a"),)),
                        )
                        self.assertEqual(result.metadata.input_chars, 1)
                        with self.assertRaises(ValueError):
                            await invoke(
                                streaming,
                                model,
                                "a",
                                [Pronunciation("a", "🙂" * (limit + 1))],
                                client,
                            )
                        original = "a" * (limit + 1)
                        contracted = await invoke(
                            streaming, model, original, [Pronunciation(original, "short")], client
                        )
                        self.assertEqual(contracted.metadata.input_chars, limit + 1)
                        self.assertEqual(
                            contracted.pronunciations,
                            SubstitutionResult(
                                "short",
                                (
                                    Edit(
                                        (0, limit + 1),
                                        (0, 5),
                                        original,
                                        original,
                                    ),
                                ),
                            ),
                        )
                    self.assertEqual(len(requests), 2)

    async def test_stream_zero_iteration_inspection_backpressure_postclose_and_ownership(
        self,
    ) -> None:
        for model in models():
            for owned in (False, True):
                with self.subTest(provider=model.provider.name, owned=owned):
                    await self.stream_case(model, owned)

    async def stream_case(self, model: ResolvedModel, owned: bool) -> None:
        body = ControlledBody()
        response = httpx.Response(200, stream=body, headers={"X-Request-ID": "private-id"})
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
                patch.object(client, "aclose", wraps=client.aclose) as close,
                patch.object(response, "aread", side_effect=AssertionError("Prefetch")) as read,
            ):
                async with stream_speech(
                    model=model,
                    text="LLM",
                    voice="v",
                    api_key="call-key",
                    pronunciations=[Pronunciation("LLM", "el")],
                    http_client=None if owned else client,
                ) as stream:
                    self.assertEqual((body.iterations, body.reads), (0, 0))
                    snapshots = (stream.request, stream.response, stream.pronunciations)
                    self.assertEqual(
                        stream.pronunciations,
                        SubstitutionResult("el", (Edit((0, 3), (0, 2), "LLM", "llm"),)),
                    )
                    if stream.request is None or stream.response is None:
                        self.fail("Details not available at context entry")
                    self.assertEqual(stream.request.content, requests[0].content)
                    self.assertEqual(dict(stream.response.headers), {"x-request-id": "private-id"})
                    self.assertNotIn("private-id", repr(stream))
                    self.assertEqual((body.iterations, body.reads), (0, 0))
                    self.assertEqual(await anext(stream.audio), b"first")
                    self.assertEqual(body.reads, 1)
                    self.assertFalse(body.waiting.is_set())
                self.assertEqual(
                    (stream.request, stream.response, stream.pronunciations), snapshots
                )
                self.assertEqual(body.reads, 1)
                with self.assertRaisesRegex(RuntimeError, "context"):
                    await anext(stream.audio)
                read.assert_not_called()
                self.assertEqual(factory.call_count, int(owned))
                self.assertEqual(close.await_count, int(owned))
            self.assertEqual(client.is_closed, owned)
            self.assertTrue(response.is_closed)
            self.assertEqual(body.closes, 1)
            self.assertEqual(len(requests), 1)
        finally:
            body.release.set()
            await client.aclose()

    async def test_buffered_owned_client_cleanup_and_postclose_details(self) -> None:
        for model in models():
            response = httpx.Response(200, content=b"audio", headers={"request-id": "private-id"})
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            )
            with (
                patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
                patch.object(client, "aclose", wraps=client.aclose) as close,
            ):
                result = await invoke(False, model, "LLM", None, None)
                factory.assert_called_once_with()
                close.assert_awaited_once_with()
            self.assertTrue(client.is_closed)
            self.assertTrue(response.is_closed)
            if result.request is None or result.response is None:
                self.fail("Snapshots lost at close")
            self.assertEqual(result.response.headers["request-id"], "private-id")
            self.assertIsNone(result.pronunciations)

    async def test_sdk_prepared_snapshot_not_mutating_hook_capture(self) -> None:
        async def mutate(request: httpx.Request) -> None:
            request.url = httpx.URL("https://hook.invalid/changed")
            request.headers["X-Hook"] = "private"

        for streaming in (False, True):
            seen: list[httpx.Request] = []

            def handler(request: httpx.Request, seen: list[httpx.Request] = seen) -> httpx.Response:
                seen.append(request)
                return httpx.Response(200, content=b"audio")

            model = models()[0]
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                event_hooks={"request": [mutate]},
            ) as client:
                result = await invoke(streaming, model, "private-text", None, client)
            self.assertEqual(str(seen[0].url), "https://hook.invalid/changed")
            if result.request is None:
                self.fail("Missing prepared request")
            self.assertEqual(result.request.url, "https://private.invalid/prefix/audio/speech")
            self.assertFalse(hasattr(result.request, "headers"))
