"""SMD's one page for a code-sent job that stopped, and a lost wake's reply.

STOPPED: SMD KNOWS, THE FIRM DOES NOT (Captain, 2026-11-06). When the daemon
records a drafting or litigation job held, failed or waiting on its lane's proof run, it
reads the job's row and raises ONE ``job_stopped`` alert naming the job, its
state, its stop class and its reason, saying the requester was not told, by
design. It never asks the broker to send anything for such a job; the broker
refuses those states on its own as well (``stop_note``: ``job_note_send``
answers ``not_owed``, and the binding refuses the gateway's turn), so either
side alone keeps the firm out of it, whichever of the image and the jobs bundle
reaches a seat first. #3816's fixed "we are checking it" note, sent the same
morning, is gone.

A LOST WAKE. When a DELIVER wake is lost (``proof/watch.wake_lost``), a
delivered job's reply is sent by the broker in code (``job_note_send``), so the
requester still hears about a filed document.

ONCE PER STATE. The alert is de-duplicated on the broker's reply key
(``kind:id:attempt:state``, ``watch.alert(unit=key)``): a later lost wake for a
job already paged does not page ``job_stopped`` twice, and a resumed job that
stops again pages again.

WHICH LANES. ``LANES`` mirrors the broker's ``stop_note.SILENT_KINDS`` (a lane
added there adds its kind here), and ``CODE_SENT_LANES`` its
``reply_text.CODE_SENT_KINDS``; every other lane is untouched. Litigation
joined on 2026-11-06 for its stops only: a held, failed or waiting list pages
SMD and the firm hears nothing; a delivered list is replied on as before.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jobs.daemon")

#: Each lane whose stops page SMD only (broker ``stop_note.SILENT_KINDS``):
#: its reply kind and the verb that reads its job row.
LANES = {
    "drafting": ("drafting_job", "drafting_job_status"),
    "litigation": ("litigation_job", "litigation_job_status"),
}
#: The lanes whose DELIVERED reply the broker sends in code after a lost wake
#: (broker ``reply_text.CODE_SENT_KINDS``). Litigation is not one: its
#: delivered reply stays the DELIVER turn's (Captain, 2026-11-06), so a lost
#: wake on a delivered list pages ``wake_lost`` saying nobody was told.
CODE_SENT_LANES = frozenset({"drafting"})
#: What ``tell`` returns besides the broker's own outcomes.
NOT_OWED = "not_owed"
FAILED = "failed"
#: What a stopped job's page says the requester heard.
NOT_TOLD = "the requester was not told (by design: SMD only, while the lane is refined)"
REASON_MAX = 300


#: A litigation hold the firm was asked about before 2026-11-06 (broker
#: ``stop_note.LITIGATION_FIRM_HOLDS``): SMD now asks it by hand.
LITIGATION_FIRM_HOLDS = frozenset({"nothing_in_scope", "staff_unresolved", "requester_unresolved"})


def stop_class(row: dict[str, Any], lane: str = "") -> str:
    """The class the page names (mirrors broker ``stop_note``; the broker's own
    answer is the rule for what is sent, this only labels the page)."""
    state = str(row.get("state") or "")
    reason = str(row.get("reason") or "")
    if state == "held" and lane == "litigation":
        from .proof import holds

        cls = holds.classify(lane, reason)[0]
        return f"held_firm_cause, {cls}" if cls in LITIGATION_FIRM_HOLDS else "held"
    if state == "failed":
        return "review_held" if reason.startswith(("review_held", "refused: review_held")) else "failed"
    if state == "submitted":
        return "waiting"
    if state == "held" and row.get("revise_of"):
        return "held_revision"
    return state or "unknown"


def _page_stopped(d: Any, job_id: str, kind: str, row: dict[str, Any], why: str) -> None:
    from .proof import watch

    state = str(row.get("state") or "?")
    key = f"{kind}:{job_id}:{row.get('attempt') or 1}:{state}"  # broker reply_binding.reply_key
    reason = str(row.get("reason") or why)[:REASON_MAX]
    watch.alert(d, job_id, "job_stopped", f"{state} ({stop_class(row, d.LANE)}): {reason}; {NOT_TOLD}", unit=key)


def _send_delivered(d: Any, job_id: str, kind: str, why: str) -> str:
    """A delivered job whose turn is gone: the broker sends its reply in code."""
    from .broker_client import BrokerError
    from .proof import watch

    try:
        resp = watch._client(d)._request({"action": "job_note_send", "kind": kind, "job_id": job_id})
    except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
        logger.error("%s job %s: could not ask for its reply (%s): %s", d.LANE, job_id, why, exc)
        return FAILED
    return str(resp.get("outcome") or FAILED)


def tell(d: Any, job_id: str, *, why: str) -> str:
    """A stopped job (held, failed, waiting): page SMD once and send the
    requester NOTHING. A delivered job (a lost wake): have the broker send its
    reply in code. Returns the outcome (``sent``, ``already_sent``,
    ``not_owed``, ``refused`` or ``failed``). Never raises: a lane must not lose
    its job to a page or a reply."""
    from .broker_client import BrokerError
    from .proof import watch

    lane = LANES.get(getattr(d, "LANE", ""))
    if lane is None or watch.in_proof_run(d):
        return NOT_OWED
    kind, status_verb = lane
    try:
        row = watch._client(d)._request({"action": status_verb, "job_id": job_id}).get("job") or {}
    except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
        logger.error("%s job %s: could not read the job (%s): %s", d.LANE, job_id, why, exc)
        watch.alert(d, job_id, "job_stopped", f"{why}; the job could not be read; nothing was sent: {exc}")
        return FAILED
    if row.get("state") == "delivered":
        outcome = _send_delivered(d, job_id, kind, why) if d.LANE in CODE_SENT_LANES else NOT_OWED
    else:
        _page_stopped(d, job_id, kind, row, why)
        outcome = NOT_OWED
    logger.info("%s job %s: %s (%s)", d.LANE, job_id, outcome, why)
    return outcome


__all__ = ["CODE_SENT_LANES", "LANES", "NOT_TOLD", "stop_class", "tell"]
