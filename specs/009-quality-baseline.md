# 009 — Strict development and public-repository baseline

Status: implemented baseline; provider APIs and smoke tests now implemented and checked offline. Live gate is **FAIL / BLOCKED** after two failed runner invocations in [007](007-validation-and-xai-e2e.md); the single private diagnostic isolated a valid WAV header and `speech-vqx.26` passes offline, but final buffered + streamed re-verification remains outstanding. Requested before starting 002–008 SDK work.

## Delivered

- Python >=3.11 `src/speech_sdk/` package skeleton with `py.typed`, Apache-2.0 metadata, a pinned conventional build backend, and initially no runtime dependencies or fake speech functions (SDK implementation subsequently added `httpx`).
- Development-only mypy, Ruff, Vulture, Radon, Semgrep, prek, and build tools resolved with hashes in `uv.lock`.
- Strict mypy plus unreachable-code warnings; Ruff correctness/annotations/import/security rules and formatting.
- Vulture minimum confidence 60. No blanket public-API whitelist; future legitimate exports/dynamic callbacks need targeted evidence rather than weakening the global threshold.
- A small failing Radon gate: complexity <=10 inclusive for functions/methods/closures; unreadable/malformed input fails. Radon only reports by default, so the wrapper supplies a failing exit status. Four standard-library regression checks cover boundaries, methods/closures, invalid/missing files, and clean input.
- Four local Semgrep rules with annotated positive/negative fixtures. TLS verification disabled, dynamic eval/exec, unsafe YAML loaders, and subprocess `shell=True` are blocked. `semgrep scan --test --strict` validates local rule schemas/patterns and fixture behavior; the existing full scan uses `--strict --error --disable-nosem`. Ordinary tests are scanned; static unsafe fixtures are excluded from ordinary checks and tested separately. No registry rules, telemetry, version pings, platform uploads, or silent inline Semgrep suppressions. The registry-fetching `scan --validate` hook was removed under `speech-vqx.27`: installed Semgrep 1.178.0 unconditionally loads `p/semgrep-rule-lints` in `CoreRunner.validate_configs`, even for a local config.
- Rust-native prek hooks check YAML/TOML syntax, merge-conflict markers, and private-key headers without remote repositories. Local system hooks via prek and `.pre-commit-config.yaml`, using frozen dependency resolution. The 13 hooks include repository-wide tool checks on every commit, including docs-only changes. The same hook suite runs in CI.
- Public PR/push CI with read-only permissions, SHA-pinned actions, no persisted checkout credentials, no secrets/publishing/live provider calls, and a timeout. Build and clean wheel import/type-marker checks run after gates. Dependabot proposes action/uv dependency updates without automatic merges.
- Contributor setup and command documentation in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Acceptance

- Frozen sync and lock consistency pass on tooling Python 3.11.
- All 13 hooks, including Ruff lint/format, strict mypy, Vulture, complexity, Semgrep strict local validation/rule tests and security scan, and offline unit tests, pass as a single `prek run --all-files` invocation. After installation, `UV_OFFLINE=1` and OS network denial permit local processes/cache use while forbidding network access.
- Injected bad typing, unused code, insecure TLS, excess complexity, and syntax errors produce nonzero exits. Safe Semgrep fixtures remain clean.
- sdist/wheel build; clean wheel installation imports outside the editable checkout; LICENSE, README metadata and `py.typed` are included and runtime requirements are locked (`httpx` after SDK implementation).
- No upstream speech behavior is claimed verified by tooling checks. xAI E2E remains required by 007 and **FAIL / BLOCKED**, awaiting final buffered + streamed re-verification; the offline sentinel fix is not a live pass.

## Boundaries

Python 3.11 is the shared tool environment to avoid coupling Semgrep/platform tool support to the newest package runtime. Expand runtime CI coverage with actual SDK code. The selected Semgrep rules are a small tested baseline, not exhaustive SAST, secret detection, or dependency vulnerability auditing. Local hooks can be bypassed; required remote status checks and reviewer enforcement must be enabled by repository maintainers.

No release automation, worktree orchestration, custom lint framework, or hosted security dashboard is part of this baseline. Provider implementations were delivered subsequently under 004/005, not by the baseline.

## Local verification

Verified 2026-09-30 on Python 3.11: all prek gates pass; deliberate mypy/Ruff/Vulture/Radon/Semgrep violations fail; four complexity tests and all four Semgrep rule tests pass. sdist/wheel build, clean wheel import/type marker/runtime-requirement checks, and license/README attribution inspection pass. GitHub Actions syntax was checked with actionlint; a hosted CI run remains unverified until pushed.

`speech-vqx.27` offline recheck (2026-09-30, installed Semgrep 1.178.0): all 13 hooks passed under macOS `sandbox-exec -p '(version 1) (allow default) (deny network*)'`, with `UV_OFFLINE=1`, bytecode writes disabled, and inherited provider keys/Semgrep token removed. Local processes and uv cache remained available; a localhost socket check returned `EPERM`. Strict annotated rule tests passed 4/4 and the strict full scan had zero findings. Temporary malformed YAML, missing-message schema, and invalid Python pattern (`eval(`) failed: strict rule-test exits 2/1/1, strict scan exits 7/7/2. Scanning a temporary copy of the unsafe fixture blocked 11 findings with exit 1; it was never executed. No registry access or validator bypass was needed.

The first shared-checkout run honestly failed on a concurrent smoke-test formatting change and a file-modification check; the subsequent complete run passed without editing those files here. Full offline discovery passed 106 tests (the earlier diagnostic evidence recorded 102; concurrent test additions account for the current count). Exact commands and temporary evidence paths are recorded in the bead notes. Historical 007 results are unchanged; this does not reverify packages, hosted CI, or live providers.
