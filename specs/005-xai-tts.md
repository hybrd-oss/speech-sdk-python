# 005 — xAI text-to-speech

Status: adapter implemented and checked offline through both public paths; live xAI **FAIL / BLOCKED** after two failed runner invocations. The single private diagnostic isolated a valid WAV's unknown-length header; the precise `speech-vqx.26` fix passes offline, not the live gate. `speech-vqx.11` awaits final buffered + streamed re-verification. Depends on [002](002-api-and-package.md) and [003](003-http-and-errors.md). Required live completion gate: [007](007-validation-and-xai-e2e.md).

## Deliverables

`XAIProvider` and buffered synthesis through `generate_speech`. Direct `POST https://api.x.ai/v1/tts`, bearer key from explicit configuration or `XAI_API_KEY`. No xAI SDK or WebSocket dependency.

One model: `grok-tts`, default. This is the SDK's resolution identifier; **do not send a `model` field** to the REST endpoint. Reject attempts to supply one through native options, since xAI does not select TTS models that way.

Use **60,000 input characters** from current docs, rather than the pinned upstream's 15,000. Oversized text fails locally; no auto-chunking. Python Unicode code-point counting is defined in 002.

## Request contract

Default request:

```json
{
  "text": "Hello from Python!",
  "voice_id": "eve",
  "language": "auto"
}
```

- Canonical text and voice overwrite native `text`/`voice_id`. Voice is required by our unified API even though xAI documents an endpoint default.
- Default `language="auto"`; native nonblank language overrides it. Accept BCP-47 strings without maintaining a brittle closed language whitelist. Provider validates availability.
- Pass input verbatim, including xAI inline/wrapping expressive tags. No local tag stripping/translation.
- Top-level instructions are unsupported. Reject nonblank canonical or native `instructions`; do not silently drop them or synthesize a prompt into text.
- Optional native `output_format` must be an object. If `AudioOutput` is omitted, honor that object's format. If explicit output is supplied, canonical codec and rate win while compatible native extra fields remain.
- Native `speed`: finite number 0.7–1.5. `text_normalization`: bool. `optimize_streaming_latency`: initially 0 or 1; current guide also lists 2 while REST reference details differ, so defer 2 until verified. Reject booleans where numeric/integer values are expected.
- `with_timestamps=True` is rejected for both buffered and streaming calls. Current API returns a JSON/base64 envelope instead of raw audio when enabled; pretending that JSON is audio is a bug. False may be omitted or forwarded.
- Native `replace` (pronunciation map) is deferred: reject it explicitly rather than imply shared pronunciation semantics or add a replacement engine during this milestone.
- Other JSON-serializable options may pass through unless they conflict with protected fields or change the response protocol. No automatic voice-list/custom-voice API calls.

## Output contract

Default: MP3 at 24,000 Hz / 128,000 bps. For explicit WAV/PCM without a sample rate, use 24,000 Hz and send it explicitly. This intentionally differs from upstream's highest-supported-rate output helper.

Supported rates: **8,000, 16,000, 22,050, 24,000, 44,100, 48,000 Hz**. Keep explicit calibration/configuration values; no resampler.

| Codec | SDK exposure | Media type |
| --- | --- | --- |
| MP3 | Common output + native | `audio/mpeg` |
| WAV | Common output + native | `audio/wav` |
| PCM | Common output + native | `audio/pcm;rate=<effective rate>` |
| mulaw | Native only | `audio/basic` |
| alaw | Native only | `audio/alaw` |

- Validate native codec/rate too; do not let passthrough bypass output checks.
- For raw PCM omitted rate, resolve/document the 24,000 Hz default and include it in the outgoing body so the media type is never guessed. PCM is signed 16-bit little-endian, mono.
- MP3 bit rates: 32,000, 64,000, 96,000, 128,000, 192,000 bps. Reject bit-rate fields on non-MP3 output instead of silently retaining a contradictory option. Any confirmed codec/rate restrictions from current docs/live checks must become validation cases.
- Preserve received audio bytes. Compare response MIME against the validated codec; allow generic/missing MIME and documented aliases. Bare PCM gains the effective requested rate. JSON/HTML/SSE or conflicting audio MIME is a terminal contract error.
- Empty buffered response uses shared retry classification. Streamed emptiness is handled by 006 without replay.
- Offline smoke validation additionally recognizes the observed unknown-length WAV pair: data size `0x7fffffff`, RIFF size `0x80000023` (data + 36), only when stdlib `wave` locates PCM at byte 44. Existing `0xffffffff` support remains; other finite data declarations must match actual PCM bytes. Both sentinel forms still require bounded, aligned, nonempty, nonsilent mono PCM16 at 24 kHz with actual duration strictly between 0 and 60 seconds. This is narrow captured-header evidence, not a general xAI format promise or a passed live two-path gate (`speech-vqx.26`).

## Acceptance checks

- Exact endpoint/auth/default body with no `model`; `xai` and `xai/grok-tts` resolve identically.
- Voice/text precedence, default/explicit language, tagged input passthrough, and unchanged caller mappings.
- 60,000 characters accepted; 60,001 rejected before HTTP. Add a case above 15,000 proving we intentionally updated the reference limit.
- MP3/WAV/PCM, every listed sample rate, native telephony codec mapping, MP3 bit-rate validation, default PCM rate, and explicit-output precedence.
- Invalid output objects, bool-as-rate/speed, unsupported instructions, native `model`, `replace`, and timestamp mode fail with zero requests.
- 400/401/404 (including unknown voice) terminal; 429/500/503 retry through 003. Non-audio 200 and empty response coverage.
- A live xAI run with voice `eve`, `language="en"`, and WAV at 24 kHz must pass 007 before marking this milestone complete.

## Sources and verification items

Pinned upstream: `src/providers/xai/index.ts`, `src/__tests__/xai-speech-model.test.ts`, `xai-sample-rate.test.ts`, and `resolve-output-format-contract.test.ts`. Cloning tests document deferred work, not milestone-one coverage.

Current documentation reviewed 2026-09-30:

- [TTS guide](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)
- [REST reference](https://docs.x.ai/developers/rest-api-reference/inference/text-to-speech)

The guide explicitly describes POST as unary/server-streamed and separately describes bidirectional WebSocket TTS. We only consume the POST audio body; streaming does **not** accept incremental text input or provide a realtime voice-agent session.

Live validation must still verify response headers/defaults and codec/rate restrictions. The guide and REST reference differ on latency optimization levels, and the REST page's generic response schema emphasizes the timestamp envelope while its default examples return binary audio. Keep timestamp mode rejected; the real buffered/streamed WAV E2E is the verification gate for binary behavior.
