"""``python -m jobs.stop <lane> <job_id> --by WHO --why WHY``: SMD stops a running job.

Root on the seat only (``fly ssh console`` / seat-probe as root). The job ends
``stopped_by_smd`` within about a minute: no further model call, nothing filed,
no DELIVER wake, and the daemon never resumes it (``smd_stop.py`` has the
whole design). Prints the job's state and spend before, writes the request,
waits for the lane to record it, and prints the state and spend after.

Exit codes: 0 stopped (or already stopped); 1 refused (not root, unknown lane,
no such job, the job already ended); 3 the request is written but the lane has
not recorded the stop within ``--wait`` seconds (the request stays: the lane
records it when its child exits, and never starts the job again).

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

from . import smd_stop
from .daemon import DEFAULT_RUN_DIR, RUN_DIR_ENV, TERMINAL

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


def _say(label: str, jd: Path, st: dict) -> None:
    print(
        f"{label}: state={st.get('state') or 'unknown'} attempts={st.get('attempts', 0)} "
        f"spend={_spend(smd_stop.spend_cents(jd))}"
    )


def main(
    argv: list[str] | None = None,
    *,
    euid: Callable[[], int] = os.geteuid,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    p = argparse.ArgumentParser(prog="python -m jobs.stop", description="SMD stops a running job for good.")
    p.add_argument("lane", choices=sorted(LANES))
    p.add_argument("job_id")
    p.add_argument("--by", required=True, help="who at SMD is stopping it")
    p.add_argument("--why", required=True, help="why, in a sentence (kept in the ledger reason)")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS, help="seconds to wait for the lane to record it")
    p.add_argument("--run-dir", default=None, help=f"default: ${RUN_DIR_ENV} or {DEFAULT_RUN_DIR}")
    args = p.parse_args(argv)
    if euid() != 0:
        print("refused: only root on the seat stops a job (never the Operator)")
        return 1
    if not args.by.strip() or not args.why.strip():
        print("refused: --by and --why must say who and why")
        return 1
    if "/" in args.job_id or args.job_id.startswith("."):
        print(f"refused: {args.job_id!r} is not a job id")
        return 1
    run_dir = Path(args.run_dir or os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    jobs_name, pid_name = LANES[args.lane]
    jd = run_dir / jobs_name / args.job_id
    if not jd.is_dir():
        print(f"refused: no {args.lane} job dir {jd} (a job still in the queue has not started; stop it once claimed)")
        return 1
    before = _state(jd)
    _say("before", jd, before)
    if before.get("state") == smd_stop.STATE:
        print(f"already stopped by {before.get('stopped_by')}: {before.get('stopped_why')}")
        return 0
    if before.get("state") in TERMINAL:
        print(f"refused: the job already ended {before.get('state')}; there is nothing to stop")
        return 1
    pid_file = run_dir / pid_name
    running = pid_file.is_file()
    req = smd_stop.request(jd, by=args.by, why=args.why, at=clock())
    print(
        f"stop requested by {req['by']}: {req['why']} "
        f"({'a child is running' if running else 'no child running now; the lane records it when it reaches the job'})"
    )
    waited = 0.0
    after = _state(jd)
    while after.get("state") != smd_stop.STATE and waited < args.wait:
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        after = _state(jd)
    if after.get("state") != smd_stop.STATE:
        _say("after", jd, after)
        print(
            f"not yet recorded after {args.wait:.0f}s; the request stays and the job will never start again. "
            "Run this again to read it back."
        )
        return 3
    print(
        f"after: state={after.get('state')} by={after.get('stopped_by')} why={after.get('stopped_why')} "
        f"spend={_spend(after.get('stopped_cents'))} ledger_recorded={bool(after.get('stopped_recorded'))} "
        f"child_exit={after.get('stopped_exit')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
