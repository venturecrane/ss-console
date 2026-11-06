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

A JOB THAT NEVER STARTED (2026-11-06). A&P memo job 01M6PNY4 sat submitted in
the drafting queue, held ``lane_unproven``, after its ask had been answered
by hand. Nothing ended it: the stop refused a job with no job dir, and
``claim_next`` never read the ledger row, so marking the row failed would not
have stopped it running the day the memo lane was proven. Two rules close it:

* :func:`stop_queued` (the CLI, root) moves a queued envelope into its job dir
  with the marker and an unclaimed state, in that order, so a claim racing it
  still finds the marker; :func:`take_unclaimed` (the lane, every second, idle
  or beside a running child) records every stopped job that is not the one
  running: ``failed`` in the ledger, ``stopped_by_smd`` in ``daemon.json``, its
  wake dropped and its lane hold set aside beside it. A parked or requeued job
  is ended the same way: none of them is running, none may start again.
* :func:`stale_envelope` (``claim_next``, before anything may claim or release
  it) refuses an envelope whose ledger row has already ended, or whose job dir
  carries this marker, and sets it aside. A failed row SMD resumed is the one
  exception: ``resume.take_requests`` re-queues it while the row still says
  ``failed`` (the broker moves the row when it runs).
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
    recorded = True
    try:
        d.broker.record(job_id, "failed", _fields(req, cents))
    except (BrokerError, ValueError) as exc:
        recorded = False
        logger.error("could not record SMD's stop of %s job %s: %s (the state file keeps it)", d.LANE, job_id, exc)
    _write_stopped(d, job_id, req, code=code, cents=cents, recorded=recorded)
    logger.warning("%s job %s stopped by SMD (exit %s); final, never resumed", d.LANE, job_id, code)
    return True


def _fields(req: dict[str, Any], cents: int | None) -> dict[str, Any]:
    fields: dict[str, Any] = {"reason": reason_of(req)}
    if cents is not None:
        fields["cents"] = cents
    return fields


def _write_stopped(
    d: Any, job_id: str, req: dict[str, Any], *, code: int | None, cents: int | None, recorded: bool
) -> None:
    d._write_state(
        job_id,
        state=STATE,
        finished_at=d.clock(),
        reason=reason_of(req),
        stopped_by=str(req.get("by") or ""),
        stopped_why=str(req.get("why") or ""),
        stopped_exit=code,
        stopped_cents=cents,
        stopped_recorded=recorded,
        wake=None,
        retry_after=None,
    )


# ---------------------------------------------------------------------------
# A job that is not running: queued, held for a proof, parked, requeued
# ---------------------------------------------------------------------------

#: The envelope's name in a job dir (``Daemon.claim_next``).
ENVELOPE = "envelope.json"
#: Where a lane hold is kept, in the job dir, once its job ended without running.
HOLD_KEPT = "lane-hold.json"
#: Where ``claim_next`` sets aside an envelope it refuses, inside the queue
#: (and inside ``lane-holds/`` for its hold).
ENDED_DIR = "ended"
#: The ledger's terminal states (every ``*_ledger.TERMINAL``).
LEDGER_ENDED = frozenset({"delivered", "failed"})
#: How long a stop the broker could not record waits before the next try.
RETRY_SECONDS = 30.0


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)


def stop_queued(queue: Path | str, job_dir: Path | str, *, by: str, why: str, at: float) -> bool:
    """Root's stop of a job still in its lane's queue: True when its envelope
    was there. The marker is written FIRST, then an unclaimed state, then the
    envelope moves (one rename): a claim racing this finds the marker in the
    job dir, and ``run_child`` refuses to start it. Idempotent: a second call
    keeps the first request and finds no envelope to move."""
    jd = Path(job_dir)
    env = Path(queue) / f"{jd.name}.json"
    if not env.is_file():
        return False
    jd.mkdir(parents=True, exist_ok=True, mode=0o700)
    if read(jd) is None:
        request(jd, by=by, why=why, at=at)
    st_path = jd / "daemon.json"
    try:
        st = json.loads(st_path.read_text(encoding="utf-8")) if st_path.is_file() else {}
    except (OSError, ValueError):
        st = {}
    st = st if isinstance(st, dict) else {}
    # Unclaimed and not ended, so the lane's sweep records it whatever the job
    # was before (a resumed failure is re-queued with its old state).
    if st.get("state") != STATE:  # an ended stop stays ended
        st.update(claimed=False, state="submitted", stop_queued_at=round(float(at), 3))
    _write_json(st_path, st)
    try:
        env.replace(jd / ENVELOPE)
    except FileNotFoundError:
        pass  # claimed a moment ago: the marker is already in its job dir
    return True


def _ended_states() -> frozenset[str]:
    from .daemon import TERMINAL  # imported here: the daemon imports this module through child.py

    return TERMINAL


def take_unclaimed(d: Any, *, running: str | None = None) -> list[str]:
    """Record every job SMD stopped that is not running now. Never raises: it
    is called once a second from the lane's idle wait and from a running
    child's. A job the broker could not record is tried again after
    :data:`RETRY_SECONDS`; it cannot run meanwhile (its envelope is out of the
    queue, ``claim_next`` refuses its dir, and ``run_child`` refuses to start it)."""
    try:
        jobs = Path(d.jobs)
        if not jobs.is_dir():
            return []
        ended = _ended_states()
        done: list[str] = []
        for jd in sorted(jobs.iterdir()):
            if jd.name == running or not (jd / MARKER).is_file():
                continue
            st = d._daemon_state(jd.name)
            if st.get("state") in ended or float(st.get("stop_retry_after") or 0) > d.clock():
                continue
            if _record_unclaimed(d, jd.name, st):
                done.append(jd.name)
        return done
    except Exception as exc:  # noqa: BLE001 - a sweep that raised would end the lane's wait loop
        logger.error("%s lane: SMD stop sweep failed: %s", getattr(d, "LANE", "?"), exc)
        return []


def _record_unclaimed(d: Any, job_id: str, st: dict[str, Any]) -> bool:
    from . import lane_proof
    from .broker_client import BrokerError

    jd = d.job_dir(job_id)
    req = read(jd) or {"by": "unknown", "why": "unreadable stop request"}
    cents = spend_cents(jd)
    try:
        d.broker.record(job_id, "failed", _fields(req, cents))
        recorded = True
    except (BrokerError, ValueError) as exc:
        try:
            row = d.broker.status(job_id)
        except (BrokerError, ValueError):
            row = None
        if row is None or str(row.get("state") or "") not in LEDGER_ENDED:
            logger.error("could not record SMD's stop of %s job %s: %s; trying again", d.LANE, job_id, exc)
            d._write_state(job_id, stop_retry_after=d.clock() + RETRY_SECONDS)
            return False
        # The row already ended (delivered, or failed and refusing a note):
        # there is nothing left to record, only the state to close.
        recorded = False
        logger.warning("%s job %s: its ledger row is already %s; closed here", d.LANE, job_id, row.get("state"))
    _write_stopped(d, job_id, req, code=None, cents=cents, recorded=recorded)
    lane_proof.set_aside_hold(d, job_id, jd / HOLD_KEPT)
    logger.warning(
        "%s job %s stopped by SMD while not running (was %s); final, never claimed",
        d.LANE,
        job_id,
        st.get("state") or "queued",
    )
    return True


def stale_envelope(d: Any, job_id: str, row: dict[str, Any]) -> str | None:
    """Why ``claim_next`` must not claim this queued envelope, or None.

    An envelope runs only for a ledger row that has not ended, in a job dir
    that carries no stop. The one exception is SMD's own resume: it re-queues
    a failed job while the row still says ``failed``, after clearing the stop
    and ``finished_at`` (``resume.take_requests``)."""
    jd = d.job_dir(job_id)
    if read(jd) is not None:
        return "SMD stopped it"
    state = str(row.get("state") or "")
    if state not in LEDGER_ENDED:
        return None
    st = d._daemon_state(job_id) if jd.is_dir() else {}
    resumed = state == "failed" and int(st.get("resumes") or 0) > 0 and st.get("finished_at") is None
    if resumed and st.get("state") != STATE:
        return None
    return f"its ledger row is {state}"


def set_aside(d: Any, env_path: Path, job_id: str, why: str) -> None:
    """Move a refused envelope and its lane hold out of the way, logged in one line."""
    from . import lane_proof

    aside = Path(env_path).parent / ENDED_DIR
    aside.mkdir(exist_ok=True, mode=0o700)
    from . import retention

    Path(env_path).replace(aside / Path(env_path).name)
    retention.mark_set_aside(aside / Path(env_path).name)
    holds_aside = Path(d.run_dir) / lane_proof.HOLDS_DIR / ENDED_DIR / f"{d.LANE}-{job_id}.json"
    if lane_proof.set_aside_hold(d, job_id, holds_aside):
        retention.mark_set_aside(holds_aside)
    logger.warning("%s job %s not claimed: %s; its envelope is set aside in %s", d.LANE, job_id, why, aside)


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "ENDED_DIR",
    "ENVELOPE",
    "GRACE_ENV",
    "HOLD_KEPT",
    "LEDGER_ENDED",
    "MARKER",
    "REASON_PREFIX",
    "STATE",
    "clear",
    "finish",
    "grace_seconds",
    "read",
    "reason_of",
    "request",
    "set_aside",
    "spend_cents",
    "stale_envelope",
    "stop_queued",
    "take_unclaimed",
]
