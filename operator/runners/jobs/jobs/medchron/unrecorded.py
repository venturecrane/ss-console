"""A chronology ending the broker could not take, and its bound.

WHY (2026-11-17, the lifecycle matrix). A job that ended while the broker was
away told the firm "delivered" while its row said running for good, and no
command accepted the row. The lane now keeps such an ending (``record_pending``
in the job's state) and records it on a later tick, and the job's DELIVER wake
waits for that record (``MedchronLane._wakes_pending``).

THE BOUND (second review, the same day). The firm being told its document is
there outranks our bookkeeping, so a delivered wake is never held past it:

* the broker ANSWERED and refused the record (``BrokerRefused``: an illegal
  move after someone else moved the row, a field it will not take): retrying
  changes nothing, so SMD is paged at once and the wake is released;
* the broker could not be REACHED: the record is retried once a minute, up to
  :data:`MAX_ATTEMPTS` attempts and at most :data:`MAX_SECONDS` after the
  ending, whichever comes first; at the bound SMD is paged and the wake is
  released, and the record goes on being tried until the broker takes it.

Either way the job's state keeps ``ending_unrecorded`` (the cause), a
``record_gave_up`` run block is noted on the row when the broker takes one (so
the run grades aided and work-health names it), and the page is retried each
poll until it lands (a page during an outage cannot be written either).
"""

from __future__ import annotations

import logging
from typing import Any

from .. import run_record, verdict as verdict_mod
from ..broker_client import BrokerError, BrokerRefused
from ..proof import watch as proof_watch

logger = logging.getLogger("jobs.daemon")

#: The page SMD gets (``proof/gate.CODES``).
CODE = "ending_unrecorded"
#: How long the lane waits between tries while the broker cannot be reached.
RETRY_SECONDS = 60.0
#: The outage bound: this many tries, and never longer than this after the ending.
MAX_ATTEMPTS = 10
MAX_SECONDS = 30 * 60.0
REFUSED, OUTAGE = "refused", "outage"


def hold(d: Any, job_id: str, state: str, fields: dict[str, Any], *, proof: bool) -> None:
    """Keep an ending the broker could not be reached for; its wake waits."""
    d._write_state(
        job_id,
        record_pending={"state": state, "fields": fields, "proof": proof, "attempts": 1, "first_at": d.clock()},
    )


def give_up(d: Any, job_id: str, state: str, cause: str, why: str, *, keep: dict[str, Any] | None = None) -> None:
    """Release the wake, note the run, and page SMD (retried until it lands).
    ``keep``: an outage's held ending, still retried (``released``: its wake no
    longer waits for it) until the broker takes it; a refusal keeps nothing."""
    pending = {**keep, "released": True} if keep is not None else None
    d._write_state(job_id, record_pending=pending, ending_unrecorded={"cause": cause, "state": state, "paged": False})
    logger.error(
        "medchron job %s: its %s ending is not recorded (%s: %s); its wake goes now", job_id, state, cause, why
    )
    try:
        row = d.broker.status(job_id) or {}
        if row.get("state"):
            d.broker.record(job_id, str(row["state"]), {"run": run_record.block("record_gave_up", code=cause)})
    except (BrokerError, ValueError) as exc:
        logger.warning("could not note the unrecorded ending of %s: %s", job_id, exc)
    page(d, job_id, why)


def holds_wake(st: dict[str, Any]) -> bool:
    """Whether the job's wake still waits for its ending's record."""
    pending = st.get("record_pending")
    return isinstance(pending, dict) and not pending.get("released")


def page(d: Any, job_id: str, why: str = "") -> None:
    st = d._daemon_state(job_id).get("ending_unrecorded") or {}
    if st.get("paged"):
        return
    detail = (
        f"the {st.get('state')} ending could not be recorded ({st.get('cause')}"
        f"{': ' + why if why else ''}); the firm's message went anyway; the job's row is wrong until SMD records it"
    )
    if proof_watch.alert(d, job_id, CODE, detail[:400]):
        d._write_state(job_id, ending_unrecorded={**st, "paged": True})


def retry(d: Any) -> bool:
    """One pass over the held endings; False while one still waits on an outage
    (or a page still waits to be written), so the caller backs off."""
    ok = True
    if not d.jobs.is_dir():
        return ok
    for jd in sorted(d.jobs.iterdir()):
        st = d._daemon_state(jd.name)
        if isinstance(st.get("ending_unrecorded"), dict) and not st["ending_unrecorded"].get("paged"):
            page(d, jd.name)
            ok = ok and bool((d._daemon_state(jd.name).get("ending_unrecorded") or {}).get("paged"))
        pending = st.get("record_pending")
        if not isinstance(pending, dict):
            continue
        try:
            d.broker.record(jd.name, pending["state"], pending["fields"])
        except BrokerRefused as exc:
            give_up(d, jd.name, pending["state"], REFUSED, str(exc))
            continue
        except BrokerError as exc:
            tries = int(pending.get("attempts") or 1) + 1
            late = d.clock() - float(pending.get("first_at") or d.clock()) >= MAX_SECONDS
            if not pending.get("released") and (tries >= MAX_ATTEMPTS or late):
                give_up(
                    d, jd.name, pending["state"], OUTAGE, f"{tries} tries: {exc}", keep={**pending, "attempts": tries}
                )
                ok = False
                continue
            logger.warning("still could not record %s for %s (try %d): %s", pending["state"], jd.name, tries, exc)
            d._write_state(jd.name, record_pending={**pending, "attempts": tries})
            ok = False
            continue
        if pending.get("proof"):
            proof_watch.record_proven(d, jd.name, verdict_mod.read(jd, ""))
        d._write_state(jd.name, record_pending=None)
    return ok


__all__ = ["CODE", "MAX_ATTEMPTS", "MAX_SECONDS", "RETRY_SECONDS", "give_up", "hold", "holds_wake", "page", "retry"]
