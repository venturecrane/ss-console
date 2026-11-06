"""A code-sent job's note to its requester, and SMD's one alert, from the daemon.

WHY (2026-11-06, the answer key). A drafting job that held on our side, failed
or waited on its lane's proof run left the attorney with the model's own words
or nothing, and SMD heard only if something else happened to page. Now, when
the daemon records such a state (and when a DELIVER wake is lost), it asks the
broker to send the job's reply in the broker's own words (``job_note_send``:
``fixed_wording.STOPPED``, rendered from the job's row), and raises one
``job_stopped`` alert naming the job, its state and its reason.

ONCE PER STATE. The broker's claim is the rule: one reply per
``attempt:state`` key, whoever sends it (this call, or the gateway's DELIVER
turn, which then finds it already answered). The alert is de-duplicated on the
same key (``watch.alert(unit=key)``), so a later lost wake for a job already
noted does not page ``job_stopped`` twice.

WHICH LANES. ``LANES`` mirrors the broker's ``reply_text.CODE_SENT_KINDS``. A
lane added there adds its kind here; every other lane is untouched.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jobs.daemon")

#: Each code-sent lane's reply kind (broker ``reply_text.CODE_SENT_KINDS``).
LANES = {"drafting": "drafting_job"}
#: What ``tell`` returns besides the broker's own outcomes.
NOT_OWED = "not_owed"
FAILED = "failed"


def tell(d: Any, job_id: str, *, why: str) -> str:
    """Have the broker send this job's reply in code; page SMD once for a
    stopped state (sent or not), or for a send that failed. Returns the outcome
    (``sent``, ``already_sent``, ``not_owed``, ``refused`` or ``failed``).
    Never raises: a lane must not lose its job to a note."""
    from .broker_client import BrokerError
    from .proof import watch

    kind = LANES.get(getattr(d, "LANE", ""))
    if kind is None or watch.in_proof_run(d):
        return NOT_OWED
    try:
        resp = watch._client(d)._request({"action": "job_note_send", "kind": kind, "job_id": job_id})
    except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
        logger.error("%s job %s: could not ask for its note (%s): %s", d.LANE, job_id, why, exc)
        watch.alert(d, job_id, "job_stopped", f"{why}; the note to the requester could not be sent: {exc}")
        return FAILED
    outcome = str(resp.get("outcome") or FAILED)
    stop = str(resp.get("stop_class") or "")
    if stop or outcome == FAILED:
        heard = {
            "sent": "the requester was sent its note",
            "already_sent": "the requester already had its note",
        }.get(outcome, f"the note was NOT sent ({outcome}: {resp.get('why') or 'no reason given'})")
        detail = f"{resp.get('state') or '?'} ({stop or 'no note class'}): {resp.get('job_reason') or why}; {heard}"
        watch.alert(d, job_id, "job_stopped", detail, unit=str(resp.get("key") or ""))
    logger.info("%s job %s: note %s (%s)", d.LANE, job_id, outcome, why)
    return outcome


__all__ = ["LANES", "tell"]
