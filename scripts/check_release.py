"""Dev-only release guards: inspect bytes, never extract or execute distributions."""

import shutil
import stat
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

NAME = "speech-sdk-python"
STEM = "speech_sdk_python"


def source_version(root: Path, *, release: bool = True) -> Version:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if not isinstance(project, dict):
        raise ValueError("Invalid source project table")
    value = project.get("version")
    if project.get("name") != NAME or not isinstance(value, str):
        raise ValueError("Invalid source project identity")
    version = Version(value)
    if str(version) != value or version.local is not None:
        raise ValueError("Source version must be canonical and public")
    if release and version == Version("0.0.0"):
        raise ValueError("Choose a new non-placeholder version before release")
    return version


def clean(root: Path) -> None:
    """Remove only setuptools/build output; unlink symlinks without following them."""
    for path in (root / "dist", root / "build", root / "src" / f"{STEM}.egg-info"):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


def safe_member(name: str) -> None:
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or ".." in parts or "\\" in name:
        raise ValueError("Unsafe archive path")
    if PurePosixPath(name).as_posix() != name.rstrip("/"):
        raise ValueError("Noncanonical archive path")


def wheel_bytes(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        seen: set[str] = set()
        for member in archive.infolist():
            safe_member(member.filename)
            if member.filename in seen or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Duplicate or linked wheel member")
            seen.add(member.filename)
            if not member.is_dir():
                files[member.filename] = archive.read(member)
    return files


def sdist_bytes(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        seen: set[str] = set()
        for member in archive.getmembers():
            safe_member(member.name)
            if member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("Duplicate or nonregular sdist member")
            seen.add(member.name)
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("Unreadable sdist member")
                files[member.name] = stream.read()
    return files


def metadata(data: bytes, version: Version, readme: bytes) -> None:
    message = BytesParser().parsebytes(data)
    if message.defects:
        raise ValueError("Malformed distribution metadata")
    if message.get_all("Name") != [NAME] or message.get_all("Version") != [str(version)]:
        raise ValueError("Distribution metadata does not match source identity")
    if readme not in data:
        raise ValueError("Distribution metadata is missing source README")


def sdk_bytes(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted((root / "src" / "speech_sdk").rglob("*")):
        if path.is_symlink():
            raise ValueError("Linked SDK source file")
        if "__pycache__" in path.parts or path.is_dir():
            continue
        if path.suffix != ".py" and path.name != "py.typed":
            raise ValueError("Unexpected SDK source file")
        files[path.relative_to(root / "src").as_posix()] = path.read_bytes()
    if "speech_sdk/__init__.py" not in files or "speech_sdk/py.typed" not in files:
        raise ValueError("SDK source is incomplete")
    return files


def compare_sdk(files: dict[str, bytes], source: dict[str, bytes], prefix: str = "") -> None:
    actual = {
        name.removeprefix(prefix): data
        for name, data in files.items()
        if name.startswith(prefix + "speech_sdk/")
    }
    if actual != source:
        raise ValueError("Distribution SDK bytes do not match source")


def private_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    forbidden = {".git", ".venv", "__pycache__", "artifacts", "dist", "build", "credentials"}
    return (
        bool(forbidden.intersection(parts))
        or any(part.startswith(".env") for part in parts)
        or PurePosixPath(name).suffix.lower()
        in {".wav", ".mp3", ".pcm", ".flac", ".key", ".pem", ".pyc"}
    )


def check_wheel(path: Path, root: Path, version: Version, source: dict[str, bytes]) -> None:
    name, parsed, build, tags = parse_wheel_filename(path.name)
    if name != NAME or parsed != version or build or {str(tag) for tag in tags} != {"py3-none-any"}:
        raise ValueError("Unexpected wheel filename or tags")
    files = wheel_bytes(path)
    info = f"{STEM}-{version}.dist-info/"
    allowed = set(source) | {
        info + part for part in ("METADATA", "WHEEL", "RECORD", "top_level.txt", "licenses/LICENSE")
    }
    if not set(files) <= allowed:
        raise ValueError("Unexpected wheel contents")
    compare_sdk(files, source)
    metadata(files[info + "METADATA"], version, (root / "README.md").read_bytes())
    if files[info + "licenses/LICENSE"] != (root / "LICENSE").read_bytes():
        raise ValueError("Wheel license differs from source")


def check_sdist(path: Path, root: Path, version: Version, source: dict[str, bytes]) -> None:
    project_name, parsed = parse_sdist_filename(path.name)
    if project_name != NAME or parsed != version:
        raise ValueError("Unexpected sdist filename")
    files = sdist_bytes(path)
    prefix = f"{STEM}-{version}/"
    if any(not name.startswith(prefix) or private_member(name) for name in files):
        raise ValueError("Unexpected or private sdist contents")
    compare_sdk(files, source, prefix + "src/")
    metadata(files[prefix + "PKG-INFO"], version, (root / "README.md").read_bytes())
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        if files[prefix + name] != (root / name).read_bytes():
            raise ValueError("Sdist source metadata differs from checkout")


def check_bundle(root: Path) -> None:
    version = source_version(root)
    paths = list((root / "dist").iterdir())
    expected = {f"{STEM}-{version}-py3-none-any.whl", f"{STEM}-{version}.tar.gz"}
    if {path.name for path in paths} != expected:
        raise ValueError("Expected exactly one current wheel and sdist")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("Distributions must be regular non-symlink files")
    source = sdk_bytes(root)
    check_wheel(root / "dist" / f"{STEM}-{version}-py3-none-any.whl", root, version, source)
    check_sdist(root / "dist" / f"{STEM}-{version}.tar.gz", root, version, source)


def main(args: list[str]) -> int:
    try:
        mode, *paths = args
        root = Path(paths[0]) if len(paths) == 1 else Path(".")
        if len(paths) > 1:
            raise ValueError("Too many paths")
        if mode == "validate-source":
            source_version(root)
        elif mode == "clean":
            clean(root)
        elif mode == "check-bundle":
            check_bundle(root)
        else:
            raise ValueError("Unknown guard mode")
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ):
        print("Release guard failed: invalid source or distribution bundle", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
