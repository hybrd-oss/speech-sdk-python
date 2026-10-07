# Contributing

**Historical milestone one, as of `3adfb10` (2026-09-30):** OpenAI/xAI public APIs had offline coverage, and real xAI buffered + streamed WAV checks **PASS**. Recorded evidence: **108 offline tests**, **13 fully offline hooks**, and **104 runtime tests against clean noneditable wheels on Python 3.11 and 3.14**; these are historical counts, not current Azure gate results. See [validation evidence](specs/007-validation-and-xai-e2e.md) for exact setup and provenance. Live OpenAI is **UNVERIFIED** (key absent). Manual listening was **NOT DONE at that milestone**; subsequently the user listened to buffered xAI and `tags-demo.wav` and said they “sound great” ([README status](README.md#status)). That subjective report is not formal streamed-audio/expected-words or STT verification; STT remains deferred. No PyPI release or full upstream parity is claimed. Smoke execution remains explicitly paid/opt-in. Status and intentional differences are in [specs/](specs/README.md).

**Current Azure extension (2026-09-30):** implemented with independent QA/review PASS: **134 offline tests (18 Azure)**, **13 offline hooks** from implementation logs independently inspected, and **130 runtime tests each against clean noneditable wheels on Python 3.11/3.14**, with `UV_OFFLINE=1`, OS network denial and provider keys/configuration unset. Tested source is `44b1c71` plus uncommitted Azure changes, not a committed Azure SHA. One authorized buffered live attempt returned HTTP 404/deployment unavailable; zero streamed requests, no audio, and authorization unknown. Live Azure is **UNVERIFIED**. See [010 offline evidence](specs/010-azure-openai-tts.md#offline-evidence) and [live attempt](specs/010-azure-openai-tts.md#live-attempt). Final independent documentation/evidence review (`speech-b6t.10`) passed; no publication is claimed.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.18 or newer** (update an older standalone installation with `uv self update 0.12.18`), then from the repository root:

```sh
uv sync --frozen --python 3.11
uv run --frozen prek install
uv run --frozen prek run --all-files
```

Use Python 3.11 for development tooling; the package targets 3.11+. `uv` installs the interpreter if needed. Tool versions and dependency hashes are committed in `uv.lock`; CI pins uv 0.12.18. Root `uv.toml` is the single source of uv policy: `required-version` enforces the minimum, and uv 0.7.8 fails fatally while parsing its unsupported relative cooldown, before even a frozen command can execute a child. Check `uv --version` before setup. No API keys or Semgrep account are needed. Initial installation downloads dependencies; the analysis itself runs locally.

`prek` is the Rust hook runner reading `.pre-commit-config.yaml`. Rust-native hooks check YAML/TOML syntax, merge markers, and private-key headers; local commands use the locked environment, with no remote hook repositories. The 13 hooks include whole-repository tool gates, even on documentation-only commits, and CI runs the same hooks. `repo: builtin` requires prek, not the Python pre-commit runner. Hooks do not rewrite files. To apply Ruff fixes deliberately:

```sh
uv run --frozen ruff check --fix .
uv run --frozen ruff format .
```

## Gates

| Gate | Policy |
| --- | --- |
| mypy | Strict mode, unreachable-code warnings; no project-wide missing-import suppression |
| Ruff | Formatting, annotations, imports, correctness, security, modernization, simplifications |
| Vulture | Fail on findings at >=60% confidence; review legitimate public/dynamic entrypoints explicitly |
| Radon | Every function/method, including nested functions, must have complexity <=10; invalid source fails |
| Semgrep | Four tested local rules: insecure TLS, dynamic execution, unsafe YAML, shell subprocesses |
| unittest | Offline standard-library checks; no credentials, paid calls, or sleep/network dependencies |
| Lock | `uv lock --check` rejects metadata/lock drift |

Semgrep uses only `.semgrep.yml`, the OSS engine, disabled metrics/version checks, and no upload. The local rule-validation/regression hook runs `semgrep scan --test --strict`: it parses rule schemas and patterns and checks all four annotated positive/negative rule tests, failing on configuration errors or mismatches. The separate full scan retains `--strict --error --disable-nosem` to block findings and errors. Do not use `semgrep scan --validate`: installed Semgrep 1.178.0 fetches registry pack `p/semgrep-rule-lints` even with a local config. The two existing local hooks need no registry or duplicate scan. After initial dependency installation, verify offline with `UV_OFFLINE=1 uv run --frozen prek run --all-files`; OS network denial can additionally enforce no connections. `.semgrepignore` includes ordinary tests in scanning; only deliberately unsafe fixtures in `tests/semgrep/` are separately rule-tested. These rules are guardrails, **not a complete security audit or a dependency-vulnerability/secret scanner**. Do not add blanket `noqa`, `type: ignore`, Vulture whitelists, or scanner exclusions to make red checks green. Reviewed typing exceptions are Radon's untyped library, isolated by an explicit adapter, and narrow builtin-descriptor annotations in smoke diagnostics/tests: `union-attr` ignores for `BaseException.__cause__.__get__`/`__set__` address Typeshed describing the instance value rather than the class descriptor. Direct descriptor access avoids untrusted exception accessors; the hostile-property regression uses a narrow `override` ignore to exercise that safety boundary. These specific rationales do not permit blanket ignores.

Ruff/mypy/Vulture discover new Python code repository-wide; complexity covers `src/`, `scripts/`, `tests/`, and `smoke/` when present. Add any new code root to the complexity check. Ignored virtual environments and generated artifacts are not project code.

## Package checks

```sh
uv run --frozen python -m build
```

CI additionally installs the wheel with hash-checked locked runtime dependencies outside the checkout, checks `py.typed`, and runs public API/streaming MockTransport tests against that installed artifact. Version `0.0.0` remains a local development placeholder, not a release. The release workflow is implemented in this preparation branch but **not activated or published**; independent QA/spec-security review and fresh-wheel verification **PASS** (179 checkout tests, 14 focused release tests and 161 runtime tests per clean noneditable wheel on Python 3.11/3.14). Exact-commit hosted CI is **PENDING**. See [release promotion](#release-promotion-and-trusted-publishing). `httpx` is the only direct runtime dependency.

Use `uv lock` after deliberately changing dependencies, review the diff, then rerun gates. Dependabot proposes action/uv dependency updates; maintainers review them, never auto-merge security tooling changes. CI actions are pinned by commit SHA, PR jobs have read-only permissions, and no secrets or persisted checkout credentials.

[Native uv cooldown](https://docs.astral.sh/uv/concepts/resolution/#dependency-cooldowns) is configured in root `uv.toml`: `exclude-newer = "7 days"`. This dedicated [configuration file](https://docs.astral.sh/uv/concepts/configuration-files/) takes precedence over `[tool.uv]` in `pyproject.toml`; dependencies remain declared in `pyproject.toml`, without duplicated uv settings. Ordinary registry resolution (`uv add`, `uv lock`, or resolving `uv sync`) rejects **each wheel/sdist uploaded within the last seven days**, including direct/transitive runtime and development dependencies. This is artifact upload age, not version/commit/PR age; late platform wheels must mature separately. Indexes must supply PEP 700 upload times; missing times make artifacts unavailable. Use `uv lock --refresh` to refresh metadata/cutoff while preserving eligible locked versions, rather than upgrading everything. Review the regenerated lock's versions, artifacts and hashes, then rerun gates.

The cutoff rolls at resolution time, but merely passing time does not rewrite the lock daily. `--frozen` sync/run/export performs no new resolution or age check: it uses the checked-in lock and its selected artifacts/hashes. Git, path and direct-URL dependencies or direct installation of a wheel are outside this registry policy. Future PyPI users installing this SDK do **not** inherit this repository's uv configuration.

[Dependabot cooldown](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#cooldown) independently delays uv and GitHub Actions **version updates** by seven days; its weekly schedule picks eligible releases at the next check. Dependabot **security updates are exempt from its cooldown, not from uv's resolver policy**. For an urgent security fix, explicitly review a temporary package-targeted resolver override (for example, `uv lock --refresh --exclude-newer-package PACKAGE=false`), review every newly selected artifact, and remove the exception afterward; ordinary repository/CI defaults remain seven days with no permanent allowlist. CLI/environment overrides (including `--exclude-newer false`, `UV_EXCLUDE_NEWER`, or `--no-config`) and configuration edits can bypass the native policy: maintainers are trusted, and manual lock/SHA edits are not a tamper-proof merge gate. Delaying first-wave adoption is not a malware guarantee; retain reviewed hashes, pins, and security fixes.

## Release promotion and Trusted Publishing

Preparation only: do **not** configure external settings, create/push `release`, merge a promotion PR, execute `uv publish` (even a dry run), or request identity tokens during setup. No PyPI release or package-name availability is established. The approved [013 contract and evidence](specs/013-release-branch-publishing.md) is authoritative.

After separate authorization, maintainers complete one-time setup:

1. Use a PyPI account with verified email and 2FA; establish `speech-sdk-python` name availability/ownership. For a new project, add a [pending Trusted Publisher](https://pypi.org/manage/account/publishing/); for an owned project, use Manage → Publishing. A pending publisher **does not reserve the name**; first successful upload creates the project under that account.

   | Publisher field | Exact value |
   | --- | --- |
   | Project | `speech-sdk-python` |
   | Repository owner | `hybrd-oss` |
   | Repository name | `speech-sdk-python` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

2. Configure GitHub's `pypi` environment with **Selected branches and tags**, one **Branch** rule for exact `release` only. No tags, `main`, all-protected-branches rule, required reviewers, wait timer or second manual gate. Naming `pypi` in YAML may auto-create an **unprotected** environment; it does not configure these restrictions. No API-token secret is needed.
3. Protect `release`: require reviewed PRs and all quality/runtime checks, prohibit force pushes/direct bypass. Maintainers enforce `main` as the PR source externally; the workflow does not enforce a `main` head branch. Confirm actual hosted check names rather than guessing them.

For each later release, choose a **new** canonical public PEP 440 version in an ordinary version PR to `main`, review metadata/license/README readiness and reconcile the project entry in `uv.lock` without unrelated upgrades. The first real-version PR may use `uv version 0.1.0 --frozen`, followed by `uv lock` and reviewed gates; **do not run those edits now**. Public rc/post/dev versions are supported; local versions and placeholder/equivalent-zero versions are rejected. There is no automatic bump, calendar or tag requirement.

Then deliberately merge a reviewed `main` → `release` PR. Only that release push invokes local reusable Checks at the triggering SHA: **both Python 3.11/3.14 runtime jobs**, all **13 quality hooks**, source-version guard, fresh build/bundle validation and clean noneditable wheel checks must pass before upload. The immutable artifact is named `release-dist-${{ github.sha }}-${{ github.run_attempt }}`. A separate OIDC-only publisher waits for complete Checks success and runs only pinned uv setup, same-run artifact download and `uv publish --trusted-publishing always dist/*.whl dist/*.tar.gz`; no checkout, rebuild or project-code execution occurs there. Main/PR runs remain ordinary checks at `0.0.0`, with no release guard failure, artifact upload or publication.

Initializing `release` at the current placeholder can itself trigger a **blocked `0.0.0` run**; branch creation is not authorized now and first promotion must be deliberate only after readiness. Failed gates/missing artifacts stop publication. PyPI will not replace used filenames; identical partial uploads can be retried, altered bytes fail. Inspect each outcome before promoting the next version: concurrency preserves an in-flight run but pending pushes can coalesce. No attestations are promised. Native seven-day uv policy and dependencies remain unchanged; `packaging` and `ruamel.yaml` are existing locked **dev-only** guard/test dependencies, not runtime dependencies or PyYAML.

See [013 offline evidence](specs/013-release-branch-publishing.md#implementation-evidence-and-remaining-gates) for historical implementation checks and final independent offline acceptance. Future consumers may use `uv add speech-sdk-python` and `import speech_sdk` **only after confirmed publication**.

## Public-repository safety

Never commit keys, `.env` files, generated audio, or raw provider responses. Smoke failure diagnostics must remain limited to local phase, static safe categories, validated HTTP status and allowlisted network causes, without exception text or provider details. Keep live smoke calls explicitly opt-in as specified in [008](specs/008-clone-and-run-smoke-tests.md); default hooks/CI must never call providers. Preserve upstream attribution when adapting code and keep implementation plans under `specs/`.

Maintainers should require the `quality` status check and PR review in repository rules. Those remote settings are not configured by this checkout. Use private vulnerability reporting on GitHub if enabled rather than posting credentials or exploit details in public issues.

## Runtime-only and installed-artifact checks

For a runtime-only clone setup, use uv without developer dependencies:

```sh
uv sync --frozen --no-dev --python 3.11
```

See [README](README.md#clone-install-run) for smoke commands and POSIX/PowerShell key configuration.

Tools stay on Python 3.11. Runtime CI uses Python 3.11 and 3.14 without dev dependencies. Select SDK test modules explicitly: `test_quality_tools` imports Radon and `test_release_workflow` imports dev-only YAML/version tooling; neither belongs in runtime-only or installed-wheel jobs. The local test commands below remove provider keys/configuration, including unused `AZURE_API_KEY`; `UV_OFFLINE=1` requires previously cached dependencies/interpreters. CI currently unsets only OpenAI/xAI keys, but its credentialless jobs receive no API secrets and make no provider calls. Azure implementation and final documentation QA/review passed. Including Azure tests does not change historical 007 evidence or verify live synthesis. Pronunciation tests use pure helpers and MockTransport, never paid calls; current feature evidence is separate in [011](specs/011-pronunciation-substitutions.md#verified-offline-evidence).

```sh
uv sync --frozen --no-dev --python 3.14
PYTHONPATH=tests UV_OFFLINE=1 env -u XAI_API_KEY -u OPENAI_API_KEY \
  -u AZURE_API_KEY -u AZURE_OPENAI_API_KEY -u AZURE_OPENAI_BASE_URL \
  -u AZURE_OPENAI_ENDPOINT -u AZURE_OPENAI_DEPLOYMENT_NAME \
  -u OPENAI_BASE_URL -u OPENAI_API_VERSION \
  uv run --frozen --no-dev python -m unittest -v \
  test_core test_http test_openai test_azure test_xai test_api test_streaming test_smoke test_pronunciations
# Restore tool environment before running prek:
uv sync --frozen --python 3.11
```

Clean wheel check (POSIX; scratch path outside checkout, no editable import). The `uv venv` and `uv pip` commands below use uv solely to verify an isolated installed wheel, not for clone setup:

```sh
uv run --frozen python -m build
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/speech-sdk-runtime.txt
uv venv /tmp/speech-sdk-wheel --python 3.11
uv pip install --python /tmp/speech-sdk-wheel/bin/python --require-hashes -r /tmp/speech-sdk-runtime.txt
uv pip install --python /tmp/speech-sdk-wheel/bin/python --no-deps dist/*.whl
mkdir -p /tmp/speech-sdk-installed/tests
cp tests/test_api.py tests/test_azure.py tests/test_streaming.py tests/test_smoke.py tests/test_pronunciations.py /tmp/speech-sdk-installed/tests/
cp -r smoke /tmp/speech-sdk-installed/
(cd /tmp/speech-sdk-installed && UV_OFFLINE=1 env -u PYTHONPATH \
  -u XAI_API_KEY -u OPENAI_API_KEY -u AZURE_API_KEY -u AZURE_OPENAI_API_KEY \
  -u AZURE_OPENAI_BASE_URL -u AZURE_OPENAI_ENDPOINT -u AZURE_OPENAI_DEPLOYMENT_NAME \
  -u OPENAI_BASE_URL -u OPENAI_API_VERSION \
  /tmp/speech-sdk-wheel/bin/python -m unittest discover -s tests -v)
```

The command fences above preserve the historical pronunciation-era selections: copied-wheel API (13), Azure (18), streaming (10), smoke (30) and pronunciation (14), **85 tests**, versus the **144-test** runtime selection. Current CI additionally selects `test_result_details` in both; its ten-module runtime selection has **161 tests**. See [012 historical verified wheels](specs/012-composite-result-inspection.md#verified-offline-evidence) and [013 verified fresh wheels](specs/013-release-branch-publishing.md#implementation-evidence-and-remaining-gates), built with the current main's setuptools 84.0.0. Earlier wheel passes remain historical evidence.

The wheel install must follow hash-required runtime export/install, not replace it with an isolated `--no-deps` wheel install. Inspect sdist/wheel contents for license, attribution metadata, `py.typed`, runtime modules, and absence of secrets/audio/test artifacts. Validate CI edits with `actionlint .github/workflows/checks.yml .github/workflows/release.yml`; preserve read-only checks, publisher-only OIDC and SHA pins.

For clone/install, Windows keys and paid smoke commands see [README](README.md). Do not execute live provider calls during ordinary contribution checks; any new paid verification requires explicit opt-in and a bounded request budget. The final xAI gate has passed; no rerun is needed for documentation changes. Preserve historical [007](specs/007-validation-and-xai-e2e.md) evidence. Record future Azure evidence in [010](specs/010-azure-openai-tts.md), separate from that original OpenAI/xAI milestone: date/tested-source provenance, bounded command/request budget, actual success or safe failure, and WAV properties/duration/paths/listening only if performed. A missing or unavailable deployment is not PASS; offline passes alone are not live evidence. Azure uses the same buffered/streaming API but is not a selection in the two-provider smoke CLI.
