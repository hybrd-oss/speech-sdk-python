"""Offline release boundary checks; never invoke publishing or package code."""

import copy
import io
import tarfile
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ruamel.yaml import YAML

from scripts.check_release import main, source_version

ROOT = Path(__file__).resolve().parents[1]
CONDITION = "github.event_name == 'push' && github.ref == 'refs/heads/release'"
ARTIFACT = "release-dist-${{ github.sha }}-${{ github.run_attempt }}"
UV = "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"


def workflow(name: str) -> dict[str, Any]:
    result: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    )
    return result


def fixture(root: Path, version: str = "0.1.0") -> tuple[dict[str, bytes], dict[str, bytes]]:
    source = root / "src" / "speech_sdk"
    source.mkdir(parents=True)
    (source / "__init__.py").write_bytes(b'"""Fixture SDK."""\n')
    (source / "py.typed").write_bytes(b"")
    (root / "README.md").write_bytes(b"Fixture README\n")
    (root / "LICENSE").write_bytes(b"Apache-2.0 Jellypod attribution\n")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "speech-sdk-python"\nversion = "{version}"\n', encoding="utf-8"
    )
    metadata = f"Metadata-Version: 2.4\nName: speech-sdk-python\nVersion: {version}\n\n".encode()
    metadata += (root / "README.md").read_bytes()
    info = f"speech_sdk_python-{version}.dist-info/"
    wheel = {
        "speech_sdk/__init__.py": (source / "__init__.py").read_bytes(),
        "speech_sdk/py.typed": b"",
        info + "METADATA": metadata,
        info + "licenses/LICENSE": (root / "LICENSE").read_bytes(),
    }
    prefix = f"speech_sdk_python-{version}/"
    sdist = {
        prefix + "src/" + name: data
        for name, data in wheel.items()
        if name.startswith("speech_sdk/")
    }
    sdist[prefix + "PKG-INFO"] = metadata
    for name in ("pyproject.toml", "LICENSE", "README.md"):
        sdist[prefix + name] = (root / name).read_bytes()
    return wheel, sdist


def archives(
    root: Path, wheel: dict[str, bytes], sdist: dict[str, bytes], version: str = "0.1.0"
) -> None:
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(dist / f"speech_sdk_python-{version}-py3-none-any.whl", "w") as output:
        for name, data in wheel.items():
            output.writestr(name, data)
    with tarfile.open(dist / f"speech_sdk_python-{version}.tar.gz", "w:gz") as output_tar:
        for name, data in sdist.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            output_tar.addfile(member, io.BytesIO(data))


class ReleaseWorkflowTests(unittest.TestCase):
    def assert_caller(self, release: dict[str, Any]) -> None:
        self.assertEqual(set(release), {"name", "on", "permissions", "concurrency", "jobs"})
        self.assertEqual(release["on"], {"push": {"branches": ["release"]}})
        self.assertEqual(release["permissions"], {})
        self.assertEqual(
            release["concurrency"], {"group": "release-pypi", "cancel-in-progress": False}
        )
        self.assertEqual(set(release["jobs"]), {"checks", "publish"})
        self.assertEqual(
            release["jobs"]["checks"],
            {"uses": "./.github/workflows/checks.yml", "permissions": {"contents": "read"}},
        )
        self.assertEqual(
            release["jobs"]["publish"],
            {
                "needs": "checks",
                "if": CONDITION,
                "runs-on": "ubuntu-24.04",
                "environment": "pypi",
                "permissions": {"id-token": "write"},
                "steps": [
                    {"uses": UV, "with": {"version": "0.12.18", "enable-cache": False}},
                    {"uses": DOWNLOAD, "with": {"name": ARTIFACT, "path": "dist/"}},
                    {
                        "name": "Publish validated distributions",
                        "run": "uv publish --trusted-publishing always dist/*.whl dist/*.tar.gz",
                    },
                ],
            },
        )

    def assert_checks(self, checks: dict[str, Any]) -> None:
        self.assertEqual(
            checks["on"],
            {"push": {"branches": ["main"]}, "pull_request": None, "workflow_call": None},
        )
        self.assertEqual(checks["permissions"], {"contents": "read"})
        self.assertEqual(
            checks["concurrency"],
            {
                "group": "checks-${{ github.ref }}",
                "cancel-in-progress": "${{ !(" + CONDITION + ") }}",
            },
        )
        self.assertEqual(set(checks["jobs"]), {"runtime", "quality"})
        self.assertEqual(checks["jobs"]["quality"]["needs"], "runtime")
        self.assertEqual(
            checks["jobs"]["runtime"]["strategy"], {"matrix": {"python": ["3.11", "3.14"]}}
        )
        for job in checks["jobs"].values():
            self.assertFalse(set(job) & {"permissions", "if", "continue-on-error", "secrets"})
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
            self.assertEqual(
                job["steps"][0], {"uses": CHECKOUT, "with": {"persist-credentials": False}}
            )
            self.assertEqual(job["steps"][1]["uses"], PYTHON)
            self.assertEqual(
                job["steps"][2], {"uses": UV, "with": {"version": "0.12.18", "enable-cache": False}}
            )
            for step in job["steps"]:
                self.assertFalse(set(step) & {"continue-on-error", "secrets"})
                self.assertNotIn("always()", str(step))
        self.assert_quality(checks["jobs"]["quality"]["steps"])
        self.assert_runtime(checks["jobs"]["runtime"]["steps"])

    def assert_quality(self, steps: list[dict[str, Any]]) -> None:
        self.assertEqual(
            [step.get("name") for step in steps[3:]],
            [
                "Install locked development tools",
                "Run the same gates as local hooks",
                "Validate release source",
                "Clear generated build output",
                "Build distributions",
                "Validate release bundle",
                "Verify clean wheel installation",
                "Upload validated release distributions",
            ],
        )
        self.assertEqual(
            [step["run"] for step in steps[3:9]],
            [
                "uv sync --frozen --python 3.11",
                "uv run --frozen prek run --all-files",
                "uv run --frozen python scripts/check_release.py validate-source",
                "uv run --frozen python scripts/check_release.py clean",
                "uv run --frozen python -m build",
                "uv run --frozen python scripts/check_release.py check-bundle",
            ],
        )
        self.assertEqual(
            [step.get("if") for step in steps[3:10]],
            [None, None, CONDITION, None, None, CONDITION, None],
        )
        self.assertEqual(
            steps[-1],
            {
                "name": "Upload validated release distributions",
                "if": CONDITION,
                "uses": UPLOAD,
                "with": {
                    "name": ARTIFACT,
                    "path": "dist/*.whl\ndist/*.tar.gz\n",
                    "if-no-files-found": "error",
                },
            },
        )
        install = steps[-2]["run"]
        for command in (
            "uv export --frozen --no-dev --no-emit-project --format requirements-txt",
            "--require-hashes -r /tmp/speech-sdk-runtime.txt",
            "--no-deps dist/*.whl",
            "cd /tmp/speech-sdk-installed",
            "env -u XAI_API_KEY -u OPENAI_API_KEY",
            "python -m unittest discover -s tests -v",
            'joinpath("py.typed").is_file()',
            '"site-packages" in speech_sdk.__file__',
            "cp tests/test_api.py tests/test_azure.py tests/test_streaming.py tests/test_smoke.py "
            "tests/test_pronunciations.py tests/test_result_details.py",
        ):
            self.assertIn(command, install)
        self.assertNotIn("test_release_workflow", install)

    def assert_runtime(self, steps: list[dict[str, Any]]) -> None:
        self.assertEqual(len(steps), 5)
        for step in steps[3:]:
            self.assertFalse(set(step) & {"if", "continue-on-error"})
        self.assertEqual(
            steps[3]["run"], 'uv sync --frozen --no-dev --python "${{ matrix.python }}"'
        )
        self.assertEqual(steps[4]["env"], {"PYTHONPATH": "tests"})
        self.assertEqual(
            steps[4]["run"],
            "env -u XAI_API_KEY -u OPENAI_API_KEY uv run --frozen --no-dev python "
            "-m unittest -v test_core test_http test_openai test_azure test_xai "
            "test_api test_streaming test_smoke test_pronunciations test_result_details",
        )

    def test_workflows(self) -> None:
        self.assert_caller(workflow("release.yml"))
        self.assert_checks(workflow("checks.yml"))

    def test_caller_mutations_fail(self) -> None:
        release = workflow("release.yml")
        mutations = [
            ("on", {"push": {"branches": ["main"]}}),
            ("on", {"pull_request": None}),
            ("permissions", {"contents": "write"}),
            ("concurrency", {"group": "release-pypi", "cancel-in-progress": True}),
        ]
        for field, value in mutations:
            changed = copy.deepcopy(release)
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(AssertionError):
                self.assert_caller(changed)
        for field, value in (
            ("needs", []),
            ("if", "always()"),
            ("permissions", {"id-token": "write", "contents": "read"}),
            ("steps", [{"run": "uv run python -m build"}]),
        ):
            changed = copy.deepcopy(release)
            changed["jobs"]["publish"][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.assert_caller(changed)
        changed = copy.deepcopy(release)
        changed["jobs"]["publish"]["steps"][1]["with"]["name"] = "other-artifact"
        with self.assertRaises(AssertionError):
            self.assert_caller(changed)

    def test_required_step_mutations_fail(self) -> None:
        checks = workflow("checks.yml")
        for job, indices in (("runtime", (3, 4)), ("quality", (3, 4, 9))):
            for index in indices:
                for field, value in (
                    ("missing", None),
                    ("if", "${{ false }}"),
                    ("if", "${{ failure() }}"),
                    ("continue-on-error", True),
                    ("continue-on-error", False),
                ):
                    changed = copy.deepcopy(checks)
                    steps = changed["jobs"][job]["steps"]
                    if field == "missing":
                        del steps[index]
                    else:
                        steps[index][field] = value
                    with (
                        self.subTest(job=job, index=index, field=field, value=value),
                        self.assertRaises((AssertionError, KeyError)),
                    ):
                        self.assert_checks(changed)
        for index in (5, 8, 10):
            changed = copy.deepcopy(checks)
            del changed["jobs"]["quality"]["steps"][index]["if"]
            with self.subTest(missing_condition=index), self.assertRaises(AssertionError):
                self.assert_checks(changed)

    def test_checks_mutations_fail(self) -> None:
        checks = workflow("checks.yml")
        for kind in ("needs", "upload-order", "upload-condition", "permissions", "artifact"):
            changed = copy.deepcopy(checks)
            quality = changed["jobs"]["quality"]
            if kind == "needs":
                quality.pop("needs")
            elif kind == "upload-order":
                quality["steps"].insert(3, quality["steps"].pop())
            elif kind == "upload-condition":
                quality["steps"][-1]["if"] = "always()"
            elif kind == "permissions":
                changed["permissions"]["id-token"] = "write"
            else:
                quality["steps"][-1]["with"]["overwrite"] = True
            with self.subTest(kind=kind), self.assertRaises((AssertionError, KeyError)):
                self.assert_checks(changed)


class ReleaseGuardTests(unittest.TestCase):
    def guard(self, root: Path, mode: str = "check-bundle") -> int:
        with redirect_stderr(io.StringIO()):
            return main([mode, str(root)])

    def test_valid_public_versions(self) -> None:
        for version in ("0.1.0", "0.1.0rc1", "0.1.0.post1", "0.1.0.dev1"):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                root = Path(directory)
                wheel, sdist = fixture(root, version)
                archives(root, wheel, sdist, version)
                self.assertEqual(self.guard(root, "validate-source"), 0)
                self.assertEqual(self.guard(root), 0)

    def test_source_rejections_and_main_placeholder(self) -> None:
        projects = [
            'name="speech-sdk-python"\nversion="' + version + '"'
            for version in (
                "0.0.0",
                "0",
                "0.0",
                "0.0.0.0",  # noqa: S104 -- PEP 440 zero-version fixture, not a bind address
                "00.0.0",
                "",
                "bad",
                "0.1.0+local",
                "v0.1.0",
            )
        ] + [
            'name="wrong"\nversion="0.1.0"',
            'name="speech-sdk-python"',
            'name="speech-sdk-python"\nversion=1',
            'version="0.1.0"',
            "broken=",
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for project in projects:
                (root / "pyproject.toml").write_text("[project]\n" + project, encoding="utf-8")
                with self.subTest(project=project):
                    self.assertEqual(self.guard(root, "validate-source"), 1)
                    self.assertFalse((root / "dist").exists())
            (root / "pyproject.toml").write_text(
                '[project]\nname="speech-sdk-python"\nversion="0.0.0"', encoding="utf-8"
            )
            self.assertEqual(str(source_version(root, release=False)), "0.0.0")
            self.assertEqual(str(source_version(ROOT, release=False)), "0.0.0")

    def test_missing_and_static_cli_errors(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            self.assertEqual(self.guard(root), 1)
            for args in ([], ["unknown"], ["clean", "a", "b"], ["validate-source", "/missing"]):
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertEqual(main(args), 1)
                self.assertEqual(
                    output.getvalue(),
                    "Release guard failed: invalid source or distribution bundle\n",
                )

    def test_metadata_and_source_rejections(self) -> None:
        for target in ("wheel", "sdist"):
            for defect in (
                "name",
                "version",
                "duplicate",
                "missing",
                "malformed",
                "readme",
                "sdk",
                "typed",
                "license",
            ):
                with self.subTest(target=target, defect=defect), TemporaryDirectory() as directory:
                    root = Path(directory)
                    wheel, sdist = fixture(root)
                    files = wheel if target == "wheel" else sdist
                    meta = next(name for name in files if name.endswith(("METADATA", "PKG-INFO")))
                    self.damage(files, meta, defect)
                    archives(root, wheel, sdist)
                    self.assertEqual(self.guard(root), 1)

    def damage(self, files: dict[str, bytes], meta: str, defect: str) -> None:
        replacements = {
            "name": (b"Name: speech-sdk-python", b"Name: wrong"),
            "version": (b"Version: 0.1.0", b"Version: 0.2.0"),
            "duplicate": (b"Version: 0.1.0", b"Version: 0.1.0\nVersion: 0.1.0"),
            "missing": (b"Name: speech-sdk-python\n", b""),
            "malformed": (b"Name: speech-sdk-python", b"not a header"),
            "readme": (b"Fixture README", b"other README"),
        }
        if defect in replacements:
            old, new = replacements[defect]
            files[meta] = files[meta].replace(old, new)
        else:
            suffix = {"sdk": "__init__.py", "typed": "py.typed", "license": "LICENSE"}[defect]
            name = next(name for name in files if name.endswith(suffix))
            if defect == "sdk":
                files[name] = b"stale SDK"
            else:
                del files[name]

    def test_extra_private_and_unsafe_members(self) -> None:
        for target in ("wheel", "sdist"):
            for name in (
                ".env",
                "artifacts/private.wav",
                "../escape",
                "/absolute",
                "a//b",
                "a\\b",
                "speech_sdk/secret.txt",
            ):
                with self.subTest(target=target, name=name), TemporaryDirectory() as directory:
                    root = Path(directory)
                    wheel, sdist = fixture(root)
                    files = wheel if target == "wheel" else sdist
                    prefix = "" if target == "wheel" else "speech_sdk_python-0.1.0/"
                    # The SDK exclusion in an sdist is rooted under src, as in a real build.
                    if target == "sdist" and name.startswith("speech_sdk/"):
                        prefix += "src/"
                    files[prefix + name] = b"private"
                    archives(root, wheel, sdist)
                    self.assertEqual(self.guard(root), 1)

    def test_dist_entries_and_filenames(self) -> None:
        for defect in (
            "missing",
            "stale",
            "extra",
            "tags",
            "version",
            "symlink",
            "directory",
            "corrupt",
        ):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                root = Path(directory)
                wheel, sdist = fixture(root)
                archives(root, wheel, sdist)
                dist = root / "dist"
                path = next(dist.glob("*.whl"))
                self.damage_dist(path, defect)
                self.assertEqual(self.guard(root), 1)

    def damage_dist(self, path: Path, defect: str) -> None:
        if defect == "missing":
            path.unlink()
        elif defect in {"stale", "extra"}:
            (path.parent / "extra.whl").write_bytes(b"stale")
        elif defect in {"tags", "version"}:
            old, new = (
                ("py3-none-any", "cp311-none-any") if defect == "tags" else ("0.1.0", "0.2.0")
            )
            path.rename(path.with_name(path.name.replace(old, new)))
        elif defect == "corrupt":
            path.write_bytes(b"invalid zip")
        else:
            path.unlink()
            if defect == "symlink":
                path.symlink_to(path.parent.parent / "LICENSE")
            else:
                path.mkdir()

    def test_sdist_source_metadata_and_root(self) -> None:
        for defect in ("pyproject.toml", "README.md", "LICENSE", "root"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                root = Path(directory)
                wheel, sdist = fixture(root)
                if defect == "root":
                    sdist = {
                        name.replace("speech_sdk_python-0.1.0", "other-0.1.0"): data
                        for name, data in sdist.items()
                    }
                else:
                    sdist[f"speech_sdk_python-0.1.0/{defect}"] = b"stale source metadata"
                archives(root, wheel, sdist)
                self.assertEqual(self.guard(root), 1)

    def test_archive_duplicates_and_links(self) -> None:
        for target in ("wheel", "sdist"):
            for defect in ("duplicate", "symlink"):
                with self.subTest(target=target, defect=defect), TemporaryDirectory() as directory:
                    root = Path(directory)
                    wheel, sdist = fixture(root)
                    archives(root, wheel, sdist)
                    self.damage_archive(root, target, defect, sdist)
                    self.assertEqual(self.guard(root), 1)

    def damage_archive(self, root: Path, target: str, defect: str, sdist: dict[str, bytes]) -> None:
        if target == "wheel":
            with zipfile.ZipFile(next((root / "dist").glob("*.whl")), "a") as output:
                member = zipfile.ZipInfo(
                    "speech_sdk/__init__.py" if defect == "duplicate" else "linked"
                )
                if defect == "symlink":
                    member.external_attr = 0o120777 << 16
                # Duplicate warnings are expected and do not alter guard failure behavior.
                output.writestr(member, b"bad")
        else:
            with tarfile.open(next((root / "dist").glob("*.tar.gz")), "w:gz") as output_tar:
                for name, data in sdist.items():
                    member_tar = tarfile.TarInfo(name)
                    member_tar.size = len(data)
                    output_tar.addfile(member_tar, io.BytesIO(data))
                member_tar = tarfile.TarInfo(
                    next(iter(sdist)) if defect == "duplicate" else "linked"
                )
                if defect == "symlink":
                    member_tar.type = tarfile.SYMTYPE
                    member_tar.linkname = "/outside"
                    self.assertEqual(member_tar.linkname, "/outside")
                output_tar.addfile(member_tar, io.BytesIO())

    def test_checkout_mismatch_and_source_symlinks(self) -> None:
        for defect in ("stale", "symlink", "missing-typed"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                root = Path(directory)
                wheel, sdist = fixture(root)
                archives(root, wheel, sdist)
                source = root / "src/speech_sdk"
                if defect == "stale":
                    (source / "__init__.py").write_bytes(b"changed after build")
                elif defect == "symlink":
                    (source / "linked").symlink_to(root, target_is_directory=True)
                else:
                    (source / "py.typed").unlink()
                self.assertEqual(self.guard(root), 1)

    def test_clean_only_generated_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            for name in ("dist", "build", "src/speech_sdk_python.egg-info"):
                path = root / name
                path.mkdir()
                (path / "stale").write_bytes(b"stale")
            keep = root / "artifacts"
            keep.mkdir()
            (keep / "untouched").write_bytes(b"private")
            self.assertEqual(self.guard(root, "clean"), 0)
            self.assertTrue((keep / "untouched").is_file())
            self.assertTrue((root / "src/speech_sdk/__init__.py").is_file())
            (root / "dist").symlink_to(keep, target_is_directory=True)
            self.assertEqual(self.guard(root, "clean"), 0)
            self.assertFalse((root / "dist").is_symlink())
            self.assertTrue((keep / "untouched").is_file())


if __name__ == "__main__":
    unittest.main()
