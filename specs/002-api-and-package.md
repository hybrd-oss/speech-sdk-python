# 002 — Python API and package

Status: implemented with offline public API/package checks; the required live xAI buffered + streamed WAV gate passed on `3adfb10`, as recorded in [007](007-validation-and-xai-e2e.md). Live OpenAI remains unverified. Dependencies: none. Scope: shared contracts needed by both adapters, not provider HTTP implementation.

## Deliverables

- Minimal `pyproject.toml`, `src/speech_sdk/`, explicit public exports, and `py.typed`.
- Python 3.11+, `httpx` runtime dependency, standard-library dataclasses/typing/unittest. No vendor SDKs, Pydantic, or audio libraries as runtime dependencies. Strict developer gates are separate development-only dependencies in 009.
- Use a conventional build backend; no custom build scripts. Distribution name must be checked before publication; import name is `speech_sdk`.
- Include README attribution and LICENSE in source/wheel artifacts.

## Public contracts

Implemented public signatures (ellipsis below abbreviates implementation):

```python
async def generate_speech(
    *, model: str | ResolvedModel, text: str, voice: str,
    output: AudioOutput | None = None,
    instructions: str | None = None,
    provider_options: Mapping[str, object] | None = None,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_retries: int = 2,
    headers: Mapping[str, str] | None = None,
) -> SpeechResult: ...

# Same keyword arguments; returns an async context manager, not an awaitable.
def stream_speech(...) -> AsyncContextManager[SpeechStream]: ...
```

- `OpenAIProvider(api_key=None, base_url=...)` and `XAIProvider(...)` expose `.model(model_id=None) -> ResolvedModel`. No provider factories on top of those classes.
- An injected HTTP client is call-level and caller-owned. Provider instances hold configuration, not secretly owned live clients.
- `AudioOutput(format="mp3" | "wav" | "pcm", sample_rate=None)` describes **native** output only. Omission preserves provider options/defaults. Unknown formats or unsupported rates fail locally.
- `AudioData(data: bytes, media_type: str)` and `SpeechResult(audio, provider, model, metadata, provider_metadata, warnings)` are dataclasses.
- Buffered metadata exposes `input_chars` and `latency_ms` (full synthesis call including retries/body reading, excluding caller file writing).
- `SpeechStream` exposes `audio: AsyncIterator[bytes]`, `media_type`, `provider`, `model`, `metadata`, `provider_metadata`, and `warnings`. Streaming metadata exposes `input_chars` and `setup_latency_ms`; first-byte/completion timing is not required in milestone one.
- Empty warnings use an empty tuple; absent provider metadata uses `None`. Never fabricate audio duration, billing, token counts, or timestamps.
- An internal provider `Protocol` covers model metadata and request construction/response interpretation shared by the two adapters. Keep HTTP execution/retries in one shared path; buffered and streaming request bodies must not drift.

## Resolution and validation

- Accept `openai`, `xai`, and `provider/model-id`; split on the first slash. Bare names select the provider defaults from 004/005.
- Accept any nonblank string model ID, preserving the original value including further slashes. Configured `.model(id)` takes the ID directly without provider-prefix parsing; `.model(None)` selects the default, but an explicit `ResolvedModel` ID must be a nonblank string. Reject unknown providers, blank IDs and invalid types before HTTP. No model whitelist, dynamic plugin discovery or network model listing.
- Export flat convenience tuples `OPENAI_MODELS`/`XAI_MODELS` and strings `DEFAULT_OPENAI_MODEL`/`DEFAULT_XAI_MODEL`; these do not restrict accepted IDs. Error summaries omit untrusted model IDs while explicit `model` attributes retain them. xAI IDs are metadata only, not REST backend selectors (005).
- Require string text/voice with non-whitespace content. Validate using `.strip()` but send original text and voice without silent normalization.
- Count characters with Python `len(text)` (Unicode code points). Record the difference from JavaScript UTF-16 `.length`; do not add a tokenizer merely to imitate it.
- `max_retries` is a nonnegative integer, not a bool; sample rates are positive integers, not bools. Reject invalid option types, nonfinite numbers, and non-JSON-serializable provider options before requests.
- Explicit function `api_key` overrides a configured provider key, which overrides the provider environment variable. Explicit blank keys fail rather than falling back. Configuration reprs must not expose keys.
- Provider options are copied, never mutated. Canonical text/model/voice override same-named native passthrough fields. Explicit `output` controls codec/rate; retain compatible extra format fields and reject conflicts or meaningless values.
- Canonical and provider-native instructions use the provider rule in 004/005. Unsupported instructions fail, regardless of which entry point supplied them.
- Do not expose deferred feature parameters; ordinary Python unexpected-keyword errors suffice.

## Acceptance checks

Standard-library tests cover defaults and configured model resolution, key precedence/blank keys, invalid identifiers, Unicode limits, whitespace validation without text mutation, invalid option values, and immutable caller mappings. These checks must run without opening a network connection.

Package setup and real speech exports are implemented. Do not export stubs that return fake audio or raise `NotImplementedError` as finished work.

## Reference

Pinned upstream: `src/resolve-provider.ts`, `src/speech-provider.ts`, `src/generate-speech-result.ts`, `src/stream-speech-result.ts`, and `src/__tests__/resolve-provider.test.ts`. Python resource ownership and nonblank model-ID validation are intentional differences; model availability is not locally whitelisted.
