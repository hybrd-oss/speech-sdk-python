"""Offline runner checks: patch public calls only; never synthesize real speech."""

import asyncio
import io
import os
import socket
import struct
import tempfile
import unittest
import wave
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from smoke import run
from speech_sdk import (
    AudioData,
    AudioOutput,
    SpeechMetadata,
    SpeechResult,
    SpeechStream,
    StreamMetadata,
)


def wav_bytes(
    *, rate: int = 24000, channels: int = 1, width: int = 2, samples: bytes = b"\x01\x00" * 240
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(samples)
    return buffer.getvalue()


class SmokeTests(unittest.IsolatedAsyncioTestCase):
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
        self.stack.enter_context(
            patch.object(
                socket.socket, "connect", side_effect=AssertionError("Real network forbidden")
            )
        )
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(temporary) / "artifacts" / "smoke"
        self.stack.enter_context(patch.object(run, "ARTIFACTS", self.root))
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.payload = wav_bytes()
        self.failures: set[tuple[str, str]] = set()
        self.active = False
        self.closed = 0
        self.stack.enter_context(patch.object(run, "generate_speech", side_effect=self.generate))
        self.stack.enter_context(patch.object(run, "stream_speech", side_effect=self.stream))

    def record(self, mode: str, options: dict[str, object]) -> str:
        self.assertFalse(self.active, "Requests must be sequential")
        model = str(options["model"])
        provider = model.split("/")[0]
        self.assertEqual(model, "xai/grok-tts" if provider == "xai" else "openai/gpt-4o-mini-tts")
        self.calls.append((provider, mode, options))
        self.assertEqual(options["max_retries"], 0)
        self.assertEqual(options["timeout"], 60.0)
        self.assertEqual(options["text"], run.TEXT)
        self.assertNotIn("api_key", options)
        output = options["output"]
        self.assertEqual(output, AudioOutput("wav", 24000))
        self.assertEqual(options["voice"], "eve" if provider == "xai" else "alloy")
        self.assertEqual(
            options["provider_options"], {"language": "en"} if provider == "xai" else None
        )
        return provider

    async def generate(self, **options: object) -> SpeechResult:
        provider = self.record("buffered", options)
        if (provider, "buffered") in self.failures:
            raise RuntimeError("private response MUST NOT BE PRINTED")
        return self.result(provider)

    def result(self, provider: str) -> SpeechResult:
        return SpeechResult(
            AudioData(self.payload, "audio/wav"),
            provider,
            "grok-tts" if provider == "xai" else "gpt-4o-mini-tts",
            SpeechMetadata(len(run.TEXT), 12.5),
        )

    @asynccontextmanager
    async def stream(self, **options: object) -> AsyncIterator[SpeechStream]:
        provider = self.record("streamed", options)
        self.active = True

        async def chunks() -> AsyncIterator[bytes]:
            yield self.payload[:15]
            if (provider, "streamed") in self.failures:
                raise RuntimeError("private response MUST NOT BE PRINTED")
            yield self.payload[15:]

        result = self.result(provider)
        try:
            yield SpeechStream(
                chunks(),
                result.audio.media_type,
                provider,
                result.model,
                StreamMetadata(len(run.TEXT), 4.0),
            )
        finally:
            self.active = False
            self.closed += 1

    def test_preflight_and_help_have_no_calls_or_files(self) -> None:
        for arguments in ([], ["--provider", "xai"], ["--provider", "openai"]):
            self.assertEqual(run.main(arguments), 2)
        with patch.dict(os.environ, {"XAI_API_KEY": "   "}):
            self.assertEqual(run.main([]), 2)
        with self.assertRaises(SystemExit) as help_exit:
            run.main(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        with self.assertRaises(SystemExit) as invalid_exit:
            run.main(["--provider", "invalid"])
        self.assertEqual(invalid_exit.exception.code, 2)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.root.exists())

    async def test_default_selection_reporting_and_explicit_missing(self) -> None:
        for provider, key in (("xai", "XAI_API_KEY"), ("openai", "OPENAI_API_KEY")):
            with patch.dict(os.environ, {key: "offline-fixture"}, clear=True):
                self.assertEqual(await run.run_checks(run.select(None)), 0)
                other = "openai" if provider == "xai" else "xai"
                self.assertIn(f"{other}: NOT RUN", self.output.getvalue())
                self.assertEqual(run.main(["--provider", other]), 2)
            for mode in ("buffered", "streamed"):
                self.assertEqual((self.root / provider / f"{mode}.wav").read_bytes(), self.payload)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.closed, 2)
        self.assertIn("full-call", self.output.getvalue())
        self.assertIn("setup", self.output.getvalue())
        self.assertNotIn("offline-fixture", self.output.getvalue())

    def test_both_keys_default_main_calls_and_outputs(self) -> None:
        with patch.dict(
            os.environ,
            {"XAI_API_KEY": "offline-fixture", "OPENAI_API_KEY": "offline-fixture"},
        ):
            self.assertEqual(run.main([]), 0)
        expected = [
            ("xai", "buffered"),
            ("xai", "streamed"),
            ("openai", "buffered"),
            ("openai", "streamed"),
        ]
        self.assertEqual([(p, m) for p, m, _ in self.calls], expected)
        lines = []
        for provider, mode in expected:
            path = self.root / provider / f"{mode}.wav"
            self.assertEqual(path.read_bytes(), self.payload)
            self.assertFalse(path.with_suffix(".wav.part").exists())
            model = "grok-tts" if provider == "xai" else "gpt-4o-mini-tts"
            timing = "full-call=12.5" if mode == "buffered" else "setup=4.0"
            lines.append(
                f"{provider}/{model} {mode}: PASS {len(self.payload)} bytes "
                f"0.010s 24000Hz mono16 {timing}ms {path}"
            )
        self.assertEqual(self.output.getvalue().splitlines(), lines)
        self.assertEqual(self.closed, 2)

    async def test_failures_continue_remove_stale_and_do_not_leak(self) -> None:
        for provider in ("xai", "openai"):
            folder = self.root / provider
            folder.mkdir(parents=True)
            for mode in ("buffered", "streamed"):
                for suffix in (".wav", ".wav.part"):
                    (folder / f"{mode}{suffix}").write_bytes(b"stale")
        self.failures = {("xai", "buffered"), ("xai", "streamed")}
        self.assertEqual(await run.run_checks(["xai", "openai"]), 1)
        self.assertEqual(
            [(p, m) for p, m, _ in self.calls],
            [
                ("xai", "buffered"),
                ("xai", "streamed"),
                ("openai", "buffered"),
                ("openai", "streamed"),
            ],
        )
        self.assertEqual(list((self.root / "xai").iterdir()), [])
        self.assertEqual(self.closed, 2)
        self.assertNotIn("private response", self.output.getvalue())

    async def test_invalid_audio_never_saved(self) -> None:
        for payload in (
            b"",
            b"not wav",
            wav_bytes(samples=b""),
            wav_bytes(samples=b"\x00\x00" * 20),
            wav_bytes(rate=8000),
            wav_bytes(channels=2),
            wav_bytes(width=1),
            wav_bytes()[:-1],
            wav_bytes()[:-2],
        ):
            self.payload = payload
            self.assertEqual(await run.run_checks(["xai"]), 1)
            self.assertEqual(list((self.root / "xai").iterdir()), [])
        self.assertEqual(len(self.calls), 18)

    async def test_large_finite_declared_data_is_truncated(self) -> None:
        for declared in (2880002, 10000000):
            with self.subTest(declared=declared):
                payload = bytearray(wav_bytes())
                struct.pack_into("<I", payload, 40, declared)
                self.payload = bytes(payload)
                self.output.seek(0)
                self.output.truncate()
                with patch.object(run, "save") as save:
                    self.assertEqual(await run.run_checks(["xai"]), 1)
                    save.assert_not_called()
                self.assertEqual(list((self.root / "xai").iterdir()), [])
                self.assertNotIn("PASS", self.output.getvalue())
                self.assertEqual(self.output.getvalue().count("FAIL"), 2)
                with self.assertRaises(ValueError):
                    run.validate_wav(self.payload)

    async def test_sentinel_stream_and_buffer_pass_actual_pcm(self) -> None:
        payload = bytearray(wav_bytes())
        struct.pack_into("<I", payload, 4, 0xFFFFFFFF)
        struct.pack_into("<I", payload, 40, 0xFFFFFFFF)
        self.payload = bytes(payload)
        self.assertEqual(await run.run_checks(["xai"]), 0)
        for mode in ("buffered", "streamed"):
            self.assertEqual((self.root / "xai" / f"{mode}.wav").read_bytes(), self.payload)
        self.assertEqual(self.closed, 1)

    def test_actual_frames_sentinel_and_limits(self) -> None:
        sentinel = bytearray(wav_bytes())
        struct.pack_into("<I", sentinel, 4, 0xFFFFFFFF)
        struct.pack_into("<I", sentinel, 40, 0xFFFFFFFF)
        self.assertAlmostEqual(run.validate_wav(bytes(sentinel)), 0.01)
        for payload in (
            wav_bytes(samples=b"\x01\x00" * (24000 * 60)),
            wav_bytes(samples=b"\x01\x00" * (24000 * 60 + 1)),
        ):
            with self.assertRaises(ValueError):
                run.validate_wav(payload)

    def test_metadata_validation(self) -> None:
        valid = self.result("xai")
        for result in (
            replace(valid, provider="openai"),
            replace(valid, model="wrong"),
            replace(valid, audio=AudioData(self.payload, "audio/mpeg")),
            replace(valid, metadata=SpeechMetadata(0, 0)),
            replace(valid, metadata=SpeechMetadata(len(run.TEXT), -1)),
            replace(valid, metadata=SpeechMetadata(len(run.TEXT), float("nan"))),
            replace(valid, metadata=SpeechMetadata(len(run.TEXT), float("inf"))),
        ):
            with self.assertRaises(ValueError):
                run.validate_metadata(
                    "xai",
                    result.provider,
                    result.model,
                    result.audio.media_type,
                    result.metadata.input_chars,
                    result.metadata.latency_ms,
                )
        run.validate_metadata(
            "xai",
            valid.provider,
            valid.model,
            valid.audio.media_type,
            valid.metadata.input_chars,
            0.0,
        )

    async def test_buffered_invalid_metadata_never_saved_or_passed(self) -> None:
        valid = self.result("xai")
        self.failures = {("xai", "streamed")}
        for result in (
            replace(valid, provider="openai"),
            replace(valid, model="wrong"),
            replace(valid, audio=AudioData(self.payload, "audio/mpeg")),
            replace(valid, metadata=SpeechMetadata(0, 12.5)),
            replace(valid, metadata=SpeechMetadata(len(run.TEXT), float("nan"))),
        ):
            with self.subTest(result=result):
                self.output.seek(0)
                self.output.truncate()

                async def malformed(
                    response: SpeechResult = result, **options: object
                ) -> SpeechResult:
                    self.record("buffered", options)
                    return response

                with (
                    patch.object(run, "generate_speech", side_effect=malformed),
                    patch.object(run, "save") as save,
                ):
                    self.assertEqual(await run.run_checks(["xai"]), 1)
                    save.assert_not_called()
                self.assertEqual(list((self.root / "xai").iterdir()), [])
                self.assertNotIn("PASS", self.output.getvalue())
                self.assertIn("FAIL", self.output.getvalue())

    async def test_stream_byte_bound_stops_before_next_yield(self) -> None:
        advances = []
        self.failures = {("xai", "buffered")}

        @asynccontextmanager
        async def oversized(**options: object) -> AsyncIterator[SpeechStream]:
            async with self.stream(**options) as stream:

                async def chunks() -> AsyncIterator[bytes]:
                    advances.append("first")
                    yield self.payload
                    advances.append("oversized")
                    yield b"x" * run.MAX_BYTES
                    advances.append("forbidden-next")
                    yield b"next"

                yield replace(stream, audio=chunks())

        with patch.object(run, "stream_speech", side_effect=oversized):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertEqual(advances, ["first", "oversized"])
        self.assertEqual(self.closed, 1)
        self.assertFalse(self.active)
        self.assertEqual(list((self.root / "xai").iterdir()), [])
        self.assertNotIn("PASS", self.output.getvalue())

    async def test_cancel_propagates_closes_and_clears_both_stale_paths(self) -> None:
        folder = self.root / "xai"
        folder.mkdir(parents=True)
        for mode in ("buffered", "streamed"):
            for suffix in (".wav", ".wav.part"):
                (folder / f"{mode}{suffix}").write_bytes(b"stale")
        with (
            patch.object(run, "generate_speech", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await run.run_checks(["xai"])
        self.assertEqual(list(folder.iterdir()), [])
        for mode in ("buffered", "streamed"):
            for suffix in (".wav", ".wav.part"):
                (folder / f"{mode}{suffix}").write_bytes(b"stale")

        @asynccontextmanager
        async def cancelled(**options: object) -> AsyncIterator[SpeechStream]:
            async with self.stream(**options) as stream:

                async def chunks() -> AsyncIterator[bytes]:
                    yield b"partial"
                    raise asyncio.CancelledError

                yield replace(stream, audio=chunks())

        with (
            patch.object(run, "stream_speech", side_effect=cancelled),
            self.assertRaises(asyncio.CancelledError),
        ):
            await run.run_checks(["xai"])
        self.assertFalse((folder / "streamed.wav").exists())
        self.assertFalse((folder / "buffered.wav.part").exists())
        self.assertFalse((folder / "streamed.wav.part").exists())
        self.assertEqual(self.closed, 1)

    async def test_cleanup_or_write_failure_is_nonzero_and_safe(self) -> None:
        with patch.object(Path, "unlink", side_effect=PermissionError("private path")):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertEqual(self.calls, [])
        with patch.object(Path, "replace", side_effect=PermissionError("private path")):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertEqual(list((self.root / "xai").iterdir()), [])
        self.assertNotIn("private path", self.output.getvalue())

    async def test_deadlines_and_stream_metadata_empty_audio(self) -> None:
        @asynccontextmanager
        async def invalid(**options: object) -> AsyncIterator[SpeechStream]:
            async with self.stream(**options) as stream:
                yield replace(stream, metadata=StreamMetadata(len(run.TEXT), float("nan")))

        with patch.object(run, "stream_speech", side_effect=invalid):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertFalse((self.root / "xai" / "streamed.wav").exists())

        @asynccontextmanager
        async def empty(**options: object) -> AsyncIterator[SpeechStream]:
            async with self.stream(**options) as stream:

                async def chunks() -> AsyncIterator[bytes]:
                    yield b""

                yield replace(stream, audio=chunks())

        with patch.object(run, "stream_speech", side_effect=empty):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertFalse((self.root / "xai" / "streamed.wav").exists())
        original_timeout = asyncio.timeout
        with patch.object(asyncio, "timeout", wraps=original_timeout) as deadline:
            self.assertEqual(await run.run_checks(["openai"]), 0)
        self.assertEqual([call.args for call in deadline.call_args_list], [(90,), (90,)])
        with patch.object(run, "generate_speech", side_effect=TimeoutError("private timeout")):
            self.assertEqual(await run.run_checks(["openai"]), 1)
        self.assertNotIn("private timeout", self.output.getvalue())

    def test_main_opt_in_and_import_discovery_do_not_call_api(self) -> None:
        import importlib

        with patch.dict(os.environ, {"XAI_API_KEY": "offline-fixture"}):
            importlib.import_module("smoke.run")
            suite = unittest.defaultTestLoader.discover("tests", pattern="test_smoke.py")
            self.assertGreater(suite.countTestCases(), 0)
            self.assertEqual(self.calls, [])
            self.assertFalse(self.root.exists())
            self.assertEqual(run.main(["--provider", "xai"]), 0)
        self.assertEqual(len(self.calls), 2)

    async def test_overall_deadline_cancels_wait_and_continues(self) -> None:
        async def blocked(**options: object) -> SpeechResult:
            self.record("buffered", options)
            await asyncio.Event().wait()
            raise AssertionError("Deadline did not cancel")

        original_timeout = asyncio.timeout
        with (
            patch.object(run, "generate_speech", side_effect=blocked),
            patch.object(asyncio, "timeout", side_effect=lambda seconds: original_timeout(0)),
        ):
            self.assertEqual(await run.run_checks(["xai"]), 1)
        self.assertEqual(
            [(p, m) for p, m, _ in self.calls], [("xai", "buffered"), ("xai", "streamed")]
        )
        self.assertFalse((self.root / "xai" / "buffered.wav").exists())
        self.assertEqual(self.closed, 1)
