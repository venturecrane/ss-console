"""Replay the existing tests that reach either decision under the recorder.

FROZEN with ``recorder.py``. One pytest process over ``tables.SELECTION``
(forward or reversed), or one process per file, each writing
``<out>/existing/<stem>.json`` and ``<out>/coverage.json``:

    python -m tests.decisions.replay <out dir> [forward|reverse|per-file]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.decisions import tables

OVERLAY = Path(__file__).resolve().parents[2]


def _env(out: Path, order: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SMD_DECISIONS", "PYTEST_"))}
    env["SMD_DECISIONS_OUT"] = str(out)
    if order == "reverse":
        env["SMD_DECISIONS_ORDER"] = "reverse"
    return env


def _pytest(out: Path, order: str, files: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tests.decisions.recorder",
        *files,
    ]
    return subprocess.run(  # noqa: S603 - this repo's own tests, a fixed argv
        cmd, cwd=OVERLAY, env=_env(out, order), capture_output=True, text=True, check=False
    )


def _merge_coverage(parts: list[Path], out: Path) -> None:
    merged: dict[str, set[tuple]] = {"sites": set(), "branches": set(), "lines": set()}
    for p in parts:
        doc = json.loads(p.read_text(encoding="utf-8"))
        for k in merged:
            merged[k] |= {tuple(x) for x in doc.get(k, [])}
    out.write_text(
        json.dumps({k: sorted(list(x) for x in v) for k, v in merged.items()}) + "\n",
        encoding="utf-8",
    )


def _problems(out: Path, done: subprocess.CompletedProcess[str], label: str) -> list[str]:
    """A run fails when pytest itself failed (exit 2 and up) or a test that
    reached a decision failed; a selected file's test that never reached one
    is not in the corpus, so its failure is not the corpus's."""
    tail = f"{label}: {done.stdout[-3000:]}{done.stderr[-1000:]}"
    if done.returncode not in (0, 1):
        return [tail]
    path = out / "failures.json"
    if not path.is_file():
        return [tail]
    reached = json.loads(path.read_text(encoding="utf-8"))["reached"]
    return [f"{label}: failed under the recorder: {', '.join(reached)}"] if reached else []


def replay(out: Path, order: str = "forward") -> list[str]:
    """Record the selection into ``out``; returns the failures (empty when every
    process passed)."""
    files = [f for f in tables.SELECTION if (OVERLAY / f).is_file()]
    if order != "per-file":
        if order == "reverse":
            files = list(reversed(files))
        return _problems(out, _pytest(out, order, files), order)
    parts = [out / "parts" / Path(f).stem for f in files]
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        runs = list(
            pool.map(lambda fp: _pytest(fp[1], "forward", [fp[0]]), zip(files, parts, strict=True))
        )
    failures = []
    for f, part, done in zip(files, parts, runs, strict=True):
        found = _problems(part, done, f)
        if found:
            failures += found
            continue
        for src in (part / "existing").glob("*.json"):
            dst = out / "existing" / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    _merge_coverage(
        [p / "coverage.json" for p in parts if (p / "coverage.json").is_file()],
        out / "coverage.json",
    )
    return failures


if __name__ == "__main__":
    problems = replay(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "forward")
    for p in problems:
        print(p)
    sys.exit(1 if problems else 0)
