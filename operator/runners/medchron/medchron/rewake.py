"""`medchron rewake <job_id> --by WHO --why WHY [--lane LANE] [--again]`: start
one finished job's DELIVER turn again, so the email it owed goes out.

The case it exists for (A&P 201167, 2026-10-20): a negotiation alert's turn
ended with its reply held by the outgoing-email check, nothing was sent, and
the reply watch paged twenty minutes later. Setting the stored wake back to
pending did nothing: ``dispatch_wakes`` posted the same ``handoff_id``
(``<lane>-<job>``) and the gateway answered 202 and dropped it as a duplicate
delivery. The re-send that worked was a hand POST under a new id.

This makes that the supported path. It re-arms the stored, code-authored task
under a re-send number, and the daemon posts it as ``<lane>-<job>-r<n>`` on
its next tick, then watches for the reply exactly as it did the first time
(``proof/watch.py``): a re-send that also goes unsent pages again.

It refuses where a re-send would be wrong: no stored task, a wake still
pending, a reply watch still open, or a reply already proven sent (that one
needs ``--again``, because it would email the requester a second time).

Run as root on the seat: the job dirs are root-owned. The file is rewritten
in place, so its owner and mode are kept.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .lane_proof_cli import DEFAULT_RUN_DIR, JOBS

#: Proof alerts a re-send must be able to raise again within the day.
_RESET_ALERTS = ("reply_unproven:", "wake_lost:")


def handoff_id(lane: str, job_id: str, wake: dict[str, Any]) -> str:
    """The gateway dedupes on this id, so a re-send carries its number."""
    n = int(wake.get("resend") or 0)
    return f"{lane}-{job_id}" + (f"-r{n}" if n > 0 else "")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rewake(
    run_dir: Path, lane: str, job_id: str, by: str, why: str, again: bool, clock: Callable[[], float] = time.time
) -> int:
    p = run_dir / JOBS[lane] / job_id / "daemon.json"
    if not p.is_file():
        print(f"no {lane} job {job_id} on this seat (wiped at 72 h, or never here)")
        return 2
    st = json.loads(p.read_text(encoding="utf-8"))
    wake = st.get("wake")
    if not isinstance(wake, dict) or not wake.get("task"):
        print(f"{job_id} has no stored DELIVER task to start again")
        return 2
    if wake.get("pending"):
        print(f"{job_id}'s wake is already pending; the daemon sends it on its next tick")
        return 1
    w = st.get("reply_watch") if isinstance(st.get("reply_watch"), dict) else {}
    if w and not w.get("done"):
        print(f"{job_id}'s reply watch is still open (due {_iso(float(w.get('due') or 0))}); wait for it to settle")
        return 1
    if w.get("proven_at") and not again:
        print(f"{job_id}'s reply was proven sent at {_iso(float(w['proven_at']))}; --again sends the requester a second")
        return 1
    n = int(wake.get("resend") or 0) + 1
    now = clock()
    wake.update(pending=True, attempts=0, resend=n, sent_at=None, outcome=None)
    st["wake"] = wake
    st.pop("reply_watch", None)
    st["proof_alerts"] = {
        k: v for k, v in (st.get("proof_alerts") or {}).items() if not k.startswith(_RESET_ALERTS)
    }
    st["rewakes"] = [*(st.get("rewakes") or []), {"at": now, "by": by, "why": why[:300], "resend": n}]
    with p.open("r+", encoding="utf-8") as f:
        f.write(json.dumps(st, indent=1))
        f.truncate()
    print(
        f"{lane} {job_id}: DELIVER turn re-armed as {handoff_id(lane, job_id, wake)}; the daemon starts it on its "
        "next tick and pages if the reply is not seen in Sent Items within 20 minutes"
    )
    return 0


def _run(args: Any) -> int:
    if not args.by.strip() or not args.why.strip():
        print("--by and --why are required: who is re-sending, and why")
        return 2
    return rewake(Path(args.run_dir), args.lane, args.job_id, args.by.strip(), args.why.strip(), args.again)


def add(sub: Any) -> None:
    rw = sub.add_parser("rewake", help="start one finished job's DELIVER turn again (a stopped alert or reply)")
    rw.add_argument("job_id", help="the job id (for a negotiation alert, the notice id)")
    rw.add_argument("--lane", choices=sorted(JOBS), default="negotiation")
    rw.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    rw.add_argument("--by", default="", help="who is re-sending")
    rw.add_argument("--why", default="", help="why the first one did not go")
    rw.add_argument("--again", action="store_true", help="re-send even though a reply was proven sent")
    rw.set_defaults(fn=_run)
