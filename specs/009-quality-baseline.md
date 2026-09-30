# 009 — Strict development and public-repository baseline

Status: implemented baseline; provider APIs and smoke tests now implemented and checked offline. Live gate remains unverified in 007. Requested before starting 002–008 SDK work.

## Delivered

- Python >=3.11 `src/speech_sdk/` package skeleton with `py.typed`, Apache-2.0 metadata, a pinned conventional build backend, and initially no runtime dependencies or fake speech functions (SDK implementation subsequently added `httpx`).
- Development-only mypy, Ruff, Vulture, Radon, Semgrep, prek, and build tools resolved with hashes in `uv.lock`.
- Strict mypy plus unreachable-code warnings; Ruff correctness/annotations/import/security rules and formatting.
- Vulture minimum confidence 60. No blanket public-API whitelist; future legitimate exports/dynamic callbacks need targeted evidence rather than weakening the global threshold.
- A small failing Radon gate: complexity <=10 inclusive for functions/methods/closures; unreadable/malformed input fails. Radon only reports by default, so the wrapper supplies a failing exit status. Four standard-library regression checks cover boundaries, methods/closures, invalid/missing files, and clean input.
- Four local Semgrep rules with annotated positive/negative fixtures. TLS verification disabled, dynamic eval/exec, unsafe YAML loaders, and subprocess `shell=True` are blocked. Ordinary tests are scanned; static unsafe fixtures are excluded from ordinary checks and tested separately. No registry rules, telemetry, version pings, platform uploads, or silent inline Semgrep suppressions.
- Rust-native prek hooks check YAML/TOML syntax, merge-conflict markers, and private-key headers without remote repositories. Local system hooks via prek and `.pre-commit-config.yaml`, using frozen dependency resolution. All checks run repository-wide on every commit, including docs-only changes. The same hook suite runs in CI.
- Public PR/push CI with read-only permissions, SHA-pinned actions, no persisted checkout credentials, no secrets/publishing/live provider calls, and a timeout. Build and clean wheel import/type-marker checks run after gates. Dependabot proposes action/uv dependency updates without automatic merges.
- Contributor setup and command documentation in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Acceptance

- Frozen sync and lock consistency pass on tooling Python 3.11.
- Ruff lint/format, strict mypy, Vulture, complexity, Semgrep validation/rule tests/scan, and offline unit tests pass as a single `prek run --all-files` invocation.
- Injected bad typing, unused code, insecure TLS, excess complexity, and syntax errors produce nonzero exits. Safe Semgrep fixtures remain clean.
- sdist/wheel build; clean wheel installation imports outside the editable checkout; LICENSE, README metadata and `py.typed` are included and runtime requirements are locked (`httpx` after SDK implementation).
- No upstream speech behavior is claimed verified by tooling checks. xAI E2E remains required by 007 and not run yet.

## Boundaries

Python 3.11 is the shared tool environment to avoid coupling Semgrep/platform tool support to the newest package runtime. Expand runtime CI coverage with actual SDK code. The selected Semgrep rules are a small tested baseline, not exhaustive SAST, secret detection, or dependency vulnerability auditing. Local hooks can be bypassed; required remote status checks and reviewer enforcement must be enabled by repository maintainers.

No release automation, worktree orchestration, custom lint framework, or hosted security dashboard is part of this baseline. Provider implementations were delivered subsequently under 004/005, not by the baseline.

## Local verification

Verified 2026-09-30 on Python 3.11: all prek gates pass; deliberate mypy/Ruff/Vulture/Radon/Semgrep violations fail; four complexity tests and all four Semgrep rule tests pass. sdist/wheel build, clean wheel import/type marker/runtime-requirement checks, and license/README attribution inspection pass. GitHub Actions syntax was checked with actionlint; a hosted CI run remains unverified until pushed.
