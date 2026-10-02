# 012 — Composite result inspection

Status: implemented; independent QA/review and fresh composite wheel/type verification **PASS** (2026-10-02). Final commit and exact-HEAD CI remain **PENDING**.
Build on [002](002-api-and-package.md), [003](003-http-and-errors.md),
[006](006-http-streaming.md), [010](010-azure-openai-tts.md) and
[011](011-pronunciation-substitutions.md). Applies to OpenAI, xAI and Azure OpenAI,
both buffered synthesis and context-managed streaming.

## Additive public contract

Append these fields, in this order, after all existing fields on both frozen
`SpeechResult` and `SpeechStream` dataclasses:

```python
request: RequestDetails | None = field(default=None, repr=False)
response: ResponseDetails | None = field(default=None, repr=False)
pronunciations: SubstitutionResult | None = field(default=None, repr=False)
```

Keep every existing field, positional order, required argument, default and repr
behavior unchanged, including audio, media type, provider, model, metadata,
`provider_metadata=None` and `warnings=()`. Existing positional construction works
without supplying the new fields. Public synthesis signatures do not change.

Export `RequestDetails` and `ResponseDetails` from `speech_sdk.types` and package
root, including their `__all__` lists. Both use `@dataclass(frozen=True, repr=False)`:

| Type | Fields |
| --- | --- |
| `RequestDetails` | `method: str`, `url: str`, `content: bytes` |
| `ResponseDetails` | `status_code: int`, `headers: Mapping[str, str]` |

Use the existing `speech_sdk.pronunciations.SubstitutionResult`, not a wrapper or
shadow type; its module export and offline repr remain unchanged.
`ResponseDetails` copies its supplied mapping into a new dictionary wrapped in
stdlib `MappingProxyType`: neither assignment through the exposed mapping nor
later mutation of the source can change the snapshot.

Successful SDK synthesis populates request and response details; their `None`
defaults support backward construction, not missing successful HTTP inspection.
No live HTTPX request, response or client object is exposed in the new fields.

## Request and response snapshots

`RequestDetails.method` is `"POST"`. Take `url` and `content` directly from the
validated `PreparedRequest`, including Azure's prepared API-version query.
`content` is the exact SDK-prepared outbound JSON bytes, not parsed/re-serialized
JSON, a reconstructed payload, or the original untransformed text. Preserve
provider wire bodies, model/voice precedence, options, instructions, output,
authentication and transport behavior. Do not copy any request headers.

This is an **SDK-prepared request snapshot**, not a forensic wire capture:
caller HTTPX hooks or custom transports may mutate the request after preparation;
those mutations are not reflected. Do not add hook interception to capture them.

Capture status and selected headers from the final accepted response after the
existing status/MIME checks and any setup retries. Buffered success also retains
its existing body-read/empty-audio validation before publication. Earlier failed
attempts do not appear; do not add retry history, callbacks or timestamps.
Keep selected header values as supplied strings, without interpreting billing,
rate-limit counts or retry timing. Missing allowed headers are simply absent.

The finite, case-insensitive response-header allowlist is exactly the following;
normalize retained names to lowercase (no arbitrary `x-*` prefix matching):

```text
content-type
content-length
request-id
x-request-id
apim-request-id
x-ms-request-id
retry-after
x-ratelimit-limit-requests
x-ratelimit-limit-tokens
x-ratelimit-remaining-requests
x-ratelimit-remaining-tokens
x-ratelimit-reset-requests
x-ratelimit-reset-tokens
```

All other headers are excluded by construction, including `Set-Cookie`, `Cookie`,
`Authorization`, `api-key` and their compact/hyphen/underscore/case aliases.
No response body snapshot or generic success-JSON parser: current adapters return
binary audio. Do not duplicate raw audio in inspection or provider metadata.

## Pronunciation report and shared flow

Retain the actual result of the existing shared `api._prepare` substitution:

```text
resolve model
  _prepare
    pronunciations omitted -> no report, unchanged text path
    pronunciations supplied -> merge once, substitute once, retain result
    provider.prepare(final text) -> validate final limits, prepare JSON bytes
    retain original input_chars and optional report on prepared request
  open_response -> existing retries reuse the same prepared bytes
  shared details builder -> request snapshot + accepted response snapshot
  return SpeechResult / yield SpeechStream
```

Appending `pronunciations: SubstitutionResult | None = None` to `PreparedRequest`
is sufficient plumbing; do not introduce a new context/wrapper. Direct provider
`prepare` calls have no pronunciation report and unchanged signatures. Both
synthesis modes use the same small details builder and the retained report;
inspection must not run a second matcher pass or make an extra HTTP request.

Omitted/`None` rules produce `pronunciations=None`. Explicit empty, all-blank or
nonmatching valid rules produce a report with unchanged text and `edits=()`.
Matches expose the actual final text and the existing immutable edit tuple with
half-open Python Unicode-code-point original/output ranges. Existing validation,
normalization, longest-match ordering, no chaining and tag behavior stay intact.
`metadata.input_chars` remains `len(original caller text)` in both modes. Providers
still validate final text: OpenAI/Azure 4,096 code points; xAI 60,000. Expansion
past the limit fails locally; oversized original text may contract and succeed.

## Privacy and streaming lifetime

Inspection is explicit and sensitive: prepared URLs, text, instructions/options,
pronunciation text/edits and even allowed response-header values may be private
or untrusted. Suppress the new fields in result/stream repr and suppress field
rendering in both details dataclass reprs. No automatic printing, logging or
rendering of these values. This is not a promise to redact explicit attribute
access, serialization, caller logging, or the existing offline report repr;
do not globally hide existing audio/provider/model repr fields.

All three fields are available at stream context entry, before any audio read.
Inspecting them must not read, prefetch, join or buffer the successful stream body.
Snapshots remain usable after context exit; the audio iterator remains single-pass
and invalid outside its context. Preserve consumer-driven backpressure, cleanup,
caller-owned clients, existing retry boundaries and no replay after publication.
A published stream snapshot confirms accepted response headers, not eventual
nonempty audio or successful completion. Existing error handling stays unchanged.

## Required offline acceptance

Use new `tests/test_result_details.py` with MockTransport for all three providers
in both modes. Check exact prepared JSON bytes/URL/method and unchanged native
options/auth, final status after retries, every allowed header and excluded aliases,
lowercase names, copied read-only mappings, safe nested repr and public exports.
Cover old positional/default constructors, no report for `None`, explicit
empty/all-blank/miss reports, matching equality with offline substitution output,
original character counts, final provider limits and once-only substitution across
retries. Prove no client objects leak or extra requests occur.

Use a controlled `httpx.AsyncByteStream` to prove inspection performs zero reads
at context entry, preserves backpressure and remains valid after context exit
while audio is invalid; an already-loaded MockTransport body alone is insufficient.
Preserve existing regression tests and add the focused module to explicit CI
runtime/copy-wheel subsets. No dependency, pin, lock, cooldown or tool-policy changes.

Checks use the approved mature uv executable (>=0.12.18), `uv run --frozen`, a
credential-free environment, `UV_OFFLINE=1` and OS-denied network. No provider calls,
dev-tool installs or ordinary pytest invocation. Implementation self-verification
(`speech-75u.2`) recorded **10 focused**, **165 checkout**, **161 runtime-selection**
tests and **13 all-file hooks** PASS under those conditions; logs are
`/tmp/speech-75u.2-{focused,full,runtime,hooks}.log`, with red evidence in
`/tmp/speech-75u.2-red.log`. These are implementation self-checks only, not
independent QA/review, fresh composite wheels, external typing checks or CI evidence.
Those implementation logs are historical; subsequent independent evidence follows.
No audio helpers, codecs, PCM/WAV utilities or cancelled audio draft restoration.

## Verified offline evidence

Recorded 2026-10-02 on `28b31c9236a8885c29de94ce34fc95f9f7f039d2`
plus the reviewed composite changes, with credential-free mature uv 0.12.18,
frozen dependencies, `UV_OFFLINE=1` and OS-denied network:

- Independent QA (`speech-75u.4`) and source/spec/security review (`speech-75u.5`)
  are **CLOSED/PASS**: each reran **165 checkout** and **10 focused** tests.
  QA also passed **161 runtime-selection** tests, **13 all-file hooks** and
  detected **8 in-memory mutants**; all 155 prior test methods remained unchanged.
  Logs: `/tmp/speech-75u.4-{focused,full,runtime,hooks,mutations,provenance}.log`
  and `/tmp/speech-75u.5-{focused,full,independent}.log`.
- Fresh noneditable wheel checks (`speech-75u.6`) passed **161 runtime tests each
  on Python 3.11 and 3.14** in clean environments with hash-locked dependencies.
  `/tmp/speech-sdk-composite-wheel.Mhry2C` records build/setup/runtime logs.
  Whole-SDK source byte equality, packaged README equality at build time,
  root exports, type metadata/`py.typed`, Jellypod LICENSE attribution and
  exclusion of private audio artifacts all **PASS**.
- External strict mypy against the installed wheel passed the valid composite
  `assert_type` consumer and rejected the invalid consumer with **4 intended
  errors** (request/response argument types, unguarded optional request access
  and read-only header assignment): `/tmp/speech-sdk-composite-consumer-types`.

These are actual offline gates, not provider verification or CI/publication
claims. Final commit and exact-HEAD CI remain **PENDING**; main/release planning
is separate future work, not part of this documentation gate.
