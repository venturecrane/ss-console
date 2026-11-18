"""A chronology ending the broker did not take: one path, with a bound.

WHY (2026-11-17, the lifecycle matrix). A job that ended while the broker was
away told the firm "delivered" while its row said running for good, and no
command accepted the row: a new request for the same work was refused as
"already running" (``MedchronLedger.active_duplicate``).

ONE PATH, whatever the broker said (it was away, or it answered an error such
as a locked database). The ending is kept (``record_pending`` in the job's
state) and recorded from ``poll_requests`` once a minute until the broker
takes it. The firm's message never waits for it: the DELIVER wake is written
and sent exactly as before. Past the bound (:data:`MAX_ATTEMPTS` tries, or
:data:`MAX_SECONDS` after the ending, whichever comes first) SMD is paged once
(``paged`` is kept with the record, so a restart does not page again) and the
record is still tried, so a broker that comes back, or that SMD repairs, lands
it. A record that lands after that page notes ``record_late`` on the row, so
the run grades aided. One that lands inside the bound is no page and not aided.

THE WIPE NEVER LOSES IT, AND NOTHING EVER RECORDS AN ENDING THAT DID NOT HAPPEN
(the review of #4110, 2026-11-17). The job's state lives in its workdir, which
the wipe removes, so ``at_wipe`` moves the pending ending to :func:`store`
(``<run dir>/medchron-unrecorded/<job id>.json``) first, with the page SMD gets
once for it. What moves is exactly what the ledger row will hold when it lands:
the state, the counts, the folder and file ids, file names and hashes, the
covered document ids, the reason, and the delivery proof's request (file ids,
sizes and hashes). No record, page text or envelope moves. The lane retries it
from there on the same minute loop until the broker takes the TRUE ending; the
file goes when it lands. Until then the row says running and the same work is
refused as already running, which is true to the ledger, and the page says so.

Every page is composed from what is true of the job: its ending, whether a
DELIVER wake was owed for it (``proof.watch.wake_owed``), and whether it went.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .. import run_record
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
#: Where a pending ending waits once its workdir is wiped (under the run dir).
STORE = "medchron-unrecorded"


def store(d: Any) -> Path:
    return Path(d.run_dir) / STORE


def keep(d: Any, job_id: str, state: str, fields: dict[str, Any], *, proof: dict[str, Any] | None, why: str) -> None:
    """Keep an ending the broker did not take; ``poll_requests`` records it.
    ``proof``: the delivery proof's request, when it has not landed either."""
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
    said = pending.get("said") or wake_said(d, job_id, state, (pending.get("fields") or {}).get("reason"))
    err = str(pending.get("error") or "no reply")
    # The broker's own code, when it answered (``broker refused: <code>: ...``).
    err = err.split(": ")[1] if err.startswith("broker refused: ") and err.count(": ") >= 2 else err[:60]
    refused = f"the same work is refused as already running as job {job_id} until it lands"
    if wiped:
        return (
            f"its workdir is wiped on schedule with its {state} ending still not recorded ({err}); {said}; "
            f"it is kept and retried each minute; {refused}"
        )
    mins = int((d.clock() - float(pending.get("first_at") or d.clock())) // 60)
    return f"its {state} ending is not recorded after {pending.get('attempts')} tries in {mins} min ({err}); {said}; {refused}"


def _save(d: Any, job_id: str, pending: dict[str, Any] | None, *, wiped: bool) -> None:
    if not wiped:
        d._write_state(job_id, record_pending=pending)
        return
    path = store(d) / f"{job_id}.json"
    if pending is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pending, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _land(d: Any, job_id: str, pending: dict[str, Any], *, wiped: bool = False) -> bool:
    """One try. True when the record landed (and the pending ending is gone)."""
    try:
        d.broker.record(job_id, pending["state"], pending["fields"])
    except BrokerError as exc:
        tries = int(pending.get("attempts") or 1) + 1
        pending = {**pending, "attempts": tries, "error": (str(exc) or type(exc).__name__)[:120]}
        _save(d, job_id, pending, wiped=wiped)
        late = d.clock() - float(pending.get("first_at") or d.clock()) >= MAX_SECONDS
        if (tries >= MAX_ATTEMPTS or late) and not pending.get("paged"):
            logger.error("medchron job %s: its %s ending is still not recorded (%s)", job_id, pending["state"], exc)
            unit = f"{'wiped' if wiped else 'bound'}:{job_id}"
            if proof_watch.alert(d, job_id, CODE, detail(d, job_id, pending, wiped=wiped), unit=unit):
                _save(d, job_id, {**pending, "paged": True}, wiped=wiped)
        return False
    if isinstance(pending.get("proof"), dict):
        try:
            proof_watch._client(d)._request(pending["proof"])
        except (BrokerError, OSError, ValueError) as exc:
            logger.error("medchron job %s: its ending landed and its proof did not: %s", job_id, exc)
    _save(d, job_id, None, wiped=wiped)
    if pending.get("paged"):
        # SMD was paged for it: the run needed someone (work_health_runs).
        try:
            d.broker.record(job_id, pending["state"], {"run": run_record.block("record_late")})
        except BrokerError as exc:
            logger.warning("could not note the late record of %s: %s", job_id, exc)
    return True


def retry(d: Any) -> bool:
    """One pass over the kept endings, in workdirs and in the store; False
    while one still waits."""
    ok = True
    for jd in sorted(d.jobs.iterdir()) if d.jobs.is_dir() else []:
        pending = d._daemon_state(jd.name).get("record_pending")
        if isinstance(pending, dict) and not _land(d, jd.name, pending):
            ok = False
    for path in sorted(store(d).glob("*.json")) if store(d).is_dir() else []:
        try:
            pending = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("unreadable pending ending %s: %s", path.name, exc)
            continue
        if not _land(d, path.stem, pending, wiped=True):
            ok = False
    return ok


def at_wipe(d: Any, job_id: str) -> None:
    """Before a workdir goes: an ending still not recorded is tried once more,
    then moved to the store with its wake's facts, and paged once (the page is
    retried from the store while it cannot be written)."""
    pending = d._daemon_state(job_id).get("record_pending")
    if not isinstance(pending, dict) or _land(d, job_id, pending):
        return
    pending = d._daemon_state(job_id).get("record_pending") or pending
    reason = (pending.get("fields") or {}).get("reason")
    pending = {**pending, "said": wake_said(d, job_id, str(pending.get("state")), reason), "paged": False}
    _save(d, job_id, pending, wiped=True)
    if proof_watch.alert(d, job_id, CODE, detail(d, job_id, pending, wiped=True), unit=f"wiped:{job_id}"):
        _save(d, job_id, {**pending, "paged": True}, wiped=True)
    logger.error("medchron job %s: its workdir is wiped with its %s ending kept in %s", job_id, pending["state"], STORE)


__all__ = [
    "CODE",
    "MAX_ATTEMPTS",
    "MAX_SECONDS",
    "RETRY_SECONDS",
    "STORE",
    "at_wipe",
    "detail",
    "keep",
    "retry",
    "store",
    "wake_said",
]
