"""A chronology ending the broker did not take: one path, with a bound.

WHY (2026-11-17, the lifecycle matrix). A job that ended while the broker was
away told the firm "delivered" while its row said running for good, and no
command accepted the row: a new request for the same work was refused as
"already running" (``MedchronLedger.active_duplicate``).

ONE PATH, whatever the broker said (it was away, or it answered an error such
as a locked database). The ending is kept (``record_pending`` in the job's
state) and recorded from ``poll_requests`` once a minute for as long as the
workdir lives. The firm's message never waits for it: the DELIVER wake is
written and sent exactly as before. Past the bound (:data:`MAX_ATTEMPTS`
tries, or :data:`MAX_SECONDS` after the ending, whichever comes first) SMD is
paged once (``paged`` is kept with the record, so a restart does not page
again) and the record is still tried, so a broker that comes back, or that SMD
repairs, lands it. A record that lands after that page notes ``record_late`` on
the row, so the run grades aided. One that lands inside the bound is no page
and not aided.

AT THE WIPE (``at_wipe``, the review of #4110): the record is tried once more.
If it still does not land, SMD is paged once with the job, its true ending
(delivered, held or failed) and the fact that its ledger row still says
running, and the workdir is wiped on schedule with the pending ending in it:
nothing is kept outside the workdir, nothing is retried after the wipe, and no
row is ever closed with an ending that did not happen. A page that cannot be
written is logged and the workdir is wiped anyway: the wipe is the promise. The
known limit: such a row stays running, refusing the same work as already
running, until SMD corrects it by hand from the page.

Every page is composed from what is true of the job: its ending, whether a
DELIVER wake was owed for it (``proof.watch.wake_owed``), and whether it went.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import run_record, verdict as verdict_mod
from ..broker_client import BrokerError
from ..proof import watch as proof_watch

logger = logging.getLogger("jobs.daemon")

#: The page SMD gets (``proof/gate.CODES``).
CODE = "ending_unrecorded"
#: How long the lane waits between tries.
RETRY_SECONDS = 60.0
#: The bound: this many tries, or this long after the ending, whichever first.
MAX_ATTEMPTS = 10
MAX_SECONDS = 30 * 60.0


def keep(d: Any, job_id: str, state: str, fields: dict[str, Any], *, proof: bool, why: str) -> None:
    """Keep an ending the broker did not take; ``poll_requests`` records it.
    ``proof``: its delivery proof did not land either."""
    pending = {"state": state, "fields": fields, "proof": proof, "attempts": 1, "first_at": d.clock()}
    d._write_state(job_id, record_pending={**pending, "error": why[:120]})


def wake_said(d: Any, job_id: str, state: str, reason: Any) -> str:
    """Whether a DELIVER wake was owed for this ending, and whether it went."""
    if not proof_watch.wake_owed(d, state, reason):
        return f"no wake is owed for a {state} chronology and none was sent (the firm hears nothing of it)"
    what = (
        "the firm is told it was delivered"
        if state == "delivered"
        else "the work is rerouted; the firm is sent nothing"
    )
    wake = d._daemon_state(job_id).get("wake") or {}
    if wake.get("sent_at"):
        return f"its wake was owed and went ({what})"
    if wake.get("pending"):
        return f"its wake is owed and has not gone yet ({what} when it does)"
    if wake:
        return f"its wake was owed and did not go ({str(wake.get('outcome') or 'no outcome')[:60]})"
    return "its wake was owed and none was written"


def detail(d: Any, job_id: str, pending: dict[str, Any], *, wiped: bool = False) -> str:
    """The page's sentence, from the job's facts."""
    state = str(pending.get("state"))
    said = wake_said(d, job_id, state, (pending.get("fields") or {}).get("reason"))
    err = str(pending.get("error") or "no reply")
    # The broker's own code, when it answered (``broker refused: <code>: ...``).
    err = err.split(": ")[1] if err.startswith("broker refused: ") and err.count(": ") >= 2 else err[:60]
    if wiped:
        return (
            f"true ending {state}; its ledger row still says running and is not retried after this wipe ({err}); "
            f"{said}; correct the row by hand to {state}; until then the same work is refused as already "
            f"running as job {job_id}"
        )
    mins = int((d.clock() - float(pending.get("first_at") or d.clock())) // 60)
    refused = f"the same work is refused as already running as job {job_id} until it lands"
    return f"its {state} ending is not recorded after {pending.get('attempts')} tries in {mins} min ({err}); {said}; {refused}"


def _land(d: Any, job_id: str, pending: dict[str, Any]) -> bool:
    """One try. True when the record landed (and the pending ending is gone)."""
    try:
        d.broker.record(job_id, pending["state"], pending["fields"])
    except BrokerError as exc:
        tries = int(pending.get("attempts") or 1) + 1
        pending = {**pending, "attempts": tries, "error": (str(exc) or type(exc).__name__)[:120]}
        d._write_state(job_id, record_pending=pending)
        late = d.clock() - float(pending.get("first_at") or d.clock()) >= MAX_SECONDS
        if (tries >= MAX_ATTEMPTS or late) and not pending.get("paged"):
            logger.error("medchron job %s: its %s ending is still not recorded (%s)", job_id, pending["state"], exc)
            if proof_watch.alert(d, job_id, CODE, detail(d, job_id, pending), unit=f"bound:{job_id}"):
                d._write_state(job_id, record_pending={**pending, "paged": True})
        return False
    if pending.get("proof"):
        proof_watch.record_proven(d, job_id, verdict_mod.read(d.job_dir(job_id), ""))
    d._write_state(job_id, record_pending=None)
    if pending.get("paged"):
        # SMD was paged for it: the run needed someone (work_health_runs).
        try:
            d.broker.record(job_id, pending["state"], {"run": run_record.block("record_late")})
        except BrokerError as exc:
            logger.warning("could not note the late record of %s: %s", job_id, exc)
    return True


def retry(d: Any) -> bool:
    """One pass over the kept endings; False while one still waits."""
    ok = True
    for jd in sorted(d.jobs.iterdir()) if d.jobs.is_dir() else []:
        pending = d._daemon_state(jd.name).get("record_pending")
        if isinstance(pending, dict) and not _land(d, jd.name, pending):
            ok = False
    return ok


def at_wipe(d: Any, job_id: str) -> None:
    """Before a workdir goes: an ending still not recorded is tried once more,
    then paged once with its true ending. The workdir goes either way."""
    pending = d._daemon_state(job_id).get("record_pending")
    if not isinstance(pending, dict) or _land(d, job_id, pending):
        return
    pending = d._daemon_state(job_id).get("record_pending") or pending
    if not proof_watch.alert(d, job_id, CODE, detail(d, job_id, pending, wiped=True), unit=f"wiped:{job_id}"):
        logger.error(
            "medchron job %s: wiping it with its %s ending unrecorded, and SMD could not be paged; "
            "its ledger row says running until it is corrected by hand",
            job_id,
            pending.get("state"),
        )


__all__ = ["CODE", "MAX_ATTEMPTS", "MAX_SECONDS", "RETRY_SECONDS", "at_wipe", "detail", "keep", "retry", "wake_said"]
