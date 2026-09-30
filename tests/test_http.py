"""HTTP tests never access a provider, credentials, real network, or real sleep."""

import asyncio
import json
import unittest
from collections.abc import AsyncIterator
from functools import partial
from typing import cast
from unittest.mock import patch

import httpx

from speech_sdk import NoSpeechGeneratedError, ProviderError, SpeechSDKError
from speech_sdk._http import RetryTiming, buffered_audio, open_response, retry_delay
from speech_sdk._validation import endpoint_url, prepare_request
from speech_sdk.types import PreparedRequest


def prepared(**overrides: object) -> PreparedRequest:
    # Runtime-invalid values intentionally exercise the pre-network boundary.
    values: dict[str, object] = {
        "provider": "xai",
        "model": "grok-tts",
        "base_url": "https://custom.example/v1/",
        "path": "tts",
        "api_key": "test-key",
        "body": {"text": "hi"},
        "media_type": "audio/mpeg",
        "input_chars": 2,
    }
    values.update(overrides)
    return prepare_request(
        provider=cast(str, values["provider"]),
        model=cast(str, values["model"]),
        base_url=cast(str, values["base_url"]),
        path=cast(str, values["path"]),
        api_key=cast(str, values["api_key"]),
        body=cast(dict[str, object], values["body"]),
        media_type=cast(str, values["media_type"]),
        input_chars=cast(int, values["input_chars"]),
        headers=cast(dict[str, str] | None, values.get("headers")),
        timeout=cast(float | httpx.Timeout, values.get("timeout", 60.0)),
        max_retries=cast(int, values.get("max_retries", 2)),
    )


class Body(httpx.AsyncByteStream):
    def __init__(
        self, chunks: tuple[bytes, ...] = (b"audio",), error: Exception | None = None
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.reads = 0
        self.closes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closes += 1


class RecordingTiming:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.timing = RetryTiming(clock=lambda: 1000.0, random=lambda: 0.0, sleep=self.sleep)

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def rejected_entry(
        self, buffered: bool, client: httpx.AsyncClient, timing: RetryTiming
    ) -> None:
        if buffered:
            await buffered_audio(prepared(), client=client, timing=timing)
        else:
            async with open_response(prepared(), client=client, timing=timing):
                self.fail("Invalid response exposed at streaming entry")

    async def test_wire_headers_and_injected_defaults(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://custom.example/v1/tts")
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            self.assertEqual(request.headers["content-type"], "application/json")
            self.assertEqual(request.headers["user-agent"], "HYBRD/speech-sdk-python")
            self.assertEqual(request.headers["x-custom"], "ok")
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("x-client", request.headers)
            self.assertEqual(request.extensions["timeout"], httpx.Timeout(60).as_dict())
            self.assertEqual(json.loads(request.content), {"text": "hi"})
            return httpx.Response(200, content=b"audio")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            auth=("bad", "bad"),
            cookies={"session": "bad"},
            headers={"X-Client": "bad"},
            follow_redirects=True,
        ) as client:
            before = dict(client.headers)
            audio = await buffered_audio(
                prepared(headers={"AUTHORIZATION": "bad", "Content-Type": "bad", "X-Custom": "ok"}),
                client=client,
            )
            self.assertEqual(audio.data, b"audio")
            self.assertEqual(dict(client.headers), before)
            self.assertFalse(client.is_closed)

    async def test_invalid_before_network(self) -> None:
        for overrides in (
            {"base_url": "ftp://example.com"},
            {"base_url": "https://user:pass@example.com"},
            {"base_url": "https://example.com?"},
            {"base_url": "https://example.com#"},
            {"base_url": "https://example.com:bad"},
            {"api_key": ""},
            {"headers": {"HOST": "bad"}},
            {"headers": {"Content-Length": "0"}},
            {"headers": {"transfer-encoding": "chunked"}},
            {"headers": {"x": "a\nb"}},
            {"headers": {"x\n": "bad"}},
            {"timeout": float("inf")},
            {"max_retries": True},
            {"body": {"x": {1: "bad"}}},
        ):
            with (
                self.subTest(overrides=tuple(overrides)),
                self.assertRaises((ValueError, TypeError, SpeechSDKError)),
            ):
                prepared(**overrides)
        self.assertEqual(
            endpoint_url("https://example.com/prefix/v1///", "/audio/speech"),
            "https://example.com/prefix/v1/audio/speech",
        )

    async def test_status_budgets_and_cleanup(self) -> None:
        for status, count in (
            (429, 3),
            (500, 3),
            (503, 3),
            (599, 3),
            (501, 1),
            (401, 1),
            (400, 1),
            (404, 1),
            (302, 1),
        ):
            bodies: list[Body] = []
            timing = RecordingTiming()

            def handler(
                request: httpx.Request, bodies: list[Body] = bodies, status: int = status
            ) -> httpx.Response:
                for previous in bodies:
                    self.assertEqual(previous.closes, 1)
                body = Body((b'{"error":{"code":"unknown","message":"secret"}}',))
                bodies.append(body)
                return httpx.Response(
                    status, stream=body, headers={"Location": "https://other.example"}
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=True
            ) as client:
                with self.subTest(status=status), self.assertRaises(ProviderError) as caught:
                    await buffered_audio(prepared(), client=client, timing=timing.timing)
            self.assertEqual(len(bodies), count)
            self.assertTrue(all(body.closes == 1 for body in bodies))
            self.assertEqual(len(timing.delays), count - 1)
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn("secret", repr(caught.exception))

    async def test_error_shapes_request_ids_and_retry_after(self) -> None:
        fixtures = (
            (b'{"error":{"code":"code","message":"private"},"request_id":"body"}', "code", "body"),
            (b'{"error":"private","requestId":"body"}', None, "body"),
            (b'{"message":"private","code":"code"}', "code", None),
            (b'{"detail":"private"}', None, None),
            (b'["unknown"]', None, None),
            (b"broken private", None, None),
        )
        for content, code, body_id in fixtures:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, content=content: httpx.Response(401, content=content)
                )
            ) as client:
                with self.assertRaises(ProviderError) as caught:
                    await buffered_audio(prepared(), client=client)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.request_id, body_id)
                self.assertEqual(caught.exception.raw_response, content.decode())
                self.assertNotIn("private", str(caught.exception))
        timing = RecordingTiming()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429,
                    content=b'{"request_id":"body"}',
                    headers={
                        "Request-ID": "header",
                        "X-Request-ID": "second",
                        "Retry-After": "2.5",
                    },
                )
            )
        ) as client:
            with self.assertRaises(ProviderError) as caught:
                await buffered_audio(prepared(max_retries=1), client=client, timing=timing.timing)
        self.assertEqual(caught.exception.request_id, "header")
        self.assertEqual(caught.exception.retry_after, 2.5)
        self.assertEqual(timing.delays, [2.5])

    async def test_retry_then_success_and_zero_budget(self) -> None:
        attempts = 0
        timing = RecordingTiming()

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(429 if attempts == 1 else 200, content=b"audio")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(
                (await buffered_audio(prepared(), client=client, timing=timing.timing)).data,
                b"audio",
            )
        self.assertEqual(attempts, 2)
        self.assertEqual(timing.delays, [1.0])
        attempts = 0
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError):
                await buffered_audio(prepared(max_retries=0), client=client, timing=timing.timing)
        self.assertEqual(attempts, 1)

    async def test_network_classification(self) -> None:
        for error_type in (
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ):
            attempts = 0
            error = error_type("private")
            timing = RecordingTiming()

            def handler(request: httpx.Request, error: Exception = error) -> httpx.Response:
                nonlocal attempts
                attempts += 1
                raise error

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with (
                    self.subTest(error=error_type),
                    self.assertRaises(ProviderError) as caught,
                ):
                    await buffered_audio(prepared(), client=client, timing=timing.timing)
            self.assertEqual(attempts, 3)
            self.assertIs(type(caught.exception), ProviderError)
            self.assertIs(caught.exception.__cause__, error)
            self.assertTrue(caught.exception.retryable)
            self.assertIsNone(caught.exception.status_code)
            self.assertEqual(timing.delays, [1.0, 2.0])

    async def test_ineligible_errors_pass_through_unchanged(self) -> None:
        for buffered in (False, True):
            for error_type in (httpx.LocalProtocolError, httpx.PoolTimeout, RuntimeError):
                attempts = 0
                error = error_type("private")
                timing = RecordingTiming()

                def handler(request: httpx.Request, error: Exception = error) -> httpx.Response:
                    nonlocal attempts
                    self.assertEqual(request.method, "POST")
                    attempts += 1
                    raise error

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with self.subTest(buffered=buffered, error=error_type):
                        with self.assertRaises(error_type) as caught:
                            await self.rejected_entry(buffered, client, timing.timing)
                        self.assertIs(type(caught.exception), error_type)
                        self.assertIs(caught.exception, error)
                        self.assertEqual(attempts, 1)
                        self.assertEqual(timing.delays, [])
                        self.assertFalse(client.is_closed)

    async def test_nonempty_contract_mime_rejected_before_read_or_exposure(self) -> None:
        for buffered in (False, True):
            for preloaded in (False, True):
                for mime, content in (
                    ("application/json", b'{"audio":"not audio"}'),
                    ("text/html", b"<html>not audio</html>"),
                    ("text/event-stream", b"data: not audio\n\n"),
                    ("audio/wav", b"RIFF conflicting audio"),
                ):
                    body = Body((content,))
                    response = httpx.Response(
                        200,
                        headers={"content-type": mime},
                        **({"content": content} if preloaded else {"stream": body}),
                    )
                    attempts = 0
                    timing = RecordingTiming()

                    def handler(
                        request: httpx.Request, response: httpx.Response = response
                    ) -> httpx.Response:
                        nonlocal attempts
                        self.assertEqual(request.method, "POST")
                        attempts += 1
                        return response

                    with self.subTest(buffered=buffered, preloaded=preloaded, mime=mime):
                        async with httpx.AsyncClient(
                            transport=httpx.MockTransport(handler)
                        ) as client:
                            with (
                                patch.object(
                                    response, "aread", side_effect=AssertionError("Body read")
                                ) as read,
                                self.assertRaises(ProviderError) as caught,
                            ):
                                await self.rejected_entry(buffered, client, timing.timing)
                            read.assert_not_called()
                            self.assertIs(type(caught.exception), ProviderError)
                            self.assertFalse(caught.exception.retryable)
                            self.assertEqual(caught.exception.status_code, 200)
                            self.assertEqual(attempts, 1)
                            self.assertEqual(timing.delays, [])
                            self.assertEqual(body.reads, 0)
                            self.assertTrue(response.is_closed)
                            self.assertEqual(body.closes, int(not preloaded))
                            self.assertFalse(client.is_closed)

    async def test_empty_buffered_body_and_contract_mime(self) -> None:
        for mime, expected_count in (
            ("audio/mpeg", 3),
            ("application/json", 1),
            ("text/html", 1),
            ("text/event-stream", 1),
            ("audio/wav", 1),
        ):
            attempts = 0
            timing = RecordingTiming()

            def handler(request: httpx.Request, mime: str = mime) -> httpx.Response:
                nonlocal attempts
                attempts += 1
                return httpx.Response(200, headers={"content-type": mime}, content=b"")

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ProviderError) as caught:
                    await buffered_audio(prepared(), client=client, timing=timing.timing)
            self.assertEqual(attempts, expected_count)
            self.assertEqual(
                isinstance(caught.exception, NoSpeechGeneratedError), expected_count == 3
            )

    async def test_success_stream_not_read_and_closed(self) -> None:
        body = Body((b"one", b"two"))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body))
        ) as client:
            async with open_response(prepared(), client=client) as opened:
                self.assertEqual(body.reads, 0)
                self.assertEqual(opened.media_type, "audio/mpeg")
                self.assertIsNotNone(opened.setup_latency_ms)
                self.assertGreaterEqual(cast(float, opened.setup_latency_ms), 0)
                iterator = opened.response.aiter_bytes()
                self.assertEqual(await anext(iterator), b"one")
                self.assertEqual(body.reads, 1)
            self.assertEqual(body.closes, 1)
            self.assertFalse(client.is_closed)

    async def test_body_read_failure_retains_status_and_closes_before_retry(self) -> None:
        for buffered in (False, True):
            statuses = (302, 400, 401, 404, 501, 429, 503) + ((200,) if buffered else ())
            for status in statuses:
                bodies: list[Body] = []
                responses: list[httpx.Response] = []
                timing = RecordingTiming()
                error = httpx.ReadError("private")

                async def sleep(
                    seconds: float,
                    bodies: list[Body] = bodies,
                    responses: list[httpx.Response] = responses,
                    timing: RecordingTiming = timing,
                ) -> None:
                    self.assertTrue(all(body.closes == 1 for body in bodies))
                    self.assertTrue(all(response.is_closed for response in responses))
                    await timing.sleep(seconds)

                def handler(
                    request: httpx.Request,
                    bodies: list[Body] = bodies,
                    responses: list[httpx.Response] = responses,
                    error: Exception = error,
                    status: int = status,
                ) -> httpx.Response:
                    self.assertEqual(request.method, "POST")
                    self.assertTrue(all(body.closes == 1 for body in bodies))
                    body = Body((), error)
                    bodies.append(body)
                    response = httpx.Response(
                        status, stream=body, headers={"X-Request-ID": "header-id"}
                    )
                    responses.append(response)
                    return response

                clock = RetryTiming(random=lambda: 0.0, sleep=sleep)
                with self.subTest(buffered=buffered, status=status):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        with self.assertRaises(ProviderError) as caught:
                            await self.rejected_entry(buffered, client, clock)
                        self.assertFalse(client.is_closed)
                    retryable = status in (200, 429, 503)
                    self.assertEqual(len(bodies), 3 if retryable else 1)
                    self.assertEqual(timing.delays, [1.0, 2.0] if retryable else [])
                    self.assertTrue(all(body.closes == 1 for body in bodies))
                    self.assertTrue(all(response.is_closed for response in responses))
                    self.assertIs(type(caught.exception), ProviderError)
                    self.assertEqual(caught.exception.retryable, retryable)
                    self.assertEqual(caught.exception.status_code, status)
                    self.assertEqual(caught.exception.request_id, "header-id")
                    self.assertEqual(caught.exception.provider, "xai")
                    self.assertEqual(caught.exception.model, "grok-tts")
                    self.assertIs(caught.exception.__cause__, error)

    async def test_owned_client_closes_and_cancellation(self) -> None:
        for fail in (False, True):
            owned = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, fail=fail: httpx.Response(
                        401 if fail else 200, content=b"audio"
                    )
                )
            )
            with patch("speech_sdk._http.httpx.AsyncClient", return_value=owned):
                if fail:
                    with self.assertRaises(ProviderError):
                        await buffered_audio(prepared())
                else:
                    await buffered_audio(prepared())
            self.assertTrue(owned.is_closed)
        started = asyncio.Event()
        wait = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await wait.wait()
            return httpx.Response(200)

        owned = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("speech_sdk._http.httpx.AsyncClient", return_value=owned):
            task = asyncio.create_task(buffered_audio(prepared()))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(owned.is_closed)

    async def test_backoff_cancellation_closes_response(self) -> None:
        started = asyncio.Event()
        wait = asyncio.Event()
        body = Body((b"error",))

        async def sleep(seconds: float) -> None:
            self.assertEqual(body.closes, 1)
            started.set()
            await wait.wait()

        timing = RetryTiming(sleep=sleep)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, stream=body))
        ) as client:
            task = asyncio.create_task(buffered_audio(prepared(), client=client, timing=timing))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(client.is_closed)
        self.assertEqual(body.closes, 1)

    async def test_truthful_latency_metadata(self) -> None:
        for buffered in (False, True):
            ticks = iter((1.0, 1.5))
            timing = RetryTiming(monotonic=partial(next, ticks))
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, content=b"audio")
                    )
                ) as client,
                open_response(
                    prepared(), client=client, timing=timing, buffered=buffered
                ) as opened,
            ):
                self.assertEqual(opened.latency_ms, 500 if buffered else None)
                self.assertEqual(opened.setup_latency_ms, None if buffered else 500)

    async def test_pcm_mime_aliases_and_explicit_mismatch(self) -> None:
        for mime in ("audio/pcm", "AUDIO/PCM; RATE=24000", "application/octet-stream", ""):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, mime=mime: httpx.Response(
                        200, content=b"\x00\x01", headers={"content-type": mime}
                    )
                )
            ) as client:
                audio = await buffered_audio(
                    prepared(media_type="audio/pcm;rate=24000"), client=client
                )
                self.assertEqual(audio.media_type, "audio/pcm;rate=24000")
                self.assertEqual(audio.data, b"\x00\x01")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"audio", headers={"content-type": "audio/pcm;rate=48000"}
                )
            )
        ) as client:
            with self.assertRaises(ProviderError) as caught:
                await buffered_audio(prepared(media_type="audio/pcm;rate=24000"), client=client)
            self.assertFalse(caught.exception.retryable)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"audio", headers={"content-type": "audio/x-wav"}
                )
            )
        ) as client:
            self.assertEqual(
                (await buffered_audio(prepared(media_type="audio/wav"), client=client)).media_type,
                "audio/wav",
            )

    async def test_consumer_failure_and_unused_response_cleanup(self) -> None:
        for consume in (False, True):
            body = Body()
            owned = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, body=body: httpx.Response(200, stream=body)
                )
            )
            with patch("speech_sdk._http.httpx.AsyncClient", return_value=owned):
                if consume:
                    with self.assertRaisesRegex(RuntimeError, "consumer"):
                        async with open_response(prepared()) as opened:
                            self.assertEqual(await anext(opened.response.aiter_bytes()), b"audio")
                            raise RuntimeError("consumer")
                else:
                    async with open_response(prepared()):
                        pass
            self.assertEqual(body.closes, 1)
            self.assertTrue(owned.is_closed)

    async def test_cancellation_during_buffered_read(self) -> None:
        started = asyncio.Event()
        wait = asyncio.Event()

        class WaitingBody(Body):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                started.set()
                await wait.wait()
                yield b"audio"

        body = WaitingBody()
        owned = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body))
        )
        with patch("speech_sdk._http.httpx.AsyncClient", return_value=owned):
            task = asyncio.create_task(buffered_audio(prepared()))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(body.closes, 1)
        self.assertTrue(owned.is_closed)

    def test_retry_delay_dates_finiteness_and_caps(self) -> None:
        timing = RetryTiming(clock=lambda: 0.0, random=lambda: 1.0)
        self.assertEqual(retry_delay(0, "2.5", timing), 2.75)
        self.assertEqual(retry_delay(0, "Thu, 01 Jan 1970 00:00:10 GMT", timing), 10.25)
        self.assertEqual(retry_delay(1000, "999", timing), 60.25)
        for value in ("nan", "inf", "-1", "bad", None):
            self.assertEqual(retry_delay(0, value, timing), 2.0)
