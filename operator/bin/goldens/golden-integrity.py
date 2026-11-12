#!/usr/bin/env python3
"""The goldens hold the OLD code's behaviour: regenerate them from the
merge-base tree and fail on any difference ``allowed-changes.json`` does not name.

WHY. A golden test compares the code with the golden file next to it. A PR
that changes the code AND rewrites the golden passes it, and then the golden
proves nothing about the change. This job closes that: it takes the tree the
PR branched from, puts THIS PR's tests and golden harness into it (the
instrument is the PR's; the behaviour measured is the base's), runs the
harness there in write mode, and compares what it wrote with the goldens the
PR commits. Every difference must be a path the PR names in that golden set's
``allowed-changes.json``, an edit reviewed with the PR.

The golden sets it regenerates:

* the job host matrix, ``runners/jobs/tests/goldens/host_matrix/``
  (``test_host_matrix_goldens.py``);
* the broker ledger goldens, ``workspace_broker/tests/goldens/ledgers/<lane>/``
  (``test_ledger_goldens.py``).

Run from ``operator/`` (``uv`` on PATH; CI: the ``goldens`` job of
``.github/workflows/operator-substrate.yml``):

    python3 bin/goldens/golden-integrity.py --base <merge-base sha>

Exit 0 when every difference is allowed; 1 otherwise, listing them.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
#: The PR's instrument, laid over the base tree: the two test dirs (their kits,
#: testkits and conftest) and the shared golden modules.
OVERLAY = (
    "runners/jobs/tests",
    "workspace_broker/tests",
    "bin/lib/golden_diff.py",
    "bin/lib/golden_broker.py",
)
LEDGER_GOLDENS = OPERATOR / "workspace_broker" / "tests" / "goldens" / "ledgers"


def sets() -> list[tuple[str, str]]:
    """(golden dir under operator/, the out subdir the harness writes) per set:
    the host matrix, and each ledger lane's dir as committed."""
    lanes = sorted(p.name for p in LEDGER_GOLDENS.iterdir() if p.is_dir())
    return [
        ("runners/jobs/tests/goldens/host_matrix", "host_matrix"),
        *((f"workspace_broker/tests/goldens/ledgers/{lane}", f"ledgers/{lane}") for lane in lanes),
    ]


TESTS = (
    ("runners/jobs/tests/test_host_matrix_goldens.py", True),
    ("workspace_broker/tests/test_ledger_goldens.py", False),
)


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=env)  # noqa: S603 - fixed tool argv over this repo's trees


def base_tree(sha: str, dest: Path) -> Path:
    """``operator/`` as it was at ``sha``, extracted (no worktree registered)."""
    raw = subprocess.run(["git", "archive", sha, "operator"], cwd=REPO, check=True, capture_output=True).stdout  # noqa: S603 - a fixed git argv over this repo, no input in it
    subprocess.run(["tar", "-x", "-C", str(dest)], input=raw, check=True)  # noqa: S603 - this repo's own archive
    return dest / "operator"


def overlay(base: Path) -> None:
    for rel in OVERLAY:
        src, dst = OPERATOR / rel, base / rel
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def venv(base: Path, at: Path) -> Path:
    """The base tree's runner installed as the seat installs it."""
    py = at / "bin" / "python"
    run(["uv", "venv", "--quiet", "--python", "3.13", str(at)], base)
    run(["uv", "pip", "install", "--quiet", "--python", str(py), "--no-config", "--require-hashes", "-r", "requirements/jobs.txt"], base)  # fmt: skip
    run(["uv", "pip", "install", "--quiet", "--python", str(py), "--no-deps", "./connectors/_sdk", "./connectors/_contract",
         "./connectors/smokeball", "./runners/jobs"], base)  # fmt: skip
    run(["uv", "pip", "install", "--quiet", "--python", str(py), "pytest", "pytest-xdist"], base)
    return py


def regenerate(base: Path, py: Path, out: Path) -> None:
    import os

    env = {**os.environ, "JOBS_GOLDENS_OUT": str(out)}
    for test, runner in TESTS:
        cfg = ["-c", "runners/jobs/pyproject.toml"] if runner else ["-p", "no:cacheprovider"]
        run([str(py), "-m", "pytest", *cfg, test, "-q", "-n", "4", "-k", "not every"], base, env)


def compare(out: Path) -> dict[str, dict[str, list[str]]]:
    if str(OPERATOR) not in sys.path:
        sys.path.insert(0, str(OPERATOR))
    from bin.lib.golden_diff import compare_dirs

    bad: dict[str, dict[str, list[str]]] = {}
    for golden, sub in sets():
        found = compare_dirs(OPERATOR / golden, out / sub, skip=("aged.json",))
        if found:
            bad[golden] = found
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the merge-base commit")
    ap.add_argument("--python", help="a venv python with the BASE runner installed (default: build one with uv)")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="goldens-base-") as tmp:
        base = base_tree(a.base, Path(tmp))
        overlay(base)
        py = Path(a.python) if a.python else venv(base, Path(tmp) / "venv")
        out = Path(tmp) / "out"
        regenerate(base, py, out)
        bad = compare(out)
    if not bad:
        print("golden integrity: every golden equals the merge-base tree's behaviour, or differs only where allowed")
        return 0
    print(
        "golden integrity: the base tree's behaviour differs from the committed goldens where no allowed change names it:"
    )
    for golden, files in bad.items():
        for name, paths in files.items():
            print(f"  {golden}/{name}.json: {', '.join(paths[:20])}{' ...' if len(paths) > 20 else ''}")
    print("Either the code changed behaviour (name each path in that set's allowed-changes.json, reviewed), "
          "or a golden was rewritten to match new behaviour (restore it).")  # fmt: skip
    return 1


if __name__ == "__main__":
    sys.exit(main())
