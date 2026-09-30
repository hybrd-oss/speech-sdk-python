"""Demand-driven streaming and resource ownership, without sockets or sleeps."""

import asyncio
import unittest
from collections.abc import AsyncIterator
from contextlib import ExitStack
from unittest.mock import patch

import httpx

from speech_sdk import NoSpeechGeneratedError, ProviderError, XAIProvider, stream_speech
from speech_sdk._http import RetryTiming


class TrackedBody(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...] = (b"a", b"bc"),
        error: BaseException | None = None,
        gated: bool = False,
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.gated = gated
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.reads = 0
        self.closes = 0
        self.eof = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if self.gated and index == 1:
                self.waiting.set()
                await self.release.wait()
            self.reads += 1
            yield chunk
        if self.error is not None:
            raise self.error
        self.eof = True

    async def aclose(self) -> None:
        self.closes += 1


class StreamingTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_first_chunk_before_eof_backpressure_single_consumer(self) -> None:
        body = TrackedBody((b"\x01", b"\x02\x03", b"", b"\x04\x05\x06"), gated=True)
        response = httpx.Response(200, stream=body, headers={"content-type": "audio/pcm"})
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response)
        ) as client:
            with patch.object(
                response, "aread", side_effect=AssertionError("Buffered body")
            ) as read:
                async with stream_speech(
                    model="xai",
                    text="hi",
                    voice="v",
                    api_key="key",
                    provider_options={"output_format": {"codec": "pcm"}},
                    http_client=client,
                ) as stream:
                    self.assertEqual(body.reads, 0)
                    iterator = aiter(stream.audio)
                    self.assertIs(iterator, stream.audio)
                    self.assertEqual(await anext(iterator), b"\x01")
                    self.assertFalse(body.eof)
                    self.assertEqual(body.reads, 1)
                    self.assertFalse(body.waiting.is_set())

                    async def next_chunk() -> bytes:
                        return await anext(iterator)

                    pending = asyncio.create_task(next_chunk())
                    await body.waiting.wait()
                    with self.assertRaisesRegex(RuntimeError, "concurrent"):
                        await anext(iterator)
                    self.assertEqual(body.reads, 1)
                    body.release.set()
                    self.assertEqual(await pending, b"\x02\x03")
                    self.assertEqual(await anext(iterator), b"\x04\x05\x06")
                    with self.assertRaises(StopAsyncIteration):
                        await anext(iterator)
                    self.assertTrue(response.is_closed)
                    self.assertEqual(body.closes, 1)
                    with self.assertRaises(StopAsyncIteration):
                        await anext(iterator)
                    self.assertEqual(body.reads, 4)
                    self.assertEqual(stream.media_type, "audio/pcm;rate=24000")
                    self.assertEqual(stream.metadata.input_chars, 2)
                    self.assertGreaterEqual(stream.metadata.setup_latency_ms, 0)
                    self.assertFalse(hasattr(stream.metadata, "ttfb_ms"))
                read.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "context"):
                    await anext(stream.audio)
            self.assertEqual(body.closes, 1)
            self.assertFalse(client.is_closed)

    async def test_pending_read_cannot_publish_after_exit_owned_injected(self) -> None:
        for owned in (False, True):
            for first_byte in (False, True):
                with self.subTest(owned=owned, first_byte=first_byte):
                    await self.pending_read_after_exit(owned, first_byte)

    async def pending_read_after_exit(self, owned: bool, first_byte: bool) -> None:
        body = TrackedBody((b"" if first_byte else b"first", b"lateaudio"), gated=True)
        response = httpx.Response(200, stream=body)
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        pending: asyncio.Task[bytes] | None = None
        try:
            async with asyncio.timeout(1):
                with (
                    patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
                    patch.object(client, "aclose", wraps=client.aclose) as close,
                ):
                    async with stream_speech(
                        model="xai",
                        text="hi",
                        voice="v",
                        api_key="offline-fixture",
                        http_client=None if owned else client,
                    ) as stream:
                        if not first_byte:
                            self.assertEqual(await anext(stream.audio), b"first")

                        async def next_chunk() -> bytes:
                            return await anext(stream.audio)

                        pending = asyncio.create_task(next_chunk())
                        await body.waiting.wait()
                        self.assertEqual(body.reads, 1)
                    self.assertFalse(pending.done())
                    self.assertTrue(response.is_closed)
                    self.assertEqual(body.closes, 1)
                    self.assertEqual(client.is_closed, owned)
                    body.release.set()
                    with self.assertRaisesRegex(RuntimeError, "valid only inside its context"):
                        await pending
                    with self.assertRaisesRegex(RuntimeError, "valid only inside its context"):
                        await anext(stream.audio)
                    self.assertEqual(body.reads, 2)
                    self.assertEqual(body.closes, 1)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(factory.call_count, int(owned))
                    self.assertEqual(close.await_count, int(owned))
                    self.assertEqual(client.is_closed, owned)
        finally:
            body.release.set()
            if pending is not None:
                pending.cancel()
                await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 1)
            await asyncio.wait_for(client.aclose(), 1)

    async def lifecycle(self, owned: bool, mode: str) -> None:
        error = httpx.ReadError("private") if mode == "readfailure" else None
        body = TrackedBody(error=error)
        response = httpx.Response(200, stream=body)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
        with (
            patch("speech_sdk._http.httpx.AsyncClient", return_value=client) as factory,
            patch.object(client, "aclose", wraps=client.aclose) as close,
        ):
            try:
                async with stream_speech(
                    model="xai",
                    text="hi",
                    voice="v",
                    api_key="key",
                    http_client=None if owned else client,
                ) as stream:
                    if mode == "unused":
                        pass
                    elif mode == "earlybreak":
                        async for chunk in stream.audio:
                            self.assertEqual(chunk, b"a")
                            break
                    elif mode == "consumererror":
                        await anext(stream.audio)
                        raise LookupError("consumer")
                    else:
                        self.assertEqual(b"".join([chunk async for chunk in stream.audio]), b"abc")
                        self.assertTrue(response.is_closed)
                        self.assertEqual(body.closes, 1)
            except (LookupError, ProviderError) as caught:
                self.assertIn(mode, ("consumererror", "readfailure"))
                if isinstance(caught, ProviderError):
                    self.assertIs(caught.__cause__, error)
                    self.assertFalse(caught.retryable)
            self.assertEqual(factory.call_count, int(owned))
            self.assertEqual(close.await_count, int(owned))
        self.assertTrue(response.is_closed)
        self.assertEqual(body.closes, 1)
        self.assertEqual(client.is_closed, owned)
        await client.aclose()
        with self.assertRaisesRegex(RuntimeError, "context"):
            await anext(stream.audio)

    async def test_owned_injected_unused_eof_break_consumer_read_cleanup(self) -> None:
        for owned in (False, True):
            for mode in ("unused", "eof", "earlybreak", "consumererror", "readfailure"):
                with self.subTest(owned=owned, mode=mode):
                    await self.lifecycle(owned, mode)

    async def test_empty_and_published_network_failure_never_retry(self) -> None:
        errors = (
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteError,
            httpx.ConnectError,
        )
        cases = [
            ((), None),
            ((b"",), None),
            *(
                (chunks, error_type("private"))
                for chunks in ((), (b"partial",))
                for error_type in errors
            ),
        ]
        for chunks, error in cases:
            body = TrackedBody(chunks, error)
            calls: list[httpx.Request] = []
            response = httpx.Response(200, stream=body, headers={"X-Request-ID": "header-id"})

            def handler(
                request: httpx.Request,
                calls: list[httpx.Request] = calls,
                response: httpx.Response = response,
            ) -> httpx.Response:
                calls.append(request)
                return response

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async with stream_speech(
                    model="xai", text="hi", voice="v", api_key="key", http_client=client
                ) as stream:
                    with self.assertRaises(ProviderError) as caught:
                        async for chunk in stream.audio:
                            self.assertEqual(chunk, b"partial")
                    self.assertTrue(response.is_closed)
                    self.assertEqual(body.closes, 1)
                    self.assertEqual(caught.exception.provider, "xai")
                    self.assertEqual(caught.exception.model, "grok-tts")
                    self.assertEqual(caught.exception.status_code, 200)
                    self.assertEqual(caught.exception.request_id, "header-id")
                    self.assertFalse(caught.exception.retryable)
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertEqual(
                        isinstance(caught.exception, NoSpeechGeneratedError), error is None
                    )
                    self.assertNotIn("private", str(caught.exception))
                self.assertFalse(client.is_closed)
            self.assertEqual(len(calls), 1)
            self.assertEqual(body.closes, 1)

    async def test_arbitrary_read_failure_identity_no_retry(self) -> None:
        for error in (
            httpx.LocalProtocolError("local"),
            httpx.PoolTimeout("pool"),
            RuntimeError("bug"),
        ):
            body = TrackedBody((), error)
            response = httpx.Response(200, stream=body)
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda request, response=response: response)
                ) as client,
                stream_speech(
                    model="openai", text="hi", voice="v", api_key="key", http_client=client
                ) as stream,
            ):
                with self.assertRaises(type(error)) as caught:
                    await anext(stream.audio)
                self.assertIs(caught.exception, error)
                self.assertTrue(response.is_closed)
            self.assertEqual(body.closes, 1)

    async def test_cancellation_inflight_read_and_request_owned_injected(self) -> None:
        for owned in (False, True):
            body = TrackedBody((b"first", b"second"), gated=True)
            response = httpx.Response(200, stream=body)
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            )

            async def consume(owned: bool = owned, client: httpx.AsyncClient = client) -> None:
                async with stream_speech(
                    model="xai",
                    text="hi",
                    voice="v",
                    api_key="key",
                    http_client=None if owned else client,
                ) as stream:
                    async for chunk in stream.audio:
                        self.assertEqual(chunk, b"first")

            with patch("speech_sdk._http.httpx.AsyncClient", return_value=client):
                task = asyncio.create_task(consume())
                await body.waiting.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(body.closes, 1)
            self.assertEqual(client.is_closed, owned)
            await client.aclose()
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            waiting.set()
            await release.wait()
            return httpx.Response(200)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def enter() -> None:
            async with stream_speech(model="xai", text="hi", voice="v", api_key="key"):
                self.fail("Cancelled request exposed")

        with patch("speech_sdk._http.httpx.AsyncClient", return_value=client):
            task = asyncio.create_task(enter())
            await waiting.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(client.is_closed)

    async def test_entry_status_retry_mime_failure_ownership(self) -> None:
        for owned in (False, True):
            for status, mime, budget, count in (
                (401, "audio/mpeg", 2, 1),
                (503, "audio/mpeg", 2, 3),
                (429, "audio/mpeg", 0, 1),
                (200, "application/json", 2, 1),
                (200, "text/html", 2, 1),
                (200, "text/event-stream", 2, 1),
                (200, "audio/wav", 2, 1),
            ):
                await self.rejected_entry(owned, status, mime, budget, count)

    async def rejected_entry(
        self, owned: bool, status: int, mime: str, budget: int, count: int
    ) -> None:
        bodies: list[TrackedBody] = []
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            self.assertTrue(all(body.closes == 1 for body in bodies))
            delays.append(seconds)

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(all(body.closes == 1 for body in bodies))
            body = TrackedBody((b"not audio",))
            bodies.append(body)
            return httpx.Response(status, stream=body, headers={"content-type": mime})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with (
            patch("speech_sdk._http.httpx.AsyncClient", return_value=client),
            patch(
                "speech_sdk._http.RetryTiming",
                return_value=RetryTiming(random=lambda: 0, sleep=sleep),
            ),
            self.assertRaises(ProviderError) as caught,
        ):
            async with stream_speech(
                model="xai",
                text="hi",
                voice="v",
                api_key="key",
                max_retries=budget,
                http_client=None if owned else client,
            ):
                self.fail("Invalid entry published")
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(len(bodies), count)
        self.assertEqual(len(delays), count - 1)
        self.assertTrue(all(body.closes == 1 for body in bodies))
        if status == 200:
            self.assertTrue(all(body.reads == 0 for body in bodies))
        self.assertEqual(client.is_closed, owned)
        await client.aclose()

    async def test_entry_backoff_cancellation_closes_owned_and_injected(self) -> None:
        for owned in (False, True):
            started = asyncio.Event()
            release = asyncio.Event()
            body = TrackedBody((b"error",))
            response = httpx.Response(503, stream=body)

            async def sleep(
                seconds: float,
                body: TrackedBody = body,
                started: asyncio.Event = started,
                release: asyncio.Event = release,
            ) -> None:
                self.assertEqual(body.closes, 1)
                started.set()
                await release.wait()

            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            )

            async def enter(owned: bool = owned, client: httpx.AsyncClient = client) -> None:
                async with stream_speech(
                    model="xai",
                    text="hi",
                    voice="v",
                    api_key="key",
                    http_client=None if owned else client,
                ):
                    self.fail("Backoff cancellation published response")

            with (
                patch("speech_sdk._http.httpx.AsyncClient", return_value=client),
                patch("speech_sdk._http.RetryTiming", return_value=RetryTiming(sleep=sleep)),
            ):
                task = asyncio.create_task(enter())
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(body.closes, 1)
            self.assertEqual(client.is_closed, owned)
            await client.aclose()

    async def test_unentered_context_does_not_validate_or_create_client(self) -> None:
        with patch(
            "speech_sdk._http.httpx.AsyncClient", side_effect=AssertionError("Client created")
        ):
            manager = stream_speech(model="unknown", text="hi", voice="v")
            self.assertTrue(hasattr(manager, "__aenter__"))

    async def test_setup_retry_then_success_no_body_read_and_truthful_timing(self) -> None:
        bodies: list[TrackedBody] = []
        now = [10.0]
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)
            now[0] += 0.25
            self.assertEqual(bodies[0].closes, 1)

        def handler(request: httpx.Request) -> httpx.Response:
            now[0] += 0.5
            body = TrackedBody()
            bodies.append(body)
            return httpx.Response(429 if len(bodies) == 1 else 200, stream=body)

        prepared = XAIProvider(api_key="key").prepare(model_id="grok-tts", text="hi", voice="v")

        def prepare(**kwargs: object) -> object:
            self.assertEqual(kwargs["model_id"], "grok-tts")
            now[0] += 5.0  # Deliberately excluded from streaming setup timing.
            return prepared

        with (
            patch.object(XAIProvider, "prepare", side_effect=prepare),
            patch(
                "speech_sdk._http.RetryTiming",
                return_value=RetryTiming(monotonic=lambda: now[0], random=lambda: 0, sleep=sleep),
            ),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async with stream_speech(
                    model="xai", text="hi", voice="v", api_key="key", http_client=client
                ) as stream:
                    self.assertEqual(stream.metadata.setup_latency_ms, 1250)
                    self.assertEqual(bodies[1].reads, 0)
                    self.assertEqual(b"".join([chunk async for chunk in stream.audio]), b"abc")
                self.assertFalse(client.is_closed)
        self.assertEqual(len(bodies), 2)
        self.assertEqual(delays, [1])
        self.assertTrue(all(body.closes == 1 for body in bodies))
