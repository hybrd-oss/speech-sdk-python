# 010 — Azure OpenAI v1 text-to-speech

Status: **implemented; offline acceptance PASS, live Azure UNVERIFIED.** Implementation, independent QA (`speech-b6t.3`) and review (`speech-b6t.4`) are complete, including URL-path and privacy regressions. One authorized live buffered attempt returned HTTP 404/deployment unavailable; no streamed request, audio, discovery, listening or STT verification followed. See [offline evidence](#offline-evidence) and [live attempt](#live-attempt). Final independent documentation/evidence review (`speech-b6t.10`) **PASS**; no publication or full parity is claimed. Depends on [002](002-api-and-package.md), [003](003-http-and-errors.md), [004](004-openai-tts.md) and [006](006-http-streaming.md).

## Scope

Add Azure **OpenAI** TTS to the existing buffered/streamed public API, not Azure AI Speech, SSML or the Speech SDK. Use direct JSON HTTP through the existing `httpx` transport. Initial support is modern **v1 preview with API-key authentication only**: no Entra/token acquisition, Azure/OpenAI SDK dependency, legacy dated deployment-path adapter, automatic fallback, plugin/factory or configuration registry. No publishing or live calls are required to implement this contract.

## Public API and configuration

Export `AzureOpenAIProvider` from both `speech_sdk.providers` and `speech_sdk`. Its configuration follows the existing frozen provider pattern:

```text
AzureOpenAIProvider(
    api_key: str | None = None,
    base_url: str | None = None,
    api_version: str = "preview",
)
# name == "azure" (not caller-settable)
# model(model_id: str | None = None) -> ResolvedModel
```

This is the implemented signature. Existing `generate_speech` and `stream_speech` signatures/results remain unchanged; accept this configured provider's `ResolvedModel` and the `azure` string prefix. Results/errors identify the provider as `"azure"` and the model as the exact deployment ID.

| Setting | Selection (first supplied, not first truthy) | Validation/default |
| --- | --- | --- |
| Base URL | Explicit `base_url` > `AZURE_OPENAI_BASE_URL` > `AZURE_OPENAI_ENDPOINT` | Required; no public-resource default |
| API key | Explicit public-call `api_key` > configured provider `api_key` > `AZURE_OPENAI_API_KEY` | Reuse visible-ASCII key validation and `MissingApiKeyError` |
| Deployment | Explicit `.model(model_id)` / `azure/<deployment>` > `AZURE_OPENAI_DEPLOYMENT_NAME` for `.model(None)` / bare `azure` | Required; no invented deployment |
| API version | Configured `api_version` | Default `"preview"`; any nonblank built-in `str`, no version whitelist or environment fallback |

`None` means omitted only where allowed. A present blank explicit or environment value fails locally, never falls through to a lower-priority setting. Azure must not read `OPENAI_API_KEY`, `OPENAI_BASE_URL` or `OPENAI_API_VERSION`. Resolve/validate URL and version during provider construction; read deployment environment at `.model(None)`; resolve keys during request preparation. All validation precedes obtaining an SDK-owned HTTP client.

Missing URL/deployment raises a static safe `ValueError` naming the required configuration, never its value. Deployment and version use `type(value) is str`, rejecting subclasses, other types and blank strings without echoing their contents. Preserve valid strings verbatim; do not strip, ASCII-restrict or interpret deployment IDs. Version is encoded as a single query value, not concatenated into a URL.

`azure/<deployment>` splits only on the first slash, using existing string resolution. Thus `azure/org/future-tts` sends `"org/future-tts"`; configured `.model("org/future-tts")` takes the ID directly. Bare `azure` requires the deployment environment variable; `azure/` fails rather than falling back. `OPENAI_MODELS` and `DEFAULT_OPENAI_MODEL` remain convenience strings for OpenAI, not Azure deployment restrictions/defaults. Add no `AZURE_MODELS` or fake default deployment constant.

## URL and wire contract

Accept an HTTP(S) resource root or a v1 API root, with optional trailing slashes. Preserve trusted custom path prefixes and normalize only the suffix:

```text
resource root: https://example.invalid[/prefix]
  → https://example.invalid[/prefix]/openai/v1/audio/speech?api-version=preview
v1 API root: https://example.invalid[/prefix]/openai/v1[/]
  → https://example.invalid[/prefix]/openai/v1/audio/speech?api-version=preview
```

Append `audio/speech` if the trimmed path ends in `/openai/v1`; otherwise append `openai/v1/audio/speech`. A deployment never enters the URL, so deployment path quoting/encoding is unnecessary. No legacy `/openai/deployments/...` route or hidden route/version fallback.

Reuse shared URL safety: scheme/host required; reject userinfo, invalid ports, whitespace/control characters, backslashes, arbitrary base queries and fragments. Do not weaken the generic base-URL validator to admit `?api-version=...`; build the one adapter-owned `api-version` query using structured URL/query APIs after base validation. Invalid URL/version errors and provider configuration `repr` must not expose endpoint/configuration values or credentials; key and base URL fields are excluded from `repr`. Custom destinations intentionally receive the key: callers must trust them.

Both public paths use `POST`, canonical `api-key`, `Content-Type: application/json` and existing SDK user-agent. With deployment `"org/future-tts"`, text `"Hello from Python!"`, voice `"alloy"` and omitted output/options, the exact decoded JSON is:

```json
{
  "model": "org/future-tts",
  "input": "Hello from Python!",
  "voice": "alloy",
  "stream_format": "audio"
}
```

The deployment is a selector in the JSON `model` field, not an underlying model-type assertion. Native options are copied first; canonical `model`, `input`, `voice` and binary `stream_format` win. Caller mappings remain unchanged. No sample-rate field is sent.

## Reuse and validation

Share OpenAI's body/options/output helpers rather than copy its adapter. Keep OpenAI's existing known-model instruction rejection unchanged, but make the shared instruction helper able to skip that inference for Azure deployments. Even Azure deployment aliases literally named `tts-1` or `tts-1-hd` have unknown underlying capabilities: forward valid instructions and let Azure validate them. No model/voice discovery call or inferred capability table.

- Text/voice retain existing nonblank string checks; text is verbatim with a 4,096 Python Unicode-code-point limit. No tag translation or voice whitelist.
- Canonical/native instructions must be strings; combine nonblank canonical then native values with `"\n\n"`, maximum 4,096 characters; omit blank-only instructions. Azure never rejects them based on deployment spelling.
- Reuse finite numeric speed validation, inclusive 0.25–4.0; reject bools, nonnumbers and nonfinite values.
- Binary response-body protocol only. Reject SSE, timestamp envelopes and protected/protocol option aliases locally. Unknown finite JSON-serializable options may be forwarded for provider validation; no option may alter URL, query/version, auth, method or body ownership. Include `api_version`/`api-version` aliases in protected Azure options.
- Omitted output preserves native `response_format` or defaults to MP3. Common `AudioOutput` supports MP3/WAV/PCM; native formats additionally support Opus/AAC/FLAC, using [004's media mapping](004-openai-tts.md#audio-contract).
- Explicit common/native sample rate accepts only `None` or integer 24,000 (not bool). Native WAV/PCM are 24 kHz; raw PCM is signed 16-bit little-endian without a WAV header. No decoder, transcoding, resampling or PCM wrapping. Omitted MP3 does not invent rate metadata.
- Reuse shared MIME/empty-audio checks, error classification, retries/timeouts, demand-driven streaming, cancellation and response/client cleanup. Generic/missing binary MIME may use the validated format; conflicting MIME and JSON/HTML/SSE are not audio success. No retries/reconnect/replay after stream exposure.

## Shared authentication protection

Extend the existing shared header/request helper with one small internal auth choice (`"authorization"` or `"api-key"`), defaulting to existing Bearer behavior. It is not a public auth framework. Apply protection where every provider routes through it:

- Validate caller headers, then remove caller auth headers/aliases case-insensitively (including compact/underscore variants of `api-key` and `authorization`); write exactly one canonical auth header from the resolved SDK key.
- Azure sends `api-key` only, never `Authorization`. OpenAI/xAI retain `Authorization: Bearer ...` and strip caller `api-key` aliases too, preventing alternate-key leakage. Caller options cannot substitute auth fields.
- Preserve protected JSON content type and reject framing/host overrides. Keep direct `httpx.Request` construction and `client.send(..., auth=None, follow_redirects=False)` so injected auth, cookies and default headers cannot enter the request. Never mutate or close the injected client.
- No automatic logging of keys, URLs, configuration, input, bodies or raw errors. Keep credentials out of provider/prepared-request representations. Existing safe error `str`/`repr` behavior must apply to Azure, including malicious provider responses echoing keys/URLs; explicit error attributes may retain raw data for caller inspection and must not be logged.

## Offline acceptance gate for implementation

The following acceptance areas PASS in the standard-library async/`httpx.MockTransport` suites, as independently checked by QA/review. These are offline assertions, not live provider guarantees; measured execution and provenance follow below:

1. Exact URL/query/header/decoded JSON through configured and string-prefixed buffered **and** streamed public paths. Cover both roots/trailing slashes/custom prefixes, default preview and a version containing query delimiters encoded as one value; no deployment in URL.
2. Configuration precedence and fail-closed blanks: both URL environment names, explicit/configured/environment keys, explicit/environment deployments, no OpenAI environment cross-fallback; missing URL/key/deployment. Reject invalid URL/version, nonstring/subclass/blank deployment/version before owned-client creation and with zero requests; static safe errors/reprs.
3. Verbatim namespaced/Unicode/unknown deployment IDs, including `tts-1`/`tts-1-hd` aliases with forwarded instructions; canonical body precedence, instruction combination/limits, text 4096/4097, unchanged mappings, voice availability left to Azure.
4. MP3/WAV/PCM/native formats and rate/MIME mapping; speed boundaries/types, invalid formats/rates, SSE/protocol/auth/version-option aliases, finite-JSON validation. Mocked bytes remain unchanged.
5. Caller mixed-case/alternate auth and injected auth/cookies/default-header contamination cannot replace/leak credentials on any provider. Azure has only canonical `api-key`; OpenAI/xAI retain Bearer only. Configuration/key/raw-error markers absent from automatic summaries/reprs; explicit error data remains caller-controlled.
6. Shared 401/429/503/malformed/empty/MIME errors, safe provider/deployment context, retries, no replay, owned/injected preservation and cancellation/early-exit cleanup. Preserve existing OpenAI/xAI regression coverage.
7. Include `test_azure.py` in existing offline runtime and clean-wheel copy/check paths; no new dependency/workflow framework or weakened gates. Verify all 13 offline hooks (strict mypy, Ruff, Vulture, Radon <=10 included) and fresh noneditable wheels on Python 3.11/3.14; record actual counts only after execution.

The two-provider smoke CLI remains unchanged; historical [007 evidence](007-validation-and-xai-e2e.md) is not rewritten. Future Azure verification belongs here, separately dated and scoped, with explicit authorization and deployment selection, no discovery, at most one bounded buffered and one streamed request with `max_retries=0`, private credentials and ignored artifacts. No automatic playback/STT or fabricated verification.

## Offline evidence

Recorded **2026-09-30**, tested source: base **`44b1c71` plus uncommitted Azure implementation and URL/privacy fixes**, not an invented committed Azure SHA. Supersedes initial 133/129 counts; historical 007's 108/104 on `3adfb10` remains unchanged.

| Check | Result and provenance |
| --- | --- |
| Checkout suite | **134 PASS**, independently executed by QA and review |
| Azure suite | **18 PASS** (10 configuration, 8 public-path tests), independently executed |
| Hooks | **13 PASS**, implementation log `/tmp/speech-b6t-7-9/hooks.log`; independently inspected by QA/review, not independently rerun by them |
| Fresh installed runtime | **130 PASS each on Python 3.11 and 3.14**, clean noneditable wheels outside the checkout, `/tmp/speech-sdk-azure-final-wheel.Oanpho/tests-3.11.log` and `tests-3.14.log` |
| Regression strength | Original URL-suffix defect and version/deployment error disclosures independently reintroduced in memory: 134 tests each, assertion failures 2/1/1 respectively, zero errors; all three mutations killed |

QA logs: `/tmp/speech-b6t-3-rerun/` (`azure.log`, `full.log`, `runtime.log`, mutation logs and `evidence.md`). The independent checkout commands used the existing development venv, not a fresh installation or version matrix:

```sh
cd /Users/mattruiters/Code/Projects/speech-sdk-python
env -i PATH=/usr/bin:/bin HOME=/Users/mattruiters TMPDIR=/tmp \
  UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests \
  sandbox-exec -p '(version 1)(allow default)(deny network*)' \
  .venv/bin/python -B -m unittest discover -s tests -v
# Same prefix: -m unittest test_azure -v
# Same prefix: -m unittest -v test_core test_http test_openai test_azure \
#   test_xai test_api test_streaming test_smoke
```

`env -i` clears all provider keys and configuration, including `OPENAI_API_KEY`, `XAI_API_KEY`, `AZURE_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT_NAME`, `OPENAI_BASE_URL` and `OPENAI_API_VERSION`. `UV_OFFLINE=1` plus macOS OS-level `(deny network*)` enforced offline execution, not just mock intent. No credentials were inspected for offline checks.

The separate final source-wheel gate also used **`UV_OFFLINE=1`, OS network denial and provider keys/configuration unset**. The frozen build used `python -m build --installer uv`; hash-locked runtime dependencies were installed before the `--no-deps` noneditable wheel. Installed imports/exports (including Azure), `py.typed`, whole-SDK source-byte equality, LICENSE/Jellypod README attribution and exclusion of private audio/key artifacts passed. Build/install logs are in `/tmp/speech-sdk-azure-final-wheel.Oanpho/`. This verifies current runtime source; the original wheel predates documentation finalization. A separate metadata artifact at `/tmp/speech-sdk-azure-doc-final-dist` passed source-byte equality, README/LICENSE and `py.typed` checks before this status/text reconciliation.

## Live attempt

**2026-09-30**, `speech-b6t.11` closed with an attempted check, **not PASS**. Source provenance was **`44b1c71` plus uncommitted Azure changes**, tested through a fresh clean Python 3.11 installed wheel with whole-SDK source-byte equality and the existing smoke WAV validator/static-diagnostic self-check.

The user authorized one explicitly selected candidate deployment **`gpt-4o-mini-tts`**. This is a candidate name, **not an SDK default**, not proof of a deployed speech model. A working Azure Responses API does not establish a TTS deployment.

- Actual request: one buffered JSON `POST /openai/v1/audio/speech?api-version=preview`, voice `alloy`, WAV 24 kHz, text `Hello from Azure OpenAI using Python`, `max_retries=0`, **30-second network-phase timeout**, **90-second overall deadline**.
- Result: **HTTP 404**, phase `synthesis`, static category `provider-http`. An exact allowlisted provider code was safely mapped to the fixed label **`deployment-unavailable`**; HTTP 404 alone is not treated as that classification. No raw provider code/body, key, URL or headers were logged.
- **Buffered attempts: 1; streamed attempts: 0 — STOP on buffered failure.** No HTTP fallback, other deployment probe, deployment creation/discovery/control-plane call or Responses request occurred. No audio or `.part` artifact remains; no playback/STT was performed.
- The configured key/resource URL reached Azure. **Key validity and authorization remain unknown**: a 404 and absence of 403 do not verify authorization. Live synthesis remains **UNVERIFIED**, with an external deployment-configuration blocker, not a blocked/unimplemented SDK.

Safe outcome: `/tmp/speech-azure-live.IQU3nx/outcome.log`; private bounded harness/build files are in that scratch directory. Future paid verification needs renewed explicit scope and a working speech deployment. Offline PASS and an authorized attempted call do not imply live PASS.

## Official sources and uncertainty

Retrieved/reviewed **2026-09-30**:

- [Azure OpenAI v1 preview image/audio/video reference](https://learn.microsoft.com/en-us/azure/foundry/openai/reference-preview-latest), updated 2026-06-24: explicitly lists `POST {endpoint}/openai/v1/audio/speech?api-version=preview`, API-key auth, input 4096, speed 0.25–4.0 and binary/audio versus SSE modes. Its speech body caption says `multipart/form-data`, but its speech example is JSON. This contract deliberately uses JSON (also shown by the classic REST quickstart), not that inconsistent multipart caption; the illustrative JSON response wrapper is not an audio envelope to parse. Live behavior remains unverified.
- [v1 API lifecycle](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle): v1 GA generally removes required dated versions, uses `/openai/v1` roots, and REST API-key examples use `api-key`. That general GA guidance does not override the speech preview route selected here; explicitly send `api-version=preview` by default. Configuring another string is not a compatibility claim.
- [Classic Azure OpenAI TTS quickstart](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/text-to-speech-quickstart): explains endpoint/key/deployment environment variables and that deployment names need not equal underlying model names. Its JSON REST example uses a legacy dated deployment-path route; do not copy that route, SDK dependencies, placeholder deployment defaults or region/voice lists as current v1 restrictions.

Preview availability, voice/model capabilities, codecs and actual deployed-resource behavior are provider-validated and not exhaustively live-verified. No Azure AI Speech or full parity claim is made. Applications must disclose AI-generated voices to users; keys and inputs go directly to the configured Azure destination under its policies.
