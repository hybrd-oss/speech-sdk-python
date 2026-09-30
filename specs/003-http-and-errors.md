# 003 — HTTP lifecycle, errors, and retries

Status: implemented with offline lifecycle/error/retry checks; live verification remains pending 007. Depends on [002](002-api-and-package.md). Used by buffered generation and streaming for both providers.

## Deliverables

One shared HTTP execution/error/retry path using `httpx` and `asyncio`. Provider adapters supply endpoint, body, and format interpretation. No retry dependency or transport framework.

## HTTP contract

- JSON POST, bearer authentication, `Content-Type: application/json`, and a HYBRD SDK user-agent identifying this implementation, not impersonating Jellypod.
- Normalize trailing slashes on configured base URLs; preserve path prefixes such as `/v1`. Reject URLs without an HTTP(S) scheme/host or with embedded credentials/query/fragment. Custom endpoints are a deliberate trust boundary: credentials will be sent there.
- Do not follow redirects, including on injected clients: redirects produce a terminal SDK error instead of forwarding credentials.
- Merge custom headers case-insensitively, then protect authorization/content-type and reject attempts to change host/content-length/transfer-encoding. These wire fields belong to the transport. Do not log request bodies, custom sensitive headers, or API keys.
- SDK-created client: create once per public invocation, reuse across its attempts, close on every exit. Injected client: caller owns it; never close or mutate global client defaults.
- Apply explicit per-request timeout defaults (60 seconds for HTTPX network phases). Accept positive finite seconds or a finite positive `httpx.Timeout` configuration. This is not an overall synthesis deadline; callers can use `asyncio.timeout(...)` for one. Document longer timeouts for large xAI inputs.
- Response objects close before retries and on every completion/failure path. Shared streaming execution must not call `.aread()` on a successful binary response.

## Error surface

Small hierarchy under `SpeechSDKError`:

- `MissingApiKeyError`: provider and expected environment variable; never includes the supplied key.
- `ProviderError`: provider/model, status code if present, provider code, request ID, parsed details, raw response text, `retryable`, optional retry-after seconds, and stage (`synthesis`).
- `NoSpeechGeneratedError`: empty input (terminal) or empty buffered/streamed response; transient buffered emptiness may be retryable, exposed-stream emptiness is not.
- Local unknown model, incompatible output, and invalid options use `ValueError`/`TypeError` rather than one new exception class per validation rule.

Preserve eligible network errors as causes (`raise ... from error`). Cancellation remains `asyncio.CancelledError`, not an SDK error. Do not catch `BaseException` except for resource cleanup that immediately re-raises.

For failed HTTP responses, parse common `error.message/code`, string `error`, top-level `message/code`, and `detail` shapes when present. Preserve unknown valid JSON in details and malformed text as raw text. Request-ID headers (`request-id`, `x-request-id`) take precedence over body `request_id`/`requestId`. Header lookups are case-insensitive.

Default `str(error)`/repr exposes provider, model, status/code/request ID and a safe summary, **not raw provider response/message/details**, which may echo input or secrets. Store those as explicit attributes for caller inspection. No automatic logging.

A 2xx JSON/HTML/SSE response is not audio success in milestone one; raise a terminal response-contract error. Adapters classify expected audio MIME types; missing/generic `application/octet-stream` may use the validated request format. Do not heuristically JSON-parse arbitrary PCM/audio bytes. Full codec decoding is outside the SDK; live WAV validation belongs to 007.

## Retry policy

Default `max_retries=2` means at most **three attempts**, not two attempts. Validation happens outside the retry loop.

| Failure | Retry? |
| --- | --- |
| HTTP 429 | Yes unless an explicit provider code establishes a terminal refusal/quota condition |
| HTTP 500–599 except 501 | Yes unless a known terminal refusal overrides status |
| HTTP 501, redirects, other 4xx | No |
| Explicit content refusal / authentication / invalid input | No |
| Connect/read/write network failures or timeouts, remote protocol failure | Yes before stream exposure |
| Local protocol/configuration/serialization/programmer exception, pool timeout | No |
| Empty buffered binary body | Yes, up to budget |
| Any failure/empty EOF after streaming response is exposed | No |
| Cancellation | Never |

Do not infer refusal from arbitrary substring searches over user-echoing messages. Add exact verified provider codes and fixtures when available; unknown codes use the status rule.

For failed retry number `n` starting at 0, randomized exponential delay is `min(2**n, 60) * uniform(1, 2)`, capped at 60 seconds. Parse `Retry-After` as nonnegative numeric seconds (including fractional values, as upstream accepts) or HTTP-date with `email.utils.parsedate_to_datetime`; past dates clamp to zero, invalid/nonfinite values fall back. Effective delay is the larger of backoff and capped server delay, with up to 250 ms jitter on the server delay (so total may be up to 60.25 s). Sleep is cancellable.

Make clock/random/sleep replaceable internally for tests; do not expose a public retry engine. On exhaustion, raise the last classified error with context, not an unrelated generic error.

An ambiguous failed POST may already have generated/billed speech. Document duplicate-cost risk and `max_retries=0`; do not invent idempotency support.

## Acceptance checks

Use `httpx.MockTransport` and standard-library async tests. Cover owned/injected client closure, response closure before another attempt, protected headers, prefix-preserving URL joining, redirect rejection, malformed errors, request-ID precedence, safe error reprs, statuses in the table, all retry budget boundaries, no real sleeping, and cancellation during request/backoff.

Record attempt counts and delays. Verify invalid input sends zero requests, 429→success sends two, persistent 503 sends three by default, 401/501/response-contract errors send one, and unexpected `RuntimeError` is never retried. Tests for timeout behavior should inject HTTPX timeout failures; mocks alone do not enforce real socket deadlines.

## Reference

Pinned upstream: `src/provider-utils.ts`, `src/retry-options.ts`, `src/errors.ts`, and `src/__tests__/provider-errors.test.ts`. Intentional differences: safe exception strings, explicit eligible Python network errors, 3xx rejection, and no arbitrary-exception retry.
