# 013 — Release branch publishing

Status: **approved contract; independent QA/spec-security review and fresh-wheel
verification PASS; exact-commit setup-PR hosted CI PENDING**.
External setup, activation and publication **NOT DONE**; source version remains
`0.0.0`. Contract: `speech-5hk.1`; implementation: `speech-5hk.2`;
documentation: `speech-5hk.3`; parent `speech-5hk`.

The initial specification was based on composite commit
`10cc8666ebdebc0a977c998e1bf486cfca18556f`. PR #8 has since been merged as
`3a5806e`; the setup now lives on `feat/release-publishing`, based on current
`main` at `93a0667b4af89a73fe553e14f4b46f1181fafd4f`, preserving the user-merged
setuptools 84.0.0 build requirement and byte-identical SDK source. Prepare a
**separate focused PR targeting `main`**, not a stacked PR on #8. GitHub-verified
green [CI run `37050472866`](https://github.com/hybrd-oss/speech-sdk-python/actions/runs/37050472866)
is valid historical evidence for that run's source commit, not this setup's
exact-commit CI. No commit, setup-PR CI success or publication is claimed here.

## Promotion contract

Maintainers deliberately merge `main` into a protected `release` branch through
an ordinary reviewed PR. That merge's push automatically publishes **after all
gates**, with no second environment reviewer approval. No schedule, tag trigger,
GitHub Release creation, automatic merge, or automatic version bump.

```text
main: features + deliberate version PR -> ordinary Checks, never publish
  reviewed main -> release PR merge
    release.yml: push refs/heads/release at exact github.sha
      Checks via workflow_call (contents: read; no OIDC)
        runtime: Python 3.11 + 3.14, locked runtime dependencies only
        quality: 13 hooks -> version guard -> fresh build -> bundle/install checks
          upload one immutable validated distribution artifact
      publish needs complete Checks success
        pypi environment -> download that artifact -> pinned uv publish
        id-token: write only; no checkout, build, install or project script
```

Preparation is authorized, **activation is not**: do not create/push `release`,
merge a release PR, change external account/repository settings, or publish to
PyPI/TestPyPI during this work. No paid provider calls, audio work, OIDC requests,
`uv publish` execution (including dry runs), secrets or API tokens in setup tests.
Help-only CLI inspection is permitted. Keep `project.version = "0.0.0"` unchanged
until a separately authorized first real-version PR.

## Workflow changes for implementation

Reuse [current Checks](../.github/workflows/checks.yml), adding `workflow_call`
without inputs/secrets. Keep its existing `push: main` and `pull_request` events;
do not add direct `push: release` there and duplicate runs. The release caller
uses `./.github/workflows/checks.yml` from the same triggering commit, not a
moving `@main` reference. Build/test checkouts retain `persist-credentials: false`.

New `.github/workflows/release.yml` has **only** `push.branches: [release]`.
Use workflow-wide `permissions: {}` and `concurrency` group `release-pypi` with
`cancel-in-progress: false`. Its `checks` calling job grants `contents: read`
only; its publisher grants `id-token: write` only, on a separate fresh
`ubuntu-24.04` runner with environment `pypi`. Require `needs: checks`, default
success semantics and an explicit push/release condition; never `always()` or
`continue-on-error`. Failure, cancellation or skipped required gates cannot
reach the publisher. No inherited secrets or `contents: write` anywhere.

Preserve runtime matrix `['3.11', '3.14']`, current explicit ten-module SDK
selection, frozen no-dev installs and credentialless offline tests. Quality
continues Python 3.11, `uv sync --frozen` and `uv run --frozen prek run --all-files`.
Make quality depend on runtime so its final artifact upload follows **both**
matrix successes as well as all quality/build/install gates; no extra uploader
job/framework is needed. The caller's `needs: checks` still gates on the entire
reusable workflow, not merely one job or an artifact's existence.

Checks retains its distinct `checks-${{ github.ref }}` concurrency group, with
`cancel-in-progress` false for push/release calls and true for ordinary main/PR
runs. Do not share the caller's group and accidentally cancel the calling run.
Default pending runs can coalesce; false protects an in-flight upload, not a
promise to publish every rapid push or a guarantee of execution order. Promote
one deliberate version at a time and inspect the result before the next merge.

Quality upload and both release-only guards use exactly:
`github.event_name == 'push' && github.ref == 'refs/heads/release'`.
A reusable workflow retains its caller's event context; do not test for an event
name of `workflow_call`. Main and PR checks must still pass with placeholder
`0.0.0`, without artifact upload, release guard failures or publication.

### Version and distribution boundary

Use the existing build command `uv run --frozen python -m build`, not a second
build in the publisher. Before building, read `[project]` with stdlib `tomllib`;
release-only validation requires name `speech-sdk-python` and a present string
version that parses as a public PEP 440 version, is canonical and is not
`Version('0.0.0')` (including equivalent zero forms). Use already locked/dev
`packaging.version.Version`, not a new dependency or invented SemVer parser.
Reject local `+...` versions, invalid/blank/missing versions and normalization
ambiguity before the build/upload. Public prereleases such as `0.1.0rc1`, post
and dev versions remain supported; no stable-triples-only policy is required.

Before every build, clear generated `dist/`, `build/` and source egg-info so stale
files cannot survive; clean only known generated paths. After building, validate
before upload using stdlib `zipfile`, `tarfile` and `email.parser.BytesParser`,
plus existing dev `packaging.utils` filename parsers. Read archive members rather
than extracting arbitrary paths or executing package code in the validator.
Require:

- `dist/` contains exactly two regular, non-symlink files: one universal
  `speech_sdk_python-<version>-py3-none-any.whl` and one
  `speech_sdk_python-<version>.tar.gz`; no stale, extra or missing entries.
- Wheel filename, sole expected `.dist-info/METADATA`, sdist filename and root
  `PKG-INFO` agree on the source's version and normalized project name. Require
  exactly one `Name` and `Version` header in each metadata file; fail on malformed,
  duplicate, missing or inconsistent metadata. Do not search arbitrary nested
  egg-info for whichever metadata happens to match.
- Wheel SDK modules and sdist `src/speech_sdk/` bytes match the triggering source;
  `py.typed`, Apache/Jellypod license attribution and README are included. Exclude
  credentials, `.env`, private audio and generated/test/tooling files from the
  wheel. An sdist may contain the project's ordinary source tests/configuration;
  it must not contain secrets or private/generated artifacts.
- Preserve the current clean noneditable wheel install with hash-required frozen
  runtime dependency export/install, outside-checkout public tests and
  `site-packages`/`py.typed` assertions. These exercise the wheel to be uploaded,
  not an editable checkout. Keep tooling-only tests out of that copied subset.

Normal main/PR build and install checks remain valid at `0.0.0`; the publication
identity/placeholder guard is release-only. Implement the small pre-build and
bundle validators together in one dev-only script if inline YAML would duplicate
logic in tests. No runtime imports/dependencies, generic release framework or
PyPI lookup is necessary.

Upload once as `release-dist-${{ github.sha }}-${{ github.run_attempt }}`, using
`if-no-files-found: error`, no overwrite and only the validated `dist/` pair.
Use the identical explicit artifact name in same-run download to `dist/` on the
fresh publisher runner. Attempt suffixes avoid name conflicts on reruns. Do not
download all artifacts, use cross-run selectors/tokens, run uploaded scripts,
rebuild or install project code. Missing artifact/download failure stops publish.
Artifact v4 archives are immutable; deleting/recreating one assigns a new ID.
This is same-run artifact isolation, not an independent signature/attestation
claim or protection against a maintainer changing the workflow itself.

Publisher steps are only pinned setup-uv (0.12.18, cache disabled), pinned
artifact download, and `uv publish --trusted-publishing always dist/*.whl dist/*.tar.gz`. The exact validated pair is passed,
not default `dist/*` with possible unrelated files. No checkout, Python setup,
`uv run`, sync, project/index configuration, credentials, logging token values,
attestation generator or GitHub tag/release command. Default PyPI publication
needs no checkout or `--index`. `always` fails closed when OIDC cannot authenticate.
Native uv publishing does **not** generate attestations; none are promised here.

Every deliberate promotion needs a new chosen project version. In the later,
explicit first-version PR, `uv version 0.1.0 --frozen` is an example manual edit,
not a command to run now; reconcile the project entry with `uv lock`, review
only the intended project-version change and rerun gates without unrelated
upgrades. Review public metadata (currently the description mentions only
OpenAI/xAI), license and README readiness before the first promotion.
No version guessing from commits or PyPI queries. PyPI refuses replacement of
used filenames; an unchanged version is not a new release. uv can resume an
identical partial upload by checking existing bytes; altered files fail, rather
than unsafe `--skip-existing` behavior. Rerun the same validated artifact when
possible; a rebuild is not guaranteed byte-identical. An expired/deleted artifact
is a failure, not permission to rebuild in the OIDC job.

### Frozen tools and pins

No new dependencies, relock, project-version edit, existing action-pin upgrade or
cooldown change in the setup PR. Root [uv.toml](../uv.toml) remains the native
seven-day cooldown and minimum-version authority. Keep these current pins:

| Action/tool | Exact selection |
| --- | --- |
| checkout v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| setup-python v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| setup-uv v10.2.0 | `c18668ad3cf93ea998bef934396af7bb5c839dc7` |
| uv | `0.12.18` |
| upload-artifact v4.6.2 (new) | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| download-artifact v4.3.0 (new) | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |

New artifact SHAs were verified by the assigning orchestrator against public tag
APIs on 2026-10-02. Official examples may use newer majors; those are not needed.
Local uv 0.12.18 help confirms `--trusted-publishing` supports `always`; that
inspection is not an invocation or authentication/publication check.

## One-time external setup — maintainer only, after permission

1. PyPI account: verified email, required 2FA, safely stored recovery codes.
   Check project-name availability/ownership for `speech-sdk-python`; no
   availability or ownership is established here. A missing public listing does
   not establish availability, and a pending publisher **does not reserve** it.
2. If the project does not exist, configure a pending publisher at
   <https://pypi.org/manage/account/publishing/>. If already owned, use that
   project's Manage → Publishing page. Enter these exact GitHub identity fields:

   | Field | Value |
   | --- | --- |
   | PyPI project | `speech-sdk-python` |
   | Repository owner | `hybrd-oss` |
   | Repository name | `speech-sdk-python` |
   | Workflow filename | `release.yml` (not a full path) |
   | Environment | `pypi` |

   First authorized upload creates the project under the pending publisher's
   account and converts it to a normal publisher. No manual first-upload token.
   If someone else registers the name first, the pending publisher is invalidated.
3. GitHub Settings → Environments: deliberately create/configure separate `pypi`.
   Select **Selected branches and tags**, add only a **Branch** rule for exact
   `release`, no tag rule or broad "all protected branches" rule. No required
   reviewers, wait timer or additional manual deployment gate: PR approval is
   the approval boundary, publication follows gates automatically. Merely naming
   an environment in YAML can create an unprotected environment; it does not
   configure these restrictions. No environment secret is needed.
4. Protect `release`: require a reviewed promotion PR and all required quality/
   runtime status checks; prohibit force pushes/direct bypass. Maintainers enforce
   `main` as the promotion source; branch protection alone does not select PR
   head branches. Protect workflow/version changes on `main` as normal. Verify
   exact hosted status-check names after implementation, not guessed contexts.
5. Only after separate authorization, ready metadata/new version, green gates and
   confirmed identity/settings, promote `main` into `release`. Inspect Actions
   and PyPI outcome before advertising `uv add speech-sdk-python` / `import
   speech_sdk`. Those are future consumer instructions, not availability evidence.

## Required offline acceptance for implementation

Add dev-only `tests/test_release_workflow.py` using stdlib unittest and safe YAML
parsing from the **already installed** Semgrep dev dependency `ruamel.yaml`.
PyYAML is not installed/locked in this checkout; do not add it or rely on `import yaml`. `packaging` is already installed/locked through dev tools. Keep this new
module, like `test_quality_tools`, out of runtime no-dev and installed-wheel SDK
selections. Full discovery in the quality job/hooks includes it.

- Parse both workflow files: preserve main/PR Checks events, new workflow_call,
  release-only caller, exact SHA/tool pins, runtime matrix/modules and frozen
  commands. Assert the complete `needs` chain, all hooks and install checks
  precede upload, release-only conditions, failure propagation and both
  non-cancelling release concurrency boundaries.
- Assert permissions/OIDC separation, environment `pypi`, publisher's three
  allowed steps, no checkout/build/project code/secrets/write scopes elsewhere,
  same explicit immutable artifact name and current-run download, no overwrite,
  fail-on-missing upload and explicit `--trusted-publishing always` file pair.
- Execute the actual validator on temporary TOML/zip/tar fixtures, not a rewritten
  model of its logic. Release `0.0.0`/equivalent zero, invalid/local/missing
  version or wrong name fail before build; main/PR placeholder checks pass.
  Valid `0.1.0` and `0.1.0rc1` fixtures pass. Fail on mismatched names/versions,
  wrong filenames/tags, stale/extra/missing/symlink files, duplicate metadata,
  missing `py.typed`/license/README and source-byte mismatch. Fixtures choose
  their own version and source offset/path; never require a production version
  edit, external lookup, build or OIDC to test a guard.
- Use in-memory workflow mutations to demonstrate regression assertions reject
  main/PR publishing, missing matrix dependency, early upload/publish, elevated
  permissions, rebuilding in publisher and wrong artifact selection. Static
  offline tests do not prove GitHub scheduling or external protections exist.
- Run all 13 existing hooks, full quality discovery, explicit runtime selections
  and fresh clean noneditable wheels on Python 3.11/3.14; keep metadata/source
  contents verified. Run `actionlint` on both workflows. Independent QA/spec-
  security review precedes the orchestrator's exact-HEAD setup-PR CI check.
  Record actual counts/results, not promises; no activation event for testing.

Use mature uv 0.12.18 from
`/tmp/speech-sdk-uv-mature.vrAdP5/uv-aarch64-apple-darwin`, approved PATH ending
`/Users/mattruiters/.local/bin:/usr/bin:/bin`, `env -i` with only HOME/PATH and
needed offline settings, `UV_OFFLINE=1`, frozen dependencies and macOS
`sandbox-exec -p '(version 1)(allow default)(deny network*)'`. Cached tooling is
already available; do not install/re-resolve tools, disable native policy or
use old global uv. Offline runtime environments exclude dev dependencies.

## References and evidence

Read official documentation for this contract:

- [Astral package/version/publishing guide](https://docs.astral.sh/uv/guides/package/)
  and [GitHub integration](https://docs.astral.sh/uv/guides/integration/github/):
  separate build/publisher, pinned uv, exact-byte partial retries; no generated
  attestations. Adapt their tag example to the requested release-branch trigger.
- [PyPI pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
  and [existing publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/):
  identity fields, first-upload project creation and no name reservation.
- [PyPI account/2FA help](https://pypi.org/help/#twofa) and
  [PEP 440 version specification](https://packaging.python.org/en/latest/specifications/version-specifiers/).
- GitHub [reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
  [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
  and [environment configuration](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
- Pinned artifact documentation: [upload v4.6.2](https://github.com/actions/upload-artifact/tree/v4.6.2),
  [download v4.3.0](https://github.com/actions/download-artifact/tree/v4.3.0).
- Existing [contribution gates](../CONTRIBUTING.md), [package contract](002-api-and-package.md)
  and [quality baseline](009-quality-baseline.md).

Historical specification completion (`speech-5hk.1`) added only this document;
source, workflows, version, lock, dependencies and pins were untouched at that
stage. Official references were retrieved/read. Credential-free,
OS-network-denied local uv help and installed dev parser/version-library
inspection passed; specification fence/link/syntax/scope checks are recorded in
`/tmp/speech-5hk.1-spec-check.py` and `/tmp/speech-5hk.1-spec-check.log`.
That historical check did not establish implementation or hosted CI success.

## Implementation evidence and remaining gates

Recorded 2026-10-02 on `feat/release-publishing`, based on current `main`
`93a0667` plus uncommitted release setup. Implementation is present in
`.github/workflows/checks.yml`, `.github/workflows/release.yml`,
`scripts/check_release.py` and `tests/test_release_workflow.py`.
The approved contract above is unchanged. SDK source/exports and README match
current main byte-for-byte; `project.version = "0.0.0"`, dependencies, `uv.lock`,
seven-day native uv policy and existing action pins are unchanged by this setup.
The user-merged setuptools 84.0.0 build requirement is preserved.

| Offline evidence | Result and provenance |
| --- | --- |
| Historical initial implementation | 13 focused / 178 full checkout tests PASS: `/tmp/speech-5hk2-green.log`, `/tmp/speech-5hk2-full.log`; these precede the tests-only regression fix, not final counts |
| Final independent QA (`speech-5hk.4`) | **14 focused / 179 full checkout tests PASS**: `/tmp/speech-5hk4-final-focused.log`, `/tmp/speech-5hk4-final-full.log`; review and SHA256 evidence: `/tmp/speech-5hk4-final-review.log`, `/tmp/speech-5hk4-final-evidence.sha256` |
| Skipped-gate regression fix (`speech-5hk.7`) | Original 13 test methods preserved; one added test executes **28 mutation subcases**. Independent QA confirms all four original skipped-runtime mutants are now rejected: `/tmp/speech-5hk4-final-mutations-coverage.log` |
| Quality hooks | All **13 PASS** after the tests-only fix: `/tmp/speech-5hk7-hooks.log`, independently inspected and hashes verified by final QA |
| Independent spec/security review (`speech-5hk.5`) | **PASS**, including both-workflow actionlint exit 0 and 15 supplemental checks: `/tmp/speech-5hk5-retry/review.md`, `/tmp/speech-5hk5-retry/evidence.sha256`; review's 13 focused tests are historical, before the fix |
| Fresh current-main build | Cached offline `build --installer uv` with **setuptools 84.0.0 PASS**: `/tmp/speech-sdk-release-main84.mAEUqn/build.log`; CI's existing build command remains unchanged |
| Fresh clean noneditable wheels (`speech-5hk.6`) | **161 SDK runtime tests each PASS on Python 3.11/3.14**: `/tmp/speech-sdk-release-main84.mAEUqn/tests-3.11.log`, `/tmp/speech-sdk-release-main84.mAEUqn/tests-3.14.log`; install logs alongside them |

Fresh wheels were reinstalled noneditable into the hash-locked runtime-only
environments at `/tmp/speech-sdk-release-wheel.L2Dt15`, outside the checkout,
with no dev tooling. SDK bytes, README/license attribution, `py.typed` and
private-artifact exclusions passed inspection; the dev release checker is not
in the wheel. Checks used clean environments, frozen cached dependencies,
`UV_OFFLINE=1`, provider settings absent and OS network denial. No PyPI,
provider or OIDC calls were made. The actual package remains `0.0.0`: these
wheel results verify SDK nonregression, **not release authentication or
publication**. Synthetic version/archive fixtures exercise release guards
without editing that version. QA/security coverage is finite and
contract-focused, not exhaustive certification.

**Remaining gate:** `speech-5hk.6` exact-commit setup-PR hosted CI is **PENDING**
until the final setup commit is checked. Independent QA/spec-security review
and fresh current-main wheels have passed; no future commit or CI result is
claimed. Historical composite 165-test/161-per-wheel results at `10cc866` and
CI run `37050472866` remain evidence for their earlier sources only.
Documentation-only checks validate links/fragments, fences, Python AST/shell
syntax, whitespace and unchanged source/metadata/README hashes; they do not
replace hosted CI. No wheel rebuild is needed for these documentation-only
changes: SDK source, package metadata and packaged README remain unchanged.

Preparation has not configured PyPI/GitHub settings, created/pushed `release`,
performed a real-version edit or publication, requested identity tokens, or
called providers. Naming the `pypi` environment in YAML is not external
configuration. Later branch initialization at `0.0.0` can trigger a blocked
run; first promotion must be separately authorized and deliberate after
version/metadata, identity, environment restrictions and gates are ready.
No hosted release CI, package availability, live provider verification,
attestation or publication is claimed.
