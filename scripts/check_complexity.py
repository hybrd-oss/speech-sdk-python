"""Fail when a function/method exceeds Radon complexity 10, or source cannot be read."""

import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

from radon.complexity import add_inner_blocks, cc_visit
from radon.visitors import Function

MAX_COMPLEXITY = 10


class Block(Protocol):
    """Typed boundary for Radon's untyped block objects."""

    name: str
    lineno: int
    complexity: int


visit = cast(Callable[[str], list[Block]], cc_visit)
expand = cast(Callable[[list[Block]], list[Block]], add_inner_blocks)


def check_file(path: Path) -> list[str]:
    try:
        blocks = expand(visit(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, SyntaxError) as error:
        return [f"{path}: cannot analyze: {error}"]
    return [
        f"{path}:{block.lineno}: {block.name} complexity {block.complexity} > {MAX_COMPLEXITY}"
        for block in blocks
        if isinstance(block, Function) and block.complexity > MAX_COMPLEXITY
    ]


def main(paths: Sequence[str] = ()) -> int:
    roots = (
        [Path(path) for path in paths]
        if paths
        else [Path(name) for name in ("src", "scripts", "tests", "smoke") if Path(name).exists()]
    )
    failures: list[str] = []
    for root in roots:
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in files:
            if not path.is_relative_to(Path("tests/semgrep")):
                failures.extend(check_file(path))
    for failure in failures:
        print(failure, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
