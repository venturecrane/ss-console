"""SMD's job stop: a running job ends for good, ``stopped_by_smd``, within a minute.

WHY (2026-11-04). A duplicate A&P drafting job kept spending, about a dollar a
minute and $56.64 in all, and nothing could stop it. The only stop was the
Machine's (``stop_request.py``): read only between stages, and it means
"resume me on the next boot". The drafting finish (compose, audit, repair,
gate, render, review) runs for many minutes with no stage boundary inside it,
and there was no verb at all that ended a job.

THE SHAPE. ``python -m jobs.stop <lane> <job_id> --by X --why Y`` (``stop.py``,
root on the seat) writes :data:`MARKER` into the job's dir. From there:

* the CHILD sees it before its next paid model call (``llm.Doorway`` calls
  ``stop_request.check_smd``), at every stage boundary, in a Batch poll, and
  before the drafting lane files, and exits with no verdict;
* the DAEMON's wait (``child.run_child``) sees it within a second, and a child
  still running :func:`grace_seconds` later (a call in flight, a stage with no
  model call) is terminated: SIGTERM to its group, then SIGKILL;
* the lane records it (:func:`finish`, called first in
  ``resume.cut_short_by_stop``, which every lane runs on a child's exit):
  ``failed`` in the broker's ledger with reason ``stopped_by_smd: by <who>:
  <why>`` and the spend the job actually made, and ``stopped_by_smd`` in
  ``daemon.json``, which is in ``daemon.TERMINAL``, so the daemon never
  resumes or requeues it. No DELIVER wake is written: nothing is filed, no
  turn runs, nothing reaches the client. SMD is the one who stopped it.

WHY THE LEDGER SAYS ``failed``. Every lane's ledger already treats ``failed``
as terminal, refuses any client reply for it, and lets only a root
``*_job_resume`` revive it. A new ledger state would be seven schema changes for
the same behaviour; the reason prefix carries the distinction, and the daemon's
own state names it outright.

WHY A FILE THE DAEMON READS, NOT A BROKER VERB. The broker cannot traverse the
job dirs (``resume.py``: ``jobs/`` is root:jobs 0710 and the broker is not
root), and the daemon's lane thread is inside ``run_child`` for as long as the
job runs, so nothing it would poll in the queue is read until the job ends.
Root writes the marker straight into the job dir; only root can (the dir is
the jobs uid's 0700 and the Operator's agent uid has no path to it and no tool).

A MACHINE STOP IS UNCHANGED. ``.stop-request`` and :data:`MARKER` are two
files. A seat restart writes only the first and the job resumes on the next
boot as before. When both are present SMD's wins: the job ends.

AN EXPLICIT RESUME UNDOES IT. ``resume.take_requests`` (SMD's root resume of
the failed row) removes the marker, so a job SMD later chooses to run again
is not stopped by the old request. A seat restart before the lane recorded the
stop does NOT remove it: ``run_child`` refuses to start a child for a job with
the marker, and the job is recorded stopped instead.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import stop_request, verdict as verdict_mod

logger = logging.getLogger("jobs.daemon")

MARKER = stop_request.SMD_NAME
STATE = "stopped_by_smd"
REASON_PREFIX = "stopped_by_smd:"
GRACE_ENV = "SMD_JOB_STOP_GRACE_SECONDS"
#: How long a stopped child gets to reach its own checkpoint before the
#: daemon terminates it. Long enough for an in-flight call to come back (its
#: tokens are billed whether or not we wait for them), short enough that the
#: whole stop is inside a minute with the terminate grace.
DEFAULT_GRACE_SECONDS = 30.0
#: The outcomes that are a job's real ending (``resume.FINISHED_OUTCOMES``):
#: a job that reached one before the stop landed is recorded as itself.
FINISHED = frozenset({"delivered", "dry_run", "held", "refused"})


def grace_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get(GRACE_ENV) or DEFAULT_GRACE_SECONDS))
    except ValueError:
        return DEFAULT_GRACE_SECONDS


def read(job_dir: Path | str) -> dict[str, Any] | None:
    """The stop request in a job dir, or None. An unreadable marker still
    stops the job: its presence is the request."""
    p = Path(job_dir) / MARKER
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"by": "unknown", "why": "unreadable stop request"}
    return doc if isinstance(doc, dict) else {"by": "unknown", "why": "unreadable stop request"}


def request(job_dir: Path | str, *, by: str, why: str, at: float) -> dict[str, Any]:
    """Write the stop request (root). 0644 in the child's own 0700 dir: the
    child reads it, nobody else can reach it."""
    doc = {"by": by.strip()[:120], "why": why.strip()[:300], "at": round(float(at), 3)}
    p = Path(job_dir) / MARKER
    tmp = p.with_name(f".{MARKER}.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(p)
    return doc


def clear(job_dir: Path | str) -> None:
    (Path(job_dir) / MARKER).unlink(missing_ok=True)


def spend_cents(job_dir: Path | str) -> int | None:
    """What the job's paid calls cost, from every usage ledger under its dir,
    priced the way the cap prices them (``budget.Pricing``). None when the
    price table cannot be read: the stop is never held up by a figure."""
    from .budget import PRICING_DEFAULT, PRICING_ENV, Pricing
    from .ledger import read_rows

    try:
        pricing = Pricing.load(Path(os.environ.get(PRICING_ENV) or PRICING_DEFAULT))
        dollars = 0.0
        for path in sorted(Path(job_dir).rglob("usage-ledger.jsonl")):
            for row in read_rows(path):
                dollars += float(pricing.price_row(row))
    except Exception as exc:  # noqa: BLE001 - a missing figure is said, never fatal
        logger.error("could not price the spend of %s: %s", job_dir, exc)
        return None
    return int(round(dollars * 100))


def reason_of(req: dict[str, Any]) -> str:
    return f"{REASON_PREFIX} by {req.get('by') or 'unknown'}: {req.get('why') or 'no reason given'}"[:500]


def finish(d: Any, job_id: str, code: int, out: str) -> bool:
    """True when SMD stopped this job and it is now recorded ``stopped_by_smd``.

    False when there is no stop request, or when the job reached a real ending
    before the stop landed (delivered, held, refused): that work happened and
    is recorded as itself by the lane's own report."""
    jd = d.job_dir(job_id)
    req = read(jd)
    if req is None:
        return False
    outcomes = verdict_mod.read(jd, out)
    if any(
        isinstance(o, dict) and (str(o.get("outcome")) in FINISHED or str(o.get("verdict")) in FINISHED)
        for o in outcomes
    ):
        logger.warning("%s job %s reached its own ending before SMD's stop; recorded as that", d.LANE, job_id)
        return False
    from .broker_client import BrokerError

    cents = spend_cents(jd)
    reason = reason_of(req)
    fields: dict[str, Any] = {"reason": reason}
    if cents is not None:
        fields["cents"] = cents
    recorded = True
    try:
        d.broker.record(job_id, "failed", fields)
    except (BrokerError, ValueError) as exc:
        recorded = False
        logger.error("could not record SMD's stop of %s job %s: %s (the state file keeps it)", d.LANE, job_id, exc)
    d._write_state(
        job_id,
        state=STATE,
        finished_at=d.clock(),
        reason=reason,
        stopped_by=str(req.get("by") or ""),
        stopped_why=str(req.get("why") or ""),
        stopped_exit=code,
        stopped_cents=cents,
        stopped_recorded=recorded,
        wake=None,
        retry_after=None,
    )
    logger.warning("%s job %s stopped by SMD (exit %s); final, never resumed", d.LANE, job_id, code)
    return True


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "GRACE_ENV",
    "MARKER",
    "REASON_PREFIX",
    "STATE",
    "clear",
    "finish",
    "grace_seconds",
    "read",
    "reason_of",
    "request",
    "spend_cents",
]
