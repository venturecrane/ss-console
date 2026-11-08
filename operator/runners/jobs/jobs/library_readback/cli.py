"""``jobs library-readback status | replay``: SMD's two read-only views of the read-back.

* ``status``: one seat's five tests, from every score the daemon wrote: each job
  folder's ``library-score.json`` and the seat's log (``lane.LOG``), which
  outlives the folders. Per test, how many jobs pass, fail, are not applicable
  or not recorded, and the newest job id in each. Job ids only.
* ``replay <job_dir>``: the scorer over a saved job folder, printed as JSON and
  written nowhere. It is how the client ops workspace scores a real job at $0:
  ``--spec``, ``--roster``, ``--drafting-inputs`` and ``--demand-inputs`` point
  at the seat's (or a saved copy of its) kept copies, roster and firm inputs,
  ``--file`` at any other folder that may hold the file a document was made in.

Neither writes anything, calls a model, or reaches the case system or a mailbox.
On a seat: ``operator/bin/library-readback.sh <slug> status``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..daemon import DEFAULT_RUN_DIR, RUN_DIR_ENV
from . import SCORE_NAME, TESTS, VERDICTS
from .lane import LANES, LOG, log_line


def _lines(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Every scored job, newest score per job: the log, then the folders still there."""
    out: dict[str, dict[str, Any]] = {}
    log = run_dir / LOG
    for raw in log.read_text(encoding="utf-8").splitlines() if log.is_file() else []:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("job_id"):
            out[str(row["job_id"])] = row
    for lane in LANES:
        for path in sorted((run_dir / f"{lane}-jobs").glob(f"*/data/{SCORE_NAME}")):
            try:
                score = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            row = log_line(lane, score)
            have = out.get(row["job_id"])
            if have is None or float(have.get("scored_at") or 0) <= float(row["scored_at"]):
                out[row["job_id"]] = row
    return out


def summarize(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per test: counts per verdict and the newest job id per verdict."""
    out: dict[str, Any] = {"jobs": len(rows), "tests": {}}
    for test in TESTS:
        counts = {v: 0 for v in VERDICTS}
        newest: dict[str, tuple[float, str]] = {}
        for jid, row in rows.items():
            v = (row.get("verdicts") or {}).get(test)
            if v not in counts:
                continue
            counts[v] += 1
            at = float(row.get("scored_at") or 0)
            if v not in newest or at >= newest[v][0]:
                newest[v] = (at, jid)
        out["tests"][test] = {"counts": counts, "newest": {v: newest[v][1] for v in sorted(newest)}}
    return out


def _print(summary: dict[str, Any]) -> None:
    print(f"library read-back: {summary['jobs']} scored job(s)")
    for test, row in summary["tests"].items():
        counts = "  ".join(f"{v} {row['counts'][v]}" for v in VERDICTS)
        newest = "  ".join(f"{v}={j}" for v, j in row["newest"].items()) or "none"
        print(f"  {test}: {counts}  newest: {newest}")


def status(args: argparse.Namespace) -> int:
    summary = summarize(_lines(Path(args.run_dir)))
    if args.json:
        print(json.dumps(summary, indent=1, sort_keys=True))
    else:
        _print(summary)
    return 0


def replay(args: argparse.Namespace) -> int:
    from operator_connector_sdk.library import roster as roster_mod

    from .score import score_job

    job = Path(args.job_dir)
    if not (job / "data").is_dir():
        print(f"jobs: {job} has no data folder", file=sys.stderr)
        return 2
    score = score_job(
        job,
        spec_dir=args.spec,
        roster=roster_mod.load(args.roster) if args.roster else roster_mod.Roster(roster_mod.ABSENT),
        drafting_inputs=args.drafting_inputs,
        demand_inputs=args.demand_inputs,
        extra=list(args.file or []),
    )
    if score is None:
        print("jobs: the folder holds no filed draft or demand to score", file=sys.stderr)
        return 1
    print(json.dumps(score, indent=1, sort_keys=True))
    return 3 if score["failed"] else 0


def _run(args: argparse.Namespace) -> int:
    return status(args) if args.action == "status" else replay(args)


def add(sub: Any) -> None:
    p = sub.add_parser("library-readback", help="the read-back of filed firm documents: status, or replay one job")
    p.add_argument("action", choices=["status", "replay"])
    p.add_argument("job_dir", nargs="?", default="", help="replay: a saved job folder")
    p.add_argument("--run-dir", default=os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    p.add_argument("--json", action="store_true", help="status: the summary as JSON")
    p.add_argument("--spec", default=None, help="replay: the spec store holding the kept copies")
    p.add_argument("--roster", default=None, help="replay: the roster's directory (absent when omitted)")
    p.add_argument("--drafting-inputs", default=None)
    p.add_argument("--demand-inputs", default=None)
    p.add_argument("--file", action="append", help="replay: another folder or file to find a base in")
    p.set_defaults(fn=_run)


__all__ = ["add", "replay", "status", "summarize"]
