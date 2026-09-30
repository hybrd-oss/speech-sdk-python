# Port scope and implementation plan

## Target

Build an independent, idiomatic Python implementation of Jellypod's Speech SDK. Start with OpenAI and xAI over direct HTTP, then expand toward full behavioral parity at a pinned upstream version.

This commit is repository setup and scope only: no SDK implementation, installed dependencies, or release scaffolding.

Reference: Jellypod-Inc/speech-sdk at `0e5a670324fb7be51a22708fe08bd7cc50f09f99` (package version 0.34.0). Source and tests define the reference behavior; current provider documentation must be checked before implementing each endpoint. Record intentional deviations instead of silently copying outdated behavior.

## Milestone 1: usable two-provider TTS

### Public API

- `generate_speech(...)`: async buffered generation.
- `stream_speech(...)`: async context manager exposing audio as an async iterator; guarantees response cleanup on completion, early exit, failure, and cancellation.
- Provider/model strings (`openai/gpt-4o-mini-tts`, `xai/grok-tts`) and bare provider names selecting defaults.
- Configured provider instances for explicit API keys, custom base URLs, and injectable HTTP clients.
- Required nonempty text and voice ID. Model capability/input-limit validation before requests; oversized inputs fail clearly until chunking is implemented.
- Provider-native options retain native key names. Canonical model/text/voice fields cannot be replaced through option passthrough.
- Optional delivery instructions where supported; reject unsupported nonempty instructions rather than silently discarding them.
- Native MP3/WAV/PCM output selection and provider-supported sample-rate validation; no local conversion.
- Results expose audio bytes and media type, provider/model metadata, latency, optional provider metadata, and warnings. Streaming exposes media type and setup latency; do not mislabel header arrival as first audio-byte arrival.

### Provider contracts to verify and port

| Provider | Models in reference | HTTP endpoint | Authentication | Request differences |
| --- | --- | --- | --- | --- |
| OpenAI | `gpt-4o-mini-tts` (default), `tts-1`, `tts-1-hd` | `POST https://api.openai.com/v1/audio/speech` | Bearer; `OPENAI_API_KEY` | `model`, `input`, `voice`, optional `instructions`, `response_format` |
| xAI | `grok-tts` (default) | `POST https://api.x.ai/v1/tts` | Bearer; `XAI_API_KEY` | `text`, `voice_id`, `language` (reference defaults to `auto`), nested `output_format`; reference does not send model ID |

Both reference adapters use binary HTTP response bodies for generation and streaming. Do not assume xAI uses OpenAI's request schema just because both use bearer authentication.

For PCM, preserve the actual sample rate in the media type. Reference OpenAI PCM is fixed at 24 kHz; xAI rates are provider-selected. Verify defaults and allowed rates against current docs; never guess missing rates for downstream processing.

### Reliability and security

- Explicit key overrides environment configuration; missing keys fail before HTTP calls. Never log keys or authorization headers.
- An SDK-owned `httpx.AsyncClient` is closed by the SDK; an injected client remains caller-owned. Avoid creating clients per audio chunk or hidden global clients.
- Finite, configurable network timeouts. Task cancellation propagates without retries or fallback.
- Errors preserve provider, model, HTTP status, provider code, request ID, parsed details, raw response, and retry classification. Sensitive response details are explicit data, not automatically logged.
- Default two retries. Match reference classification for 429, 5xx except 501, eligible network failures, and transient empty buffered responses. Authentication/content refusals/invalid inputs are terminal.
- Jittered exponential backoff; honor numeric and HTTP-date `Retry-After`, capped at 60 seconds as upstream does. Document potential duplicate synthesis/cost after ambiguous network failure; do not invent provider idempotency support.
- Streaming retries stop once a successful response is exposed to the consumer. Mid-stream failures propagate; never replay delivered audio.
- Validate malformed/empty responses and provider-native error envelopes, not just HTTP status.

### Acceptance checks

- Offline tests using `httpx.MockTransport` cover exact request/auth/option precedence, model resolution, native formats/sample rates, error envelopes, missing keys, input limits, retries, and timeout/cancellation propagation.
- Custom streaming test transport exercises multiple chunks, mid-stream failure, early exit, and response cleanup without buffering the whole response.
- Default tests require no credentials, network, or paid API calls. Retry tests inject sleep to avoid real waits.
- Explicit opt-in live smoke checks for both providers confirm a nonempty playable buffered file and streamed output. Credentials come from environment variables; generated audio is ignored by Git.
- No release until the offline checks pass and live checks are performed or clearly recorded as unverified.

## Deliberately outside milestone 1

These are deferred, not removed from the long-term port:

- Sync facade, timestamps/STT/forced alignment, captions, pronunciation substitutions.
- Automatic long-text chunking, expressive-tag translation/checking/removal.
- Conversations, native dialogue dispatch, stitching, normalization, resampling, speed changes, local codecs, and per-turn splitting.
- Voice cloning/design and other providers.
- PyPI publishing, docs website, plugin registry, hosted gateway, caching, and background task infrastructure.

Do not expose deferred options and silently ignore them. Until tag processing exists, text is sent verbatim and provider-native expressive behavior is not normalized across providers.

## Implementation order

1. **Foundation + OpenAI vertical slice:** minimal package configuration, result/error types, provider contract, injectable HTTP transport, buffered OpenAI synthesis, and offline checks.
2. **xAI:** provider-specific body/output handling, default model/key resolution, and equivalent HTTP contract checks.
3. **Streaming + reliability:** context-managed responses, bounded retries, timeouts, cancellation, cleanup tests, and opt-in live smoke checks for both providers.
4. **First usable package:** finish examples and API documentation; validate installation and license/notice inclusion. Add CI for the actual checks once those checks exist.

Each step should leave runnable checks. No empty implementations or speculative class hierarchies. Use `httpx` plus the standard library initially; dataclasses and `typing.Protocol` cover shared contracts with two adapters. No official provider SDK dependencies.

## Expansion toward the entire upstream SDK

1. Inventory every upstream public export, provider capability, default, warning, metadata field, error, and test; maintain a parity matrix against the pinned commit.
2. Port shared text preprocessing/chunking, pronunciation mapping, timestamp validation/fallback, and captions with upstream-derived golden cases.
3. Select and verify a Python audio backend (candidate: NumPy + PyAV); reproduce PCM/WAV handling, RMS normalization, resampling, speed processing, MP3 encoding, silence insertion, and sample-accurate cuts. Keep heavy audio dependencies optional where possible.
4. Port conversation dispatch (native, native-split, stitch), alignment/turn attribution, spoken-tag removal, and per-turn splitting. Preserve processing order and failure/fallback behavior.
5. Add all remaining reference provider adapters and supported STT/alignment/cloning/design operations. Translate provider HTTP fixtures; verify current APIs with opt-in live checks.
6. Differential-test deterministic canned responses against TypeScript, including error classifications, transcript mapping, audio boundaries, and streaming fragmentation. Compare exact bytes where deterministic and explicit numeric tolerances elsewhere; separate live TTS calls are not byte-comparable.

Full parity means tested behavior and documented intentional differences, not just matching function names. Reconcile later upstream changes explicitly; do not chase an unpinned moving target.

## Attribution when implementation begins

Retain relevant upstream notices. Files translated or adapted from upstream should prominently identify their origin and Python modifications, including the reference commit. Include LICENSE and NOTICE in built distributions. Do not claim Jellypod affiliation or guaranteed parity before verification.
