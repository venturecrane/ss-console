#!/usr/bin/env python3
"""Print the jobs runner test files that one half of the CI `runner` suite runs.

The `runner` job of .github/workflows/operator-substrate.yml runs its suite as
two jobs, one per half, behind a summary job that keeps the required check name
`runner`. The split is deterministic and needs no stored timings. The two
matrix files in runners/jobs/tests/spread-files.txt, which hold half the
suite's time, go one to each half (the N-th, counting from 0, to half N mod 2
+ 1); every other file's half is fixed by its path alone (crc32 of the path
from operator/, mod 2), so a new file lands in a half without anyone choosing
it. Measured 2026-11-21 (`--durations=0`, four workers): about 1,016 s and
1,158 s of worker time of the suite's 2,174 s.

    python3 bin/runner-half.py 1   # from operator/: half 1's files, one per line

bin/tests/test_ci_coverage_conformance.py asserts the two halves cover every
test file exactly once.
"""

from __future__ import annotations

import fnmatch
import sys
import tomllib
import zlib
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[1]
TESTS = OPERATOR / "runners" / "jobs" / "tests"
SPREAD = TESTS / "spread-files.txt"
PYPROJECT = OPERATOR / "runners" / "jobs" / "pyproject.toml"
HALVES = 2
#: pytest's own default for `python_files`, used when the runner's config sets none.
PYTEST_DEFAULT_FILES = ["test_*.py", "*_test.py"]


def python_files(pyproject: Path = PYPROJECT) -> list[str]:
    """The file patterns pytest collects by: the runner's `python_files`, or
    pytest's default. Read here, not restated, so a `foo_test.py` is in a half."""
    ini = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("pytest", {}).get("ini_options", {})
    got = ini.get("python_files", PYTEST_DEFAULT_FILES)
    return [got] if isinstance(got, str) else list(got)


def spread_files() -> list[str]:
    """spread-files.txt's names, in order, as paths from operator/."""
    names = [ln.strip() for ln in SPREAD.read_text(encoding="utf-8").splitlines()]
    return [(TESTS / n).relative_to(OPERATOR).as_posix() for n in names if n and not n.startswith("#")]


def runner_test_files(tests: Path = TESTS, patterns: list[str] | None = None, root: Path = OPERATOR) -> list[str]:
    """Every file pytest collects under ``tests``, as paths from ``root`` (operator/)."""
    patterns = python_files() if patterns is None else patterns
    return sorted(
        p.relative_to(root).as_posix()
        for p in tests.rglob("*.py")
        if "__pycache__" not in p.parts and any(fnmatch.fnmatch(p.name, pat) for pat in patterns)
    )


def half_of(path: str, spread: list[str] | None = None) -> int:
    spread = spread_files() if spread is None else spread
    if path in spread:
        return spread.index(path) % HALVES + 1
    return zlib.crc32(path.encode("utf-8")) % HALVES + 1


def files_in(half: int) -> list[str]:
    spread = spread_files()
    return [p for p in runner_test_files() if half_of(p, spread) == half]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {str(h) for h in range(1, HALVES + 1)}:
        print(f"usage: runner-half.py <1..{HALVES}>", file=sys.stderr)
        return 2
    files = files_in(int(argv[0]))
    if not files:
        print(f"runner-half.py: half {argv[0]} has no test files", file=sys.stderr)
        return 1
    print("\n".join(files))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
