"""Pinned Jellypod golden cases and offline Python pronunciation contracts."""

import asyncio
import json
import unittest
from collections import UserList
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import FrozenInstanceError
from typing import Literal, TypedDict, cast
from unittest.mock import patch

import httpx

from speech_sdk import (
    AudioOutput,
    AzureOpenAIProvider,
    NoSpeechGeneratedError,
    Pronunciation,
    ProviderError,
    ResolvedModel,
    generate_speech,
    stream_speech,
)
from speech_sdk._http import RetryTiming
from speech_sdk.pronunciations import (
    Edit,
    PronunciationMatcher,
    SubstitutionResult,
    merge_rules,
    rule_map_key,
    substitute,
)


class StringSubclass(str):
    pass


class RuleSubclass(Pronunciation):
    pass


class PronunciationTests(unittest.TestCase):
    def test_normalization_duplicates_order_and_fresh_snapshot(self) -> None:
        source = UserList(
            [
                Pronunciation(" LLM ", " first "),
                Pronunciation(" New  York ", " noo  YORK "),
                Pronunciation("llm", "second", True),
                Pronunciation("LLM", "exact", True),
                Pronunciation("\u2003", "skip"),
                Pronunciation("x", "\n"),
            ]
        )
        before = tuple(source)
        merged = merge_rules(source)
        self.assertEqual(list(merged), ["llm", "new  york", "LLM"])
        self.assertEqual(
            list(merged.values()),
            [
                Pronunciation("llm", "second", True),
                Pronunciation("New  York", "noo  YORK"),
                Pronunciation("LLM", "exact", True),
            ],
        )
        self.assertEqual(tuple(source), before)
        self.assertIsNot(merged["llm"], source[2])
        source.clear()
        self.assertEqual(len(merged), 3)
        self.assertEqual(merge_rules([]), {})
        self.assertEqual(rule_map_key(" LLM ", False), " llm ")
        self.assertEqual(rule_map_key(" LLM ", True), " LLM ")
        with self.assertRaises(FrozenInstanceError):
            merged["llm"].word = "changed"  # type: ignore[misc]

    def test_literal_longest_unicode_and_single_pass_golden_table(self) -> None:
        cases = [
            ("empty", "", [], ""),
            ("empty map", "Hello, world.", [], "Hello, world."),
            (
                "whole words",
                "hello belt café señor el",
                [Pronunciation("el", "X"), Pronunciation("caf", "X"), Pronunciation("señ", "X")],
                "hello belt café señor X",
            ),
            (
                "longest phrase",
                "New York and New",
                [Pronunciation("New", "N"), Pronunciation("New York", "NY")],
                "NY and N",
            ),
            (
                "exact case",
                "Apple apple APPLE",
                [Pronunciation("Apple", "fruit", True)],
                "fruit apple APPLE",
            ),
            (
                "default lower",
                "an llm and an LLM",
                [Pronunciation("LLM", "ell em")],
                "an ell em and an ell em",
            ),
            ("word gaps", "a² a_ a9 éa aé a", [Pronunciation("a", "X")], "a² a_ a9 éa aé X"),
            (
                "literal punctuation gaps",
                "C++ C++x xC++ [bracket]x x[bracket]",
                [Pronunciation("C++", "cee"), Pronunciation("[bracket]", "B")],
                "cee ceex xC++ Bx xB",
            ),
            (
                "internal whitespace",
                "New  York\nNew York",
                [Pronunciation("New  York", "N")],
                "N\nNew York",
            ),
            (
                "no chaining",
                "LLM el",
                [Pronunciation("LLM", "el"), Pronunciation("el", "X")],
                "el X",
            ),
            ("lower not casefold", "ß SS ss", [Pronunciation("ß", "eszett")], "eszett SS ss"),
            ("equal-length sigma", "ς \u03c3 Σ", [Pronunciation("\u03c3", "X")], "ς X X"),
            (
                "fixed length lower expansion",
                "İ i i\u0307",
                [Pronunciation("İ", "X")],
                "X i i\u0307",
            ),
            ("no NFC or graphemes", "é e\u0301", [Pronunciation("e", "X")], "é X\u0301"),
            (
                "tags remain unless explicit",
                "LLM [pause]",
                [Pronunciation("LLM", "el")],
                "el [pause]",
            ),
        ]
        for name, text, rules, expected in cases:
            with self.subTest(name=name):
                result = substitute(text, merge_rules(rules))
                self.assertEqual(result.text, expected)
                self.assertIsInstance(result.edits, tuple)
                for edit in result.edits:
                    self.assertEqual(text[slice(*edit.original_range)], edit.original_word)
                    self.assertIsInstance(edit.original_range, tuple)
                    self.assertIsInstance(edit.replacement_range, tuple)

    def test_exact_codepoint_edits_expansion_contraction_and_immutable_result(self) -> None:
        result = substitute(
            "🙂 LLM and Apple e\u0301 LLM",
            merge_rules(
                [
                    Pronunciation("LLM", "el el em"),
                    Pronunciation("Apple", "A", True),
                ]
            ),
        )
        self.assertEqual(
            result,
            SubstitutionResult(
                "🙂 el el em and A e\u0301 el el em",
                (
                    Edit((2, 5), (2, 10), "LLM", "llm"),
                    Edit((10, 15), (15, 16), "Apple", "Apple"),
                    Edit((19, 22), (20, 28), "LLM", "llm"),
                ),
            ),
        )
        for value, field in ((result, "text"), (result.edits[0], "original_word")):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(value, field, "changed")
        self.assertEqual(substitute("\n", {}), SubstitutionResult("\n", ()))

    def test_manual_map_keys_ignored_stable_ties_and_snapshot(self) -> None:
        first = Pronunciation("LLM", "first", True)
        second = Pronunciation("llm", "second")
        for values, expected in (([first, second], "first"), ([second, first], "second")):
            with self.subTest(first=values[0].case_sensitive):
                manual = {object(): values[0], object(): values[1]}
                before = tuple(manual.items())
                result = substitute("LLM", cast(Mapping[str, Pronunciation], manual))
                self.assertEqual(result.text, expected)
                self.assertEqual(
                    result.edits[0].rule_key, rule_map_key(values[0].word, values[0].case_sensitive)
                )
                self.assertEqual(tuple(manual.items()), before)
                manual.clear()
                self.assertEqual(result.text, expected)

    def test_container_snapshots_precede_rule_processing(self) -> None:
        source = [Pronunciation("a", "A"), Pronunciation("b", "B")]
        manual = {"a": source[0], "b": source[1]}
        from speech_sdk import pronunciations

        validate = pronunciations._validate_rule

        def clear_source(rule: Pronunciation) -> None:
            source.clear()
            validate(rule)

        with patch.object(pronunciations, "_validate_rule", side_effect=clear_source):
            self.assertEqual(list(merge_rules(source)), ["a", "b"])
        self.assertEqual(source, [])

        def clear_map(rule: Pronunciation) -> None:
            manual.clear()
            validate(rule)

        with patch.object(pronunciations, "_validate_rule", side_effect=clear_map):
            self.assertEqual(substitute("a b", manual).text, "A B")
        self.assertEqual(manual, {})

    def test_exact_field_types_and_static_diagnostics(self) -> None:
        cases = [
            (7, "secret-replacement", False),
            ("secret-word", None, False),
            ("secret-word", "secret-replacement", 1),
            (StringSubclass("secret-word"), "secret-replacement", False),
            ("secret-word", StringSubclass("secret-replacement"), False),
        ]
        for word, replacement, sensitive in cases:
            with self.subTest(field_types=(type(word), type(replacement), type(sensitive))):
                with self.assertRaises(TypeError) as caught:
                    Pronunciation(cast(str, word), cast(str, replacement), cast(bool, sensitive))
                self.assertNotIn("secret", str(caught.exception))
        for word, sensitive in ((StringSubclass("secret"), False), ("secret", 1)):
            with self.assertRaises(TypeError) as caught:
                rule_map_key(word, cast(bool, sensitive))
            self.assertNotIn("secret", str(caught.exception))

    def test_invalid_containers_rules_text_and_manual_maps(self) -> None:
        invalid: list[object] = [
            "secret",
            b"secret",
            bytearray(b"secret"),
            {},
            {"secret"},
            iter([]),
            None,
        ]
        for rules in invalid:
            with self.subTest(container=type(rules)), self.assertRaises(TypeError) as caught:
                merge_rules(cast(Sequence[Pronunciation], rules))
            self.assertNotIn("secret", str(caught.exception))
        mutated = Pronunciation("secret-word", "secret-replacement")
        object.__setattr__(mutated, "case_sensitive", 1)
        for rule in ({"word": "secret"}, RuleSubclass("secret", "X"), mutated):
            with self.subTest(rule_type=type(rule)), self.assertRaises(TypeError):
                merge_rules(cast(Sequence[Pronunciation], [rule]))
        for text in (None, 1, StringSubclass("secret")):
            with self.subTest(text_type=type(text)), self.assertRaises(TypeError):
                substitute(cast(str, text), {})
        for manual, error in (
            ([], TypeError),
            ({"secret": mutated}, TypeError),
            ({"secret": RuleSubclass("a", "b")}, TypeError),
            ({"secret": Pronunciation("", "x")}, ValueError),
            ({"secret": Pronunciation("a", " ")}, ValueError),
            ({"secret": Pronunciation(" a", "b")}, ValueError),
            ({"secret": Pronunciation("a", "b ")}, ValueError),
        ):
            with self.subTest(manual_type=type(manual)), self.assertRaises(error) as invalid_map:
                substitute("secret-input", cast(Mapping[str, Pronunciation], manual))
            self.assertNotIn("secret", str(invalid_map.exception))


class PronunciationMatcherTests(unittest.TestCase):
    def test_reuse_clones_rules_and_snapshots_mapping(self) -> None:
        rule = Pronunciation("LLM", "el em")
        manual = {"ignored": rule}
        matcher = PronunciationMatcher(manual)
        expected = substitute("LLM", manual)
        manual.clear()
        object.__setattr__(rule, "word", "changed")
        object.__setattr__(rule, "replacement", "changed")
        object.__setattr__(rule, "case_sensitive", 1)
        self.assertEqual(matcher.substitute("LLM"), expected)
        self.assertEqual(
            matcher.substitute("llm!"),
            substitute("llm!", merge_rules([Pronunciation("LLM", "el em")])),
        )
        self.assertEqual(matcher.substitute("other"), SubstitutionResult("other", ()))

    def test_constructor_snapshots_values_before_validation(self) -> None:
        from speech_sdk import pronunciations

        manual = {"a": Pronunciation("a", "A"), "b": Pronunciation("b", "B")}
        validate = pronunciations._validate_rule

        def clear_map(rule: Pronunciation) -> None:
            manual.clear()
            validate(rule)

        with patch.object(pronunciations, "_validate_rule", side_effect=clear_map):
            matcher = PronunciationMatcher(manual)
        self.assertEqual(matcher.substitute("a b").text, "A B")
        self.assertEqual(manual, {})

    def test_same_bucket_collisions_and_cross_bucket_rank(self) -> None:
        cases = [
            ([Pronunciation("LLM", "first"), Pronunciation("llm", "second")], "first"),
            ([Pronunciation("LLM", "first", True), Pronunciation("LLM", "second", True)], "first"),
            ([Pronunciation("llm", "first"), Pronunciation("LLM", "second", True)], "first"),
            ([Pronunciation("LLM", "first", True), Pronunciation("llm", "second")], "first"),
            (
                [
                    Pronunciation("other", "unused"),
                    Pronunciation("llm", "second"),
                    Pronunciation("LLM", "third", True),
                ],
                "second",
            ),
        ]
        for rules, expected in cases:
            with self.subTest(rules=rules):
                manual = {str(rank): rule for rank, rule in enumerate(rules)}
                result = PronunciationMatcher(manual).substitute("LLM")
                self.assertEqual(result.text, expected)
                self.assertEqual(result, substitute("LLM", manual))

    def test_original_lengths_and_contextual_lowering(self) -> None:
        cases = [
            ("İ i i\u0307", [Pronunciation("İ", "X")], "X i i\u0307"),
            ("ΟΣΑ ΟΣ", [Pronunciation("ΟΣ", "X")], "ΟΣΑ X"),
            ("ΟΣΑ", [Pronunciation("ΟΣ", "X"), Pronunciation("\u0391", "Y")], "ΟΣΑ"),
            # Sigma's lowercase depends on letters outside a punctuation-ended slice.
            ("ΟΣ.\u0391", [Pronunciation("ΟΣ.", "X")], "X\u0391"),
            ("ß SS ss", [Pronunciation("ß", "X")], "X SS ss"),
        ]
        for text, rules, expected in cases:
            with self.subTest(text=text):
                manual = merge_rules(rules)
                result = PronunciationMatcher(manual).substitute(text)
                self.assertEqual(result.text, expected)
                self.assertEqual(result, substitute(text, manual))

    def test_class_and_wrapper_equivalence_for_100_small_cases(self) -> None:
        rules = merge_rules(
            [
                Pronunciation("a", "B"),
                Pronunciation("a b", "phrase"),
                Pronunciation("B", "C", True),
                Pronunciation("[a]", "tag"),
                Pronunciation("İ", "expanded"),
                Pronunciation("\u03c3", "sigma"),
            ]
        )
        matcher = PronunciationMatcher(rules)
        pieces = ["", "a", "A", "B", "a b", "[a]", "İ", "i\u0307", "Σ", "a_"]
        for left in pieces:
            for right in pieces:
                text = left + " " + right
                with self.subTest(text=text):
                    self.assertEqual(matcher.substitute(text), substitute(text, rules))

    def test_lookup_count_independent_of_same_length_rule_count(self) -> None:
        for count in (100, 1000):
            matcher = PronunciationMatcher(
                merge_rules(
                    [Pronunciation(f"r{rank:04}", "hit", bool(rank % 2)) for rank in range(count)]
                )
            )
            with patch.object(matcher, "_lookup", wraps=matcher._lookup) as lookup:
                self.assertEqual(
                    matcher.substitute("zzzzz zzzzz"), SubstitutionResult("zzzzz zzzzz", ())
                )
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(
                [call.args for call in lookup.call_args_list], [(5, "zzzzz"), (5, "zzzzz")]
            )

    def test_constructor_and_method_static_validation(self) -> None:
        mutated = Pronunciation("secret", "replacement")
        object.__setattr__(mutated, "replacement", StringSubclass("secret"))
        for manual, error in (
            ([], TypeError),
            ({"ignored": mutated}, TypeError),
            ({"ignored": RuleSubclass("a", "b")}, TypeError),
            ({"ignored": Pronunciation(" a", "b")}, ValueError),
            ({"ignored": Pronunciation("a", " ")}, ValueError),
        ):
            with self.subTest(manual=type(manual)), self.assertRaises(error) as caught:
                PronunciationMatcher(cast(Mapping[str, Pronunciation], manual))
            self.assertNotIn("secret", str(caught.exception))
        for text in (None, 1, StringSubclass("secret")):
            with self.assertRaises(TypeError) as invalid_text:
                PronunciationMatcher({}).substitute(cast(str, text))
            self.assertEqual(
                str(invalid_text.exception), "Pronunciation text must be a plain string"
            )
        with self.assertRaises(TypeError) as invalid_order:
            substitute(cast(str, None), cast(Mapping[str, Pronunciation], []))
        self.assertEqual(str(invalid_order.exception), "Pronunciation text must be a plain string")


def models() -> tuple[str | ResolvedModel, ...]:
    return (
        "openai",
        "xai",
        AzureOpenAIProvider(base_url="https://fixture.invalid/prefix", api_version="preview").model(
            "tts-1"
        ),
    )


class RequestArgs(TypedDict):
    model: str | ResolvedModel
    text: str
    voice: str
    pronunciations: Sequence[Pronunciation] | None
    api_key: str
    http_client: httpx.AsyncClient | None
    output: AudioOutput | None
    instructions: str | None
    provider_options: Mapping[str, object] | None
    headers: Mapping[str, str]


async def invoke(
    streaming: bool,
    model: str | ResolvedModel,
    text: str,
    rules: Sequence[Pronunciation] | None,
    client: httpx.AsyncClient | None,
    *,
    output: AudioOutput | None = None,
    instructions: str | None = None,
    options: Mapping[str, object] | None = None,
) -> tuple[bytes, int]:
    kwargs: RequestArgs = {
        "model": model,
        "text": text,
        "voice": " LLM ",
        "pronunciations": rules,
        "api_key": "offline-fixture",
        "http_client": client,
        "output": output,
        "instructions": instructions,
        "provider_options": options,
        "headers": {"Authorization": "ignored", "Api_Key": "ignored", "X-Test": "same"},
    }
    if streaming:
        async with stream_speech(**kwargs) as stream:
            return b"".join([chunk async for chunk in stream.audio]), stream.metadata.input_chars
    result = await generate_speech(**kwargs)
    return result.audio.data, result.metadata.input_chars


class GatedBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.reads = 0
        self.closes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.reads += 1
        yield b"first"
        self.waiting.set()
        await self.release.wait()
        raise httpx.ReadError("secret-input")

    async def aclose(self) -> None:
        self.closes += 1


class PronunciationAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_transformed_wire_options_headers_audio_and_original_metadata(self) -> None:
        for model in models():
            for streaming in (False, True):
                for codec in ("mp3", "wav"):
                    with self.subTest(model_type=type(model), streaming=streaming, codec=codec):
                        await self.wire_case(model, streaming, codec)

    async def wire_case(self, model: str | ResolvedModel, streaming: bool, codec: str) -> None:
        xai = model == "xai"
        azure = isinstance(model, ResolvedModel)
        options: dict[str, object] = (
            {"text": "ignored", "voice_id": "ignored", "speed": 1.1}
            if xai
            else {
                "input": "ignored",
                "voice": "ignored",
                "model": "ignored",
                "instructions": "native LLM",
                "speed": 1.1,
            }
        )
        before = options.copy()
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                content=b"\x00\xffwire",
                headers={"content-type": "audio/wav" if codec == "wav" else "audio/mpeg"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            audio, count = await invoke(
                streaming,
                model,
                "🙂 LLM Apple [pause]",
                [
                    Pronunciation("LLM", "el el em"),
                    Pronunciation("Apple", "A"),
                ],
                client,
                output=AudioOutput(format=cast(Literal["mp3", "wav"], codec)),
                instructions=None if xai else "canonical LLM",
                options=options,
            )
            self.assertFalse(client.is_closed)
        self.assertEqual((audio, count), (b"\x00\xffwire", 19))
        self.assertEqual(options, before)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        expected: dict[str, object] = {"speed": 1.1}
        if xai:
            expected.update(
                text="🙂 el el em A [pause]",
                voice_id=" LLM ",
                language="auto",
                output_format={
                    "codec": codec,
                    **({"sample_rate": 24000} if codec == "wav" else {}),
                },
            )
        else:
            expected.update(
                model="tts-1" if azure else "gpt-4o-mini-tts",
                input="🙂 el el em A [pause]",
                voice=" LLM ",
                stream_format="audio",
                response_format=codec,
                instructions="canonical LLM\n\nnative LLM",
            )
        self.assertEqual(json.loads(request.content), expected)
        self.assert_wire_headers(request, azure, xai)

    def assert_wire_headers(self, request: httpx.Request, azure: bool, xai: bool) -> None:
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["x-test"], "same")
        self.assertEqual(request.headers["user-agent"], "HYBRD/speech-sdk-python")
        auth = "api-key" if azure else "authorization"
        self.assertEqual(
            request.headers[auth], "offline-fixture" if azure else "Bearer offline-fixture"
        )
        self.assertNotIn("authorization" if azure else "api-key", request.headers)
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            str(request.url),
            "https://fixture.invalid/prefix/openai/v1/audio/speech?api-version=preview"
            if azure
            else "https://api.x.ai/v1/tts"
            if xai
            else "https://api.openai.com/v1/audio/speech",
        )

    async def test_final_limits_expansion_and_oversized_original_contraction(self) -> None:
        for model in models():
            limit = 60000 if model == "xai" else 4096
            for streaming in (False, True):
                with self.subTest(model_type=type(model), streaming=streaming):
                    await self.limit_case(model, streaming, limit)

    async def limit_case(self, model: str | ResolvedModel, streaming: bool, limit: int) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"audio")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for size in (limit, limit + 1):
                rules = [Pronunciation("a", "x" * size)]
                if size == limit:
                    self.assertEqual(
                        await invoke(streaming, model, "a", rules, client), (b"audio", 1)
                    )
                else:
                    with self.assertRaises(ValueError):
                        await invoke(streaming, model, "a", rules, client)
            original = "a" * (limit + 1)
            self.assertEqual(
                await invoke(
                    streaming, model, original, [Pronunciation(original, "short")], client
                ),
                (b"audio", limit + 1),
            )
        self.assertEqual(len(requests), 2)
        field = "text" if model == "xai" else "input"
        self.assertEqual(json.loads(requests[0].content)[field], "x" * limit)
        self.assertEqual(json.loads(requests[1].content)[field], "short")

    async def test_none_empty_blank_and_literal_tag_requests(self) -> None:
        cases = [
            (None, "LLM [pause]"),
            ([], "LLM [pause]"),
            ([Pronunciation(" ", "speak"), Pronunciation("LLM", "\n")], "LLM [pause]"),
            ([Pronunciation("pause", "wait")], "LLM [wait]"),
        ]
        for model in models():
            for streaming in (False, True):
                for rules, expected in cases:
                    with self.subTest(streaming=streaming, expected=expected):
                        requests: list[httpx.Request] = []

                        def handler(
                            request: httpx.Request, requests: list[httpx.Request] = requests
                        ) -> httpx.Response:
                            requests.append(request)
                            return httpx.Response(200, content=b"audio")

                        async with httpx.AsyncClient(
                            transport=httpx.MockTransport(handler)
                        ) as client:
                            self.assertEqual(
                                await invoke(streaming, model, "LLM [pause]", rules, client),
                                (b"audio", 11),
                            )
                        self.assertEqual(
                            json.loads(requests[0].content)["text" if model == "xai" else "input"],
                            expected,
                        )

    async def test_none_preserves_existing_text_subclass_path(self) -> None:
        for model in models():
            for streaming in (False, True):
                with self.subTest(streaming=streaming):
                    async with httpx.AsyncClient(
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(200, content=b"audio")
                        )
                    ) as client:
                        self.assertEqual(
                            await invoke(streaming, model, StringSubclass("LLM"), None, client),
                            (b"audio", 3),
                        )

    async def test_local_invalid_inputs_no_owned_client_no_requests(self) -> None:
        mutated = Pronunciation("secret", "replacement")
        object.__setattr__(mutated, "word", 1)
        cases: list[tuple[object, object, type[Exception]]] = [
            ("secret", "secret", TypeError),
            ("secret", {}, TypeError),
            ("secret", [mutated], TypeError),
            (StringSubclass("secret"), [], TypeError),
            (1, [], TypeError),
            ("", [Pronunciation("", "speech")], NoSpeechGeneratedError),
            (" \n", [], NoSpeechGeneratedError),
        ]
        for model in models():
            for streaming in (False, True):
                await self.invalid_case(model, streaming, cases)

    async def invalid_case(
        self,
        model: str | ResolvedModel,
        streaming: bool,
        cases: list[tuple[object, object, type[Exception]]],
    ) -> None:
        with patch(
            "speech_sdk._http.httpx.AsyncClient", side_effect=AssertionError("Owned client created")
        ) as factory:
            for text, rules, error in cases:
                with (
                    self.subTest(streaming=streaming, error=error),
                    self.assertRaises(error) as caught,
                ):
                    await invoke(
                        streaming,
                        model,
                        cast(str, text),
                        cast(Sequence[Pronunciation], rules),
                        None,
                    )
                self.assertNotIn("secret", str(caught.exception))
            factory.assert_not_called()

    async def test_retries_reuse_once_transformed_wire_for_both_modes(self) -> None:
        for model in models():
            for streaming in (False, True):
                with self.subTest(streaming=streaming):
                    await self.retry_case(model, streaming)

    async def retry_case(self, model: str | ResolvedModel, streaming: bool) -> None:
        requests: list[httpx.Request] = []
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(429 if len(requests) == 1 else 200, content=b"audio")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch(
                "speech_sdk._http.RetryTiming",
                return_value=RetryTiming(random=lambda: 0, sleep=sleep),
            ):
                self.assertEqual(
                    await invoke(
                        streaming,
                        model,
                        "LLM",
                        [Pronunciation("LLM", "el"), Pronunciation("el", "second")],
                        client,
                    ),
                    (b"audio", 3),
                )
        self.assertEqual(len(requests), 2)
        self.assertEqual(delays, [1])
        self.assertEqual(requests[0].content, requests[1].content)
        self.assertEqual(
            json.loads(requests[0].content)["text" if model == "xai" else "input"], "el"
        )

    async def test_stream_context_entry_snapshot_demand_and_no_replay_after_failure(self) -> None:
        body = GatedBody()
        response = httpx.Response(200, stream=body)
        requests: list[httpx.Request] = []
        rules = [Pronunciation("LLM", "first")]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manager = stream_speech(
                model="xai",
                text="LLM",
                voice="v",
                pronunciations=rules,
                api_key="offline-fixture",
                http_client=client,
            )
            self.assertEqual(requests, [])
            rules[0] = Pronunciation("LLM", "second")
            with patch.object(response, "aread", side_effect=AssertionError("Buffered")) as read:
                async with manager as stream:
                    self.assertEqual(body.reads, 0)
                    self.assertEqual(json.loads(requests[0].content)["text"], "second")
                    self.assertEqual(await anext(stream.audio), b"first")
                    self.assertFalse(body.waiting.is_set())

                    async def next_chunk() -> bytes:
                        return await anext(stream.audio)

                    task = asyncio.create_task(next_chunk())
                    await asyncio.wait_for(body.waiting.wait(), 1)
                    body.release.set()
                    with self.assertRaises(ProviderError) as caught:
                        await asyncio.wait_for(task, 1)
                    self.assertFalse(caught.exception.retryable)
                read.assert_not_called()
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), 1)
        self.assertEqual(body.closes, 1)
