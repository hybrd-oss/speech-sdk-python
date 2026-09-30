# 008 — Clone-and-run provider smoke tests

Status: runner and offline checks implemented; live xAI gate **FAIL / BLOCKED** after two authorized failed runs (`speech-vqx.11` / `speech-vqx.21`), with [diagnostic evidence in 007](007-validation-and-xai-e2e.md). Live OpenAI and manual listening remain unverified. Depends on [004](004-openai-tts.md), [005](005-xai-tts.md), and [006](006-http-streaming.md). Used by the validation gate in [007](007-validation-and-xai-e2e.md).

## Goal

Anyone with [uv](https://docs.astral.sh/uv/getting-started/installation/) and their own provider key can clone the repo, install it, and verify real buffered and streamed speech. No contributor accounts, test framework, audio tools, Node runtime, or hosted HYBRD service required.

## Deliverables

- One runnable `smoke/run.py` using `argparse`, `asyncio`, and the installed `speech_sdk` public API.
- Two real checks per provider: buffered WAV generation and streamed WAV generation, including non-silent PCM validation from 007.
- README instructions for setup, key configuration, provider selection, results, costs, and saved audio.
- No duplicate xAI-only E2E implementation: the xAI smoke selection **is** the required E2E gate.
- Keep `smoke/` outside default unit-test discovery. Importing the runner must never make requests; execution happens behind a main guard.

## Clone-and-run contract

Install uv first; use it to manage Python and the environment. Clone-and-run commands (checkout `feat/two-provider-tts` until merged):

```sh
git clone https://github.com/hybrd-oss/speech-sdk-python.git
cd speech-sdk-python
git checkout feat/two-provider-tts
uv sync --frozen --no-dev --python 3.11

# Set either or both using your own credentials:
export XAI_API_KEY="your-xai-key"
# export OPENAI_API_KEY="your-openai-key"

# Runs only providers with nonblank configured keys:
uv run --frozen --no-dev python smoke/run.py

# Explicit provider: missing key is an error, not a skip:
uv run --frozen --no-dev python smoke/run.py --provider xai
uv run --frozen --no-dev python smoke/run.py --provider openai
```

Windows PowerShell uses the same clone, uv sync and uv run commands; set keys with `$env:XAI_API_KEY = "your-xai-key"` and `$env:OPENAI_API_KEY = "your-openai-key"` instead of POSIX `export`. See the [README PowerShell setup](../README.md#clone-install-run). Do not add `python-dotenv`: standard environment variables suffice. Never commit keys or include real keys in screenshots/reports. An `.env` file alone is not automatically loaded.

## Selection and execution

- `--provider` accepts `xai` or `openai`. Omission selects all providers with nonblank environment keys.
- Preflight configuration before creating artifacts or opening HTTP connections. Explicit provider with missing/blank key fails. Default mode with no keys fails and tells the user which environment variables to set.
- In default mode, visibly report unconfigured providers as `NOT RUN (key not set)`. They are not passing checks; a successful configured xAI run does not imply OpenAI was verified.
- Run providers/checks sequentially with fixed short text, `max_retries=0`, 60-second network-phase timeout, and a 90-second overall deadline per check.
- At most two synthesis requests per selected provider on the happy path, with no retries. No paid refusal/invalid-key/limit tests: those belong offline.
- Ordinary check failures are recorded; continue remaining checks so the user sees both buffered/streaming results and all selected providers. Ctrl-C/cancellation interrupts promptly; do not hide it as an ordinary failed assertion.
- No automatic playback, voice cloning, model/voice discovery, STT, or long-form synthesis. Saved WAVs allow optional manual listening.

| Provider | Model | Voice | Native options | Output |
| --- | --- | --- | --- | --- |
| xAI | `xai/grok-tts` | `eve` | `language="en"` | WAV, 24 kHz |
| OpenAI | `openai/gpt-4o-mini-tts` | `alloy` | None required | WAV, 24 kHz |

Use the fixed nonsensitive text and audio assertions in 007. OpenAI applications must disclose that speech is AI-generated; explain this beside its sample artifacts.

## Output and exit codes

Save to ignored `artifacts/smoke/<provider>/buffered.wav` and `streamed.wav`. Only finalize a new artifact after a complete response passes validation; on failure remove partial output and don't present an older artifact as this run's success. Overwrite previous successful artifacts on a passing run.

Print a concise human-readable result for each check: provider/model, mode, PASS/FAIL, byte count, actual audio duration, sample rate, channels, and artifact path. Label timings correctly: buffered full-call latency versus streaming setup latency; never claim header arrival is audio TTFB.

- **0:** at least one provider was selected and all selected checks passed.
- **1:** any live request/audio validation check failed.
- **2:** invalid CLI arguments or missing required key/no configured providers.
- Interrupted runs are not successful; use conventional interrupt handling/nonzero exit status.

Failure output uses safe SDK summaries, never dumps authorization, keys, request text, or raw response details by default. No persistent JSON result schema or dashboard needed.

## Offline acceptance checks for the runner

- CLI provider selection, configured-key detection, explicit missing keys, default no-key failure, and exit codes require no network.
- Mock only the runner's public SDK calls to validate reporting/artifact cleanup; actual provider request contracts remain covered by 004/005 tests.
- Default `unittest` discovery never executes smoke calls, even if keys are present.
- A failed/empty/invalid WAV cannot produce PASS or a zero exit code; absent providers are clearly marked not run.
- Failed stream does not leave a partial artifact or report stale output; no automatic retries/extra paid requests.
- The exact documented clone/install/run path is checked manually before milestone completion, at minimum with xAI.

## Completion relationship

A successful `uv run --frozen --no-dev python smoke/run.py --provider xai` verifies both real xAI public API paths and satisfies 007's live xAI requirement. Record the run in 007. OpenAI's smoke checks must exist and have offline runner coverage even when its live verification is unavailable; report that status honestly.
