"""``python -m jobs.stop <lane> <job_id> --by WHO --why WHY``: SMD ends a job, in any state.

Root on the seat only (``fly ssh console`` / seat-probe as root). The job ends
``stopped_by_smd``: no further model call, nothing filed, no DELIVER wake, and
the daemon never runs or resumes it (``smd_stop.py`` has the whole design).

* RUNNING: the request lands in the job dir; the child stops within a minute.
* QUEUED or HELD for a lane proof (no job dir yet): its envelope moves into a
  job dir beside the request, so no claim can run it, and the lane records it
  ``failed`` / ``stopped_by_smd`` within a second or two and sets its lane
  hold aside (2026-11-06, A&P memo job 01M6PNY4, which nothing could end).
* PARKED, PAUSED or waiting to retry (claimed, not running): recorded the same way.
* FAILED: already ended; a pending resume request for it is withdrawn, so it
  never runs again unless SMD asks again.

Prints the job's state and spend before, writes the request, waits for the
lane to record it, and prints the state and spend after. Running it twice is
safe: the second run reads the stop back.

Exit codes: 0 stopped (or already stopped, or already failed); 1 refused (not
root, unknown lane, no such job, the job was delivered); 3 the request is
written but the lane has not recorded the stop within ``--wait`` seconds (the
request stays: the lane records it, and never starts the job again).

NEVER AN OPERATOR ACT. There is no broker verb and no agent tool for this. The
job dirs are root:jobs and the jobs uid's 0700; the Operator's agent uid has no
path to them, and this command refuses any caller that is not uid 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from . import resume as resume_mod, smd_stop, spend
from .daemon import DEFAULT_RUN_DIR, RUN_DIR_ENV, TERMINAL
from .lane_proof_cli import QUEUES

#: Each lane's job dirs and child pid file under the run dir (each lane's
#: ``jobs`` property and ``run_child`` pidfile; the chronology's predate the
#: other lanes and carry no prefix).
LANES: dict[str, tuple[str, str]] = {
    "medchron": ("jobs", "child.pid"),
    "demand": ("demand-jobs", "demand-child.pid"),
    "drafting": ("drafting-jobs", "drafting-child.pid"),
    "litigation": ("litigation-jobs", "litigation-child.pid"),
    "negotiation": ("negotiation-jobs", "negotiation-child.pid"),
    "casefile": ("casefile-jobs", "casefile-child.pid"),
    "records": ("records-jobs", "records-child.pid"),
}
DEFAULT_WAIT_SECONDS = 90.0
POLL_SECONDS = 1.0


def _state(jd: Path) -> dict:
    p = jd / "daemon.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        return {}


def _spend(cents: int | None) -> str:
    return "unknown (price table unreadable)" if cents is None else f"${cents / 100:.2f}"


def _say(label: str, lane: str, jd: Path, st: dict) -> None:
    print(
        f"{label}: state={st.get('state') or 'unknown'} attempts={st.get('attempts', 0)} "
        f"spend={_spend(spend.priced_cents(jd, lane))}"
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m jobs.stop", description="SMD stops a job for good, in any state.")
    p.add_argument("lane", choices=sorted(LANES))
    p.add_argument("job_id")
    p.add_argument("--by", required=True, help="who at SMD is stopping it")
    p.add_argument("--why", required=True, help="why, in a sentence (kept in the ledger reason)")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS, help="seconds to wait for the lane to record it")
    p.add_argument("--run-dir", default=None, help=f"default: ${RUN_DIR_ENV} or {DEFAULT_RUN_DIR}")
    return p.parse_args(argv)


def _refusal(args: argparse.Namespace, euid: Callable[[], int]) -> str | None:
    if euid() != 0:
        return "refused: only root on the seat stops a job (never the Operator)"
    if not args.by.strip() or not args.why.strip():
        return "refused: --by and --why must say who and why"
    if "/" in args.job_id or args.job_id.startswith("."):
        return f"refused: {args.job_id!r} is not a job id"
    return None


def _stop_queued(args: argparse.Namespace, queue: Path, jd: Path, at: float) -> None:
    print(f"before: queued in {queue} (job dir state={_state(jd).get('state') or 'none'})")
    smd_stop.stop_queued(queue, jd, by=args.by, why=args.why, at=at)
    req = smd_stop.read(jd) or {}
    print(f"stop requested by {req.get('by')}: {req.get('why')} (the envelope is out of the queue; nothing claims it)")


def _stop_claimed(args: argparse.Namespace, queue: Path, jd: Path, running: bool, at: float) -> int | None:
    """The stop of a job with a job dir. An exit code when it is decided here
    (already stopped, failed, delivered); None when the lane must record it."""
    before = _state(jd)
    _say("before", args.lane, jd, before)
    if before.get("state") == smd_stop.STATE:
        print(f"already stopped by {before.get('stopped_by')}: {before.get('stopped_why')}")
        return 0
    if before.get("state") == "failed":
        resume = queue / f"{resume_mod.MARKER_PREFIX}{args.job_id}.json"
        if resume.is_file():
            resume.unlink()
            print(f"withdrew the pending resume request {resume.name}")
        print("the job already ended failed; it never runs again unless SMD resumes it")
        return 0
    if before.get("state") in TERMINAL:
        print(f"refused: the job already ended {before.get('state')}; there is nothing to stop")
        return 1
    req = smd_stop.read(jd) or smd_stop.request(jd, by=args.by, why=args.why, at=at)
    print(
        f"stop requested by {req['by']}: {req['why']} "
        f"({'a child is running' if running else 'no child running now; the lane records it within seconds'})"
    )
    return None


def _read_back(lane: str, jd: Path, wait: float, sleep: Callable[[float], None]) -> int:
    waited = 0.0
    after = _state(jd)
    while after.get("state") != smd_stop.STATE and waited < wait:
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        after = _state(jd)
    if after.get("state") != smd_stop.STATE:
        _say("after", lane, jd, after)
        print(
            f"not yet recorded after {wait:.0f}s; the request stays and the job will never start again. "
            "Run this again to read it back."
        )
        return 3
    print(
        f"after: state={after.get('state')} by={after.get('stopped_by')} why={after.get('stopped_why')} "
        f"spend={_spend(after.get('stopped_cents'))} ledger_recorded={bool(after.get('stopped_recorded'))} "
        f"child_exit={after.get('stopped_exit')}"
    )
    return 0


def main(
    argv: list[str] | None = None,
    *,
    euid: Callable[[], int] = os.geteuid,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parse(argv)
    refused = _refusal(args, euid)
    if refused:
        print(refused)
        return 1
    run_dir = Path(args.run_dir or os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    jobs_name, pid_name = LANES[args.lane]
    jd = run_dir / jobs_name / args.job_id
    queue = run_dir / QUEUES[args.lane]
    queued = (queue / f"{args.job_id}.json").is_file()
    if not jd.is_dir() and not queued:
        print(f"refused: no {args.lane} job {args.job_id}: not in {queue}, no job dir {jd}")
        return 1
    if queued:
        _stop_queued(args, queue, jd, clock())
    else:
        decided = _stop_claimed(args, queue, jd, (run_dir / pid_name).is_file(), clock())
        if decided is not None:
            return decided
    return _read_back(args.lane, jd, args.wait, sleep)


if __name__ == "__main__":
    sys.exit(main())
