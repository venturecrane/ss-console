#!/usr/bin/env python3
"""Cross-version job folders: the merge-base's job host and this tree's carry
each other's jobs across a restart, both directions.

WHY. A release restarts the jobs daemon between two ticks of any job, so the
new host resumes job folders the old host wrote, and a roll back hands the new
host's folders to the old one. The host matrix goldens hold each version to
the same behaviour within one process; this runs
``runners/jobs/tests/test_host_cross_version.py`` with the merge-base tree as
the old version, so every checked cell is restarted after each of its ticks
onto the other version and must record exactly what the old version's own
restart records.

Run from ``operator/`` (``uv`` on PATH):

    python3 bin/goldens/cross-version-hosts.py --base <merge-base sha> [--python <runner venv python>] [--lanes a,b]

Exit 0 when every case passes; the pytest exit code otherwise.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

OPERATOR = Path(__file__).resolve().parents[2]
TEST = "runners/jobs/tests/test_host_cross_version.py"


def _integrity() -> Any:
    """``golden-integrity.py``'s tree extraction and runner venv, reused."""
    spec = importlib.util.spec_from_file_location("golden_integrity", OPERATOR / "bin/goldens/golden-integrity.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the merge-base commit (the old version)")
    ap.add_argument("--python", help="a venv python with the runner's dependencies (default: build one with uv)")
    ap.add_argument("--lanes", default="", help="only these lanes, comma-separated (default: the test's own list)")
    ap.add_argument("--workers", default="4", help="pytest-xdist workers")
    a = ap.parse_args(argv)
    gi = _integrity()
    with tempfile.TemporaryDirectory(prefix="hosts-xv-") as tmp:
        old = gi.extract(a.base, Path(tmp) / "base")
        py = Path(a.python) if a.python else gi.runner_venv(OPERATOR, Path(tmp) / "venv")
        run_env = {**gi.code_path(OPERATOR), "JOBS_CROSS_VERSION_OLD": str(old)}
        if a.lanes:
            run_env["JOBS_CROSS_VERSION_LANES"] = a.lanes
        cmd = [str(py), "-m", "pytest", "-c", "runners/jobs/pyproject.toml", TEST, "-q", "-p", "no:cacheprovider",
               "-n", a.workers]  # fmt: skip
        print("+", " ".join(cmd), f"(old version: {a.base})", flush=True)
        code = subprocess.run(cmd, cwd=OPERATOR, env=run_env, check=False).returncode  # noqa: S603 - this tree's own test over a fixed argv
    print(f"cross-version job folders: {'every case passed' if code == 0 else 'FAILED'}")
    return code


if __name__ == "__main__":
    os.chdir(OPERATOR)
    sys.exit(main())
