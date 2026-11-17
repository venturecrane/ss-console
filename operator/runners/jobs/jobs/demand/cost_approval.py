"""SMD widens one demand job's cost cap, and the job resumes from its estimate.

    python -m jobs.demand.cost_approval approve <job_id> --usd N --by "<name>" [--dry-run]

Root only, on the seat, with the jobs venv's python: the same uid and
environment as the runner daemon, like ``jobs.medchron.allowance``.

WHY (2026-11-06). A demand whose estimate is over the firm's
``per_job_cap_usd`` stops before anything is paid (``run._estimate``), as
``failed`` with the setting's name and the estimate, after the free pull and
preflight have run. ``failed`` is the right ending: it is the one a person can
resume (``demand_job_resume``), and the stages already done are recorded in
``data/state.json``, so a resume skips the free pull and goes straight back
to the estimate. What did not exist was a way to say yes to THIS job: the only
lever was the firm's cap itself, which every later demand would inherit.

``approve`` is that yes, the dollar twin of ``jobs.medchron.allowance approve``.
It writes ``cost-approval.json`` to the job dir (the approved dollars, who and
when), which the runner reads when it opens the job and adds to the firm's
cap FOR THIS JOB ONLY, at the estimate and on every paid call. The monthly
cost budget still binds exactly as the firm set it. Then it asks the broker
for the job's resume, whose ``DEMAND_JOB_RESUME_REQUESTED`` row carries the
reason with the approved amount in cents and who approved.

What it refuses: a job that is not ``failed`` on ``per_job_cap_usd``; a job
already queued to run; an amount that is not a positive number; no author;
a second approval on the same attempt (approve again after the next stop).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from ..limits import CAP_SETTING
from ..resume import MARKER_PREFIX

APPROVAL_FILE = "cost-approval.json"


class Refused(RuntimeError):
    """This approval cannot be recorded; the message says why."""


# ---- the approval file the runner reads ----------------------------------------
def _approvals(job_dir: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((Path(job_dir) / APPROVAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("approvals") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _usd(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and f > 0 else None


def approved_usd(job_dir: Path) -> float:
    """Every dollar SMD approved past the firm's cap for this job, summed. An
    unreadable file, or a row that is not a positive amount, is no approval:
    the cap stays as the firm set it."""
    return round(sum(u for u in (_usd(r.get("usd")) for r in _approvals(job_dir)) if u is not None), 2)


def write_approval(job_dir: Path, *, usd: float, by: str, attempt: int, at: str | None = None) -> Path:
    rows = _approvals(job_dir)
    stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows.append({"usd": round(float(usd), 2), "by": by, "at": stamp, "attempt": int(attempt)})
    p = Path(job_dir) / APPROVAL_FILE
    p.write_text(json.dumps({"approvals": rows}, sort_keys=True), encoding="utf-8")
    return p


# ---- the decision ---------------------------------------------------------------------
def plan_approve(d: Any, job_id: str, *, usd: Any, by: str) -> dict[str, Any]:
    if not by.strip():
        raise Refused("--by is required: who approved")
    amount = _usd(usd)
    if amount is None:
        raise Refused("--usd must be a dollar amount greater than 0")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no demand job {job_id}")
    reason = str(row.get("reason") or "")
    if row.get("state") != "failed" or not reason.startswith(CAP_SETTING):
        raise Refused(f"job {job_id} is {row.get('state')} ({reason[:80]}), not stopped at {CAP_SETTING}")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    attempt = int(d._daemon_state(job_id).get("attempts") or 1)
    if any(int(r.get("attempt") or 0) == attempt for r in _approvals(d.job_dir(job_id))):
        raise Refused(f"job {job_id} already has an approval on attempt {attempt}; nothing was written")
    cents = int(round(amount * 100))
    return {
        "job_id": job_id,
        "decision": {"usd": round(amount, 2), "by": by.strip(), "attempt": attempt},
        "resume": {
            "action": "demand_job_resume",
            "job_id": job_id,
            "reason": f"{CAP_SETTING} widened by SMD for this job only: {cents:,} cents more ({by.strip()})",
        },
    }


def apply_approve(d: Any, p: dict[str, Any]) -> None:
    """The approval file first, then the broker's resume: a marker the daemon
    could claim before the file existed would only stop at the cap again. A
    refused resume takes the approval back out."""
    from ..broker_client import BrokerError

    job_id, dec = p["job_id"], p["decision"]
    path = d.job_dir(job_id) / APPROVAL_FILE
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    write_approval(d.job_dir(job_id), usd=dec["usd"], by=dec["by"], attempt=dec["attempt"])
    d._chown_child(path)
    try:
        d.broker.client._request(p["resume"])
    except (BrokerError, OSError, ValueError) as exc:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, encoding="utf-8")
        raise Refused(f"the broker refused the resume ({exc}); the approval was taken back out") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.demand.cost_approval", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ap = sub.add_parser("approve", help="resume a job stopped at the cost cap, with an approved amount")
    a_ap.add_argument("job_id")
    a_ap.add_argument("--usd", type=float, required=True, help="dollars approved past the firm's cap, this job only")
    a_ap.add_argument("--by", required=True, help="who approved")
    a_ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from .. import daemon as daemon_mod, demand_lane

    d = demand_lane.build_lane(daemon_mod.build())
    if d is None:
        print("REFUSED: this seat has no demand lane", file=sys.stderr)
        return 2
    try:
        p = plan_approve(d, a.job_id, usd=a.usd, by=a.by)
        print(json.dumps({"dry_run": a.dry_run, **p}, indent=1, default=str))
        if a.dry_run:
            return 0
        apply_approve(d, p)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(
        f"approved {p['decision']['usd']:.2f} USD past the cap on {a.job_id}; the daemon re-queues it on its next tick"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
