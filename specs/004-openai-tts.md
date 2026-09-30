# 004 — OpenAI text-to-speech

Status: adapter implemented and checked offline through both public paths; live OpenAI not run/unverified. Depends on [002](002-api-and-package.md) and [003](003-http-and-errors.md). Streaming lifecycle is owned by [006](006-http-streaming.md).

## Deliverables

`OpenAIProvider` and buffered synthesis through `generate_speech`. Direct `POST https://api.openai.com/v1/audio/speech`, bearer key from explicit configuration or `OPENAI_API_KEY`. No OpenAI SDK dependency.

Model table: `gpt-4o-mini-tts` (default), `tts-1`, `tts-1-hd`; all support HTTP audio streaming and max 4,096 input characters. Instructions are supported only by `gpt-4o-mini-tts` in this initial table. Current docs also list `gpt-4o-mini-tts-2025-12-15`; defer that additional model until separately checked rather than inventing capabilities for arbitrary strings.

## Request contract

Default request:

```json
{
  "model": "gpt-4o-mini-tts",
  "input": "Hello from Python!",
  "voice": "alloy",
  "stream_format": "audio"
}
```

- Native options are copied first; canonical `model`, `input`, and `voice` overwrite conflicting values.
- Enforce binary `stream_format="audio"` for generation and streaming. Reject `"sse"` before HTTP; parsing OpenAI SSE is deferred, not equivalent to audio bytes.
- Omitted output uses native `response_format` or the provider MP3 default. Explicit `AudioOutput` controls format. Common formats: MP3/WAV/PCM; native options may also request documented Opus/AAC/FLAC, with correct media types.
- Reject invalid `response_format`, unknown output modes, nonnumeric/nonfinite speed, and speed outside 0.25–4.0 before sending. Unknown JSON-serializable native options can be forwarded unless they conflict with protected fields or change the response protocol.
- Canonical and native `instructions` must be strings. Combine nonblank canonical then native instructions with `"\n\n"`, matching upstream's order when both are supplied. Combined maximum is 4,096 characters per current API reference. Blank-only instructions are omitted; nonblank instructions on `tts-1`/`tts-1-hd` fail locally.
- Text is sent verbatim, including punctuation/whitespace/bracket tags. Do not call upstream's tag-to-instructions algorithm in milestone one.
- String voice IDs only initially. Accept any nonblank string, leaving account/model voice availability to the provider; no extra voice-list request per synthesis. Custom voice objects and creation are deferred.

## Audio contract

| Requested native format | Result media type |
| --- | --- |
| MP3 / default | `audio/mpeg` |
| WAV | `audio/wav` |
| PCM | `audio/pcm;rate=24000` |
| Opus | `audio/opus` |
| AAC | `audio/aac` |
| FLAC | `audio/flac` |

PCM is signed 16-bit little-endian at fixed 24 kHz, with no WAV header. Normalize bare `audio/pcm` using that contract. No local re-encoding, rate conversion, or PCM-to-WAV wrapping.

The reference exposes only 24 kHz for explicit common-output sample-rate requests. Accept `None` or 24,000; reject other rates instead of promising resampling. Do not add unsupported `sample_rate` fields to the OpenAI request.

Check response MIME against the request (allow documented equivalent MIME aliases and generic binary/missing MIME); explicit conflicting audio MIME is a terminal contract error, not silently relabeled. Nonempty JSON/HTML/SSE is an error even on 200. Empty binary response enters the shared buffered retry classification.

## Acceptance checks

- Exact endpoint/headers/body for bare/default and all three models; custom base URL preserves `/v1` prefix.
- Model/input/voice precedence cannot be replaced through provider options; mappings are unchanged.
- Instructions combination, length, blank omission, older-model rejection, and verbatim tagged text.
- 4,096 characters accepted; 4,097 rejected without requests; Unicode counts follow 002.
- MP3/WAV/PCM and native Opus/AAC/FLAC media mapping; bare PCM and omitted/generic MIME; conflicting/non-audio MIME errors.
- Unsupported rates, invalid speed, SSE mode, missing keys, 401/429/503, malformed provider errors, and empty audio use shared rules.
- Byte-for-byte preservation of mocked audio; no decoder or codec dependency.

## Sources and differences

Pinned upstream: `src/providers/openai/index.ts`, `src/__tests__/openai-speech-model.test.ts`, `openai-sample-rate.test.ts`, `openai-stream.test.ts`, `instructions-contract.test.ts`, and `openai-instructions.test.ts` (last documents deferred tag behavior).

Current documentation reviewed 2026-09-30:

- [Create speech](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)
- [TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech)

Explicit audio response mode, rejection of unsupported native instructions, stricter MIME checks, and deferred tag processing are intentional differences. Examples must remind applications to disclose AI-generated speech to users as required by OpenAI's policies. Live OpenAI verification is optional for the first completion gate; mark it unverified until actually run.
