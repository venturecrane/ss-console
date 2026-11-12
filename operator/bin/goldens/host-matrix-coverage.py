#!/usr/bin/env python3
"""Branch coverage of the job hosts' ending, wake and transition code under
the host matrix, written to ``runners/jobs/tests/goldens/host_matrix_coverage.json``.

WHY. The host matrix (``runners/jobs/tests/test_host_matrix_goldens.py``) is
meant to hold every ending the hosts' code can reach. Which endings exist is
read from the code's branches, not from memory: this script runs the matrix
under ``coverage.py --branch`` and lists, per host function that decides an
ending, a wake or a transition, every line and branch the matrix never took.
Each one is either a cell to add or a reason it is unreachable; the report is
committed so a reviewer sees what the matrix does NOT hold.

Run it in the runner's venv (``coverage`` installed), from ``operator/``:

    python bin/goldens/host-matrix-coverage.py [--python /path/to/venv/python]

It runs the matrix serially (coverage of xdist workers is not collected here).
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
JOBS = OPERATOR / "runners" / "jobs" / "jobs"
OUT = OPERATOR / "runners" / "jobs" / "tests" / "goldens" / "host_matrix_coverage.json"

#: The host files, and the functions in them that decide an ending, a wake or a
#: transition (the claim, the run, the report, the wake, the loop's own steps).
HOSTS = (
    "daemon.py",
    "medchron/lane.py",
    "demand_lane.py",
    "drafting_lane.py",
    "library_setup_lane.py",
    "litigation_lane.py",
    "casefile_lane.py",
    "negotiation_lane.py",
    "records_lane.py",
    "note_lane.py",
)
FUNCTIONS = frozenset(
    {
        "claim_next", "_in_progress", "run_job", "_report", "_report_reentry", "_compose_wake", "_deadlines_wake",
        "_fail_unconfigured", "_fail", "tick", "_defer", "_ready", "_paused", "_refuse_held_resumes", "_spawn",
        "_allowance_hold", "_allowance_reason", "_notice_wake", "dispatch_wakes", "_dispatch_wakes",
        "_thread_already_answered", "_write_job", "_write_job_yaml", "_ledger_fields", "run", "read_verdict",
        "_busy", "_same_matter_busy", "_child_env", "_cgroup_preexec", "fields_of", "compose_wake", "wake_task",
        "record", "status", "month_state", "month_cents", "list_allowance", "_seed_from_superseded",
    }
)  # fmt: skip


def _functions(path: Path) -> list[tuple[str, int, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name in FUNCTIONS:
                    out.append((f"{node.name}.{f.name}", f.lineno, f.end_lineno or f.lineno))
    for f in tree.body:
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name in FUNCTIONS:
            out.append((f.name, f.lineno, f.end_lineno or f.lineno))
    return sorted(out, key=lambda t: t[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    include = ",".join(str(JOBS / h) for h in HOSTS)
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / ".coverage"
        env_cmd = [a.python, "-m", "coverage", "run", "--branch", f"--data-file={data}", f"--include={include}"]
        test = ["-m", "pytest", "-c", "runners/jobs/pyproject.toml", "runners/jobs/tests/test_host_matrix_goldens.py", "-q", "-p", "no:xdist"]  # fmt: skip
        subprocess.run([*env_cmd, *test], cwd=OPERATOR, check=False)  # noqa: S603 - fixed argv, this tree
        report = Path(tmp) / "cov.json"
        subprocess.run(  # noqa: S603 - fixed argv, this tree
            [a.python, "-m", "coverage", "json", f"--data-file={data}", "-o", str(report)], cwd=OPERATOR, check=True
        )
        cov = json.loads(report.read_text(encoding="utf-8"))
    files = {str(Path(k).resolve()): v for k, v in cov["files"].items()}
    out: dict[str, object] = {
        "_why": "Branch coverage of the job hosts' ending/wake/transition functions under the host matrix "
        "(bin/goldens/host-matrix-coverage.py). Every uncovered line is a cell the matrix does not hold; "
        "regenerate after adding cells.",
        "hosts": {},
    }
    total = {"lines": 0, "covered": 0, "branches": 0, "branches_covered": 0}
    for h in HOSTS:
        path = JOBS / h
        f = files.get(
            str(path.resolve()),
            {"missing_lines": [], "executed_lines": [], "missing_branches": [], "executed_branches": []},
        )
        missing, executed = set(f["missing_lines"]), set(f["executed_lines"])
        mb = [tuple(b) for b in f.get("missing_branches", [])]
        eb = [tuple(b) for b in f.get("executed_branches", [])]
        src = path.read_text(encoding="utf-8").splitlines()
        per: dict[str, object] = {}
        for name, lo, hi in _functions(path):
            span = range(lo, hi + 1)
            lm = sorted(n for n in missing if n in span)
            le = sorted(n for n in executed if n in span)
            bm = sorted(b for b in mb if b[0] in span)
            be = sorted(b for b in eb if b[0] in span)
            total["lines"] += len(lm) + len(le)
            total["covered"] += len(le)
            total["branches"] += len(bm) + len(be)
            total["branches_covered"] += len(be)
            per[name] = {
                "lines": len(lm) + len(le),
                "lines_covered": len(le),
                "branches": len(bm) + len(be),
                "branches_covered": len(be),
                "uncovered": [f"{n}: {src[n - 1].strip()}" for n in lm],
                "branches_not_taken": [f"{b[0]}->{b[1]}" for b in bm],
            }
        out["hosts"][h] = per  # type: ignore[index]
    out["total"] = total
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
