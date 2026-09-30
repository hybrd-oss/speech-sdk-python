# Contributing

Both provider adapters and public APIs are implemented with offline checks. Smoke execution is explicitly paid/opt-in; live verification remains pending the post-review `speech-vqx.11` gate. Status and intentional differences are in [specs/](specs/README.md).

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the repository root:

```sh
uv sync --frozen --python 3.11
uv run --frozen prek install
uv run --frozen prek run --all-files
```

Use Python 3.11 for development tooling; the package targets 3.11+. `uv` installs the interpreter if needed. Tool versions and dependency hashes are committed in `uv.lock`; CI pins uv 0.12.21. No API keys or Semgrep account are needed. Initial installation downloads dependencies; the analysis itself runs locally.

`prek` is the Rust hook runner reading `.pre-commit-config.yaml`. Rust-native hooks check YAML/TOML syntax, merge markers, and private-key headers; local commands use the locked environment, with no remote hook repositories. Tool gates check the whole repository, even on documentation-only commits, and CI runs the same hooks. `repo: builtin` requires prek, not the Python pre-commit runner. Hooks do not rewrite files. To apply Ruff fixes deliberately:

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

Semgrep uses only `.semgrep.yml`, the OSS engine, disabled metrics/version checks, and no upload. `.semgrepignore` includes ordinary tests in scanning; only deliberately unsafe fixtures in `tests/semgrep/` are separately rule-tested. These rules are guardrails, **not a complete security audit or a dependency-vulnerability/secret scanner**. Do not add blanket `noqa`, `type: ignore`, Vulture whitelists, or scanner exclusions to make red checks green. The only typing exception is Radon's untyped library, isolated by an explicit adapter.

Ruff/mypy/Vulture discover new Python code repository-wide; complexity covers `src/`, `scripts/`, `tests/`, and `smoke/` when present. Add any new code root to the complexity check. Ignored virtual environments and generated artifacts are not project code.

## Package checks

```sh
uv run --frozen python -m build
```

CI additionally installs the wheel with hash-checked locked runtime dependencies outside the checkout, checks `py.typed`, and runs public API/streaming MockTransport tests against that installed artifact. Version `0.0.0` is a local development placeholder, not a release; no publication workflow exists. `httpx` is the only direct runtime dependency.

Use `uv lock` after deliberately changing dependencies, review the diff, then rerun gates. Dependabot proposes action/uv dependency updates; maintainers review them, never auto-merge security tooling changes. CI actions are pinned by commit SHA, PR jobs have read-only permissions, and no secrets or persisted checkout credentials.

## Public-repository safety

Never commit keys, `.env` files, generated audio, or raw provider responses. Keep live smoke calls explicitly opt-in as specified in [008](specs/008-clone-and-run-smoke-tests.md); default hooks/CI must never call providers. Preserve upstream attribution when adapting code and keep implementation plans under `specs/`.

Maintainers should require the `quality` status check and PR review in repository rules. Those remote settings are not configured by this checkout. Use private vulnerability reporting on GitHub if enabled rather than posting credentials or exploit details in public issues.

## Runtime-only and installed-artifact checks

Tools stay on Python 3.11. Runtime CI uses Python 3.11 and 3.14 without dev dependencies. Select SDK test modules explicitly: `test_quality_tools` imports Radon and must not be imported in runtime-only jobs. No provider keys are inherited by these commands.

```sh
uv sync --frozen --no-dev --python 3.14
PYTHONPATH=tests env -u XAI_API_KEY -u OPENAI_API_KEY uv run --frozen --no-dev python -m unittest -v test_core test_http test_openai test_xai test_api test_streaming test_smoke
# Restore tool environment before running prek:
uv sync --frozen --python 3.11
```

Clean wheel check (POSIX; scratch path outside checkout, no editable import):

```sh
uv run --frozen python -m build
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/speech-sdk-runtime.txt
uv venv /tmp/speech-sdk-wheel --python 3.11
uv pip install --python /tmp/speech-sdk-wheel/bin/python --require-hashes -r /tmp/speech-sdk-runtime.txt
uv pip install --python /tmp/speech-sdk-wheel/bin/python --no-deps dist/*.whl
mkdir -p /tmp/speech-sdk-installed/tests
cp tests/test_api.py tests/test_streaming.py tests/test_smoke.py /tmp/speech-sdk-installed/tests/
cp -r smoke /tmp/speech-sdk-installed/
(cd /tmp/speech-sdk-installed && env -u XAI_API_KEY -u OPENAI_API_KEY /tmp/speech-sdk-wheel/bin/python -m unittest discover -s tests -v)
```

The wheel install must follow hash-required runtime export/install, not replace it with an isolated `--no-deps` wheel install. Inspect sdist/wheel contents for license, attribution metadata, `py.typed`, runtime modules, and absence of secrets/audio/test artifacts. Validate CI edits with `actionlint .github/workflows/checks.yml`; preserve read-only permissions and SHA pins.

For clone/install, Windows keys and paid smoke commands see [README](README.md). Do not execute live xAI/OpenAI during ordinary contribution checks; the final live gate runs only after QA/review approval. Record date/ref/command, WAV properties, duration, paths and manual listening separately in [007](specs/007-validation-and-xai-e2e.md); offline passes do not close that gate.
