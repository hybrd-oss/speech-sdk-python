# 006 — Context-managed HTTP audio streaming

Status: implemented with offline streaming/backpressure/cleanup checks, including pending-read invalidation on context exit; live integration remains pending 007. Depends on [003](003-http-and-errors.md), [004](004-openai-tts.md), and [005](005-xai-tts.md).

## Deliverables

`stream_speech(...)` uses the same validated provider request as buffered generation, but exposes the successful response body incrementally:

```python
async with stream_speech(model="xai", text="Hello!", voice="eve") as stream:
    async for chunk in stream.audio:
        await consume(chunk)
```

No `await stream_speech(...)`, background reader, unbounded queue, SSE parser, base64 conversion, or WebSocket transport. All input text is supplied before the POST; only output audio is streamed.

## Lifecycle

```text
enter context
  validate and resolve provider request
  obtain owned or caller-injected client
  retry eligible failures while opening response/validating status
  publish successful binary response + metadata
    iterate body on consumer demand
    propagate EOF or error; never reconnect/replay
exit context (normal, early, error, cancellation)
  close response
  close only SDK-owned client
```

- Use HTTPX's streaming response API; never `.content`/`.aread()`/join the successful audio body before yielding the result.
- Use `aiter_bytes()` without a fixed minimum chunk size so the iterator does not wait to fill a large buffer. Ignore empty chunks. Chunk boundaries are transport boundaries, not independent playable files or PCM frames.
- Concatenated chunks reproduce the response audio bytes. Consumers may need to buffer partial frames; the SDK does not decode or repackage audio.
- A single consumer/iteration is supported. Do not permit concurrent consumers/replay; document the iterator as single-pass, valid only inside its context.
- Preserve backpressure: no pulling the next body chunk until the consumer requests it.
- `setup_latency_ms` measures validation-complete-to-response-publication time including retries. It is not first-audio-byte latency; no fabricated `ttfb_ms` or audio duration.
- Exiting without consuming, breaking early, consumer failure, read failure, and task cancellation all close response resources. Cancellation in an in-flight read still propagates unchanged.
- Close on normal exhaustion as well as context exit (idempotently). Use context-manager/finally ownership rather than relying on garbage collection.
- API errors during entry use 003. If a body is JSON/HTML/SSE according to its MIME, fail at entry as an unsupported contract, without publishing it as audio.
- After publication, read/network failures become context-rich nonretryable errors. EOF with zero nonempty bytes raises `NoSpeechGeneratedError` at iteration completion, without retries. Exiting before consuming anything is caller choice, not empty-audio failure.

## Acceptance checks

Use a custom `httpx.AsyncByteStream` with controlled read/close tracking inside an HTTPX transport. `MockTransport` returning an already-loaded byte body cannot prove streaming/backpressure.

1. First chunk becomes available before the test releases the next chunk; no full-response buffering.
2. Slow consumer does not prefetch body chunks; joined output equals fixture bytes.
3. Response media/provider/model metadata matches buffered request construction for both providers.
4. Normal EOF, unused context, early break, consumer exception, read exception, timeout, and cancellation close the response exactly/idempotently as appropriate.
5. Injected client remains open; SDK-owned client closes on all paths, including entry failure.
6. Setup 429→success retries; terminal 401 does not. Once result is exposed, failure before the first byte, after a partial chunk, or empty EOF performs no second POST.
7. Multiple chunk sizes/partial PCM samples do not corrupt or rewrite bytes. No requirement that live HTTP responses produce a particular number of chunks.

The live xAI streaming WAV run in [007](007-validation-and-xai-e2e.md) proves provider integration; deterministic offline tests prove demand-driven streaming and cleanup, which network chunk timing alone cannot prove.

## Reference

Pinned upstream: `src/stream-speech.ts`, `src/stream-speech-result.ts`, `src/__tests__/stream-speech.test.ts`, `stream-speech-integration.test.ts`, and provider stream tests. Deferred upstream tag/pronunciation processing is not silently exposed. Python async context management replaces JS stream lifetime semantics.
