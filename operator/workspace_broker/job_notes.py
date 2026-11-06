"""``job_note_send``: the runner daemon has the broker send a job's reply in the
broker's own words, with no model and no gateway turn.

WHY. A lost DELIVER wake leaves no turn to send a delivered job's reply, so the
broker sends it itself (2026-11-06). The same verb answers what the daemon
needs for SMD's ``job_stopped`` page (``jobs/job_notes.py``): the job's state,
its stop class and its reason.

A STOPPED JOB IS TOLD NOTHING (Captain, 2026-11-06, ``stop_note``). For a job
that held, failed or waited on its lane's proof run this verb sends nothing to
anyone and answers ``not_owed`` with the class, before the binding is even
read; the binding refuses those states as well, so a gateway turn cannot send
one either. (#3816 had this verb send a fixed "we are checking it" note; that
note is gone.)

WHAT IT DOES otherwise. Root only (the runner daemon). For a kind in
``reply_text.CODE_SENT_KINDS``: verify the job's binding exactly as the
gateway's bind does (``reply_binding.verify``: the requester's own email in
this seat's Inbox, the sender check, once per ``attempt:state``), render the
job's one text (``reply_text.fixed_texts``), and send it through the same bound
reply verb the gateway uses, so the send check, the claim, the participant
fence, the audit row and the Sent Items proof are the same ones. A reply
already sent for that key is ``already_sent``; nothing goes twice. It writes no
alert itself: the daemon owns the alert path and its de-duplication.
"""

from __future__ import annotations

import logging
from typing import Any

from . import reply_binding, reply_text, stop_note
from .broker_context import BrokerContext
from .msgraph_ops import MsGraphRefused, MsGraphTransportError

logger = logging.getLogger(__name__)

VERBS: tuple[str, ...] = ("job_note_send",)
REASON_MAX = 300


def _row(broker: BrokerContext, kind: str, job_id: str) -> dict[str, Any] | None:
    if not broker.audit_db_path:
        return None
    try:
        return reply_binding.job_ledger(broker, kind).read(job_id)
    except Exception:  # an unreadable row answers "refused", never raises
        logger.exception("the %s %s row could not be read for its note", kind, job_id)
        return None


def send_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], pid: int, uid: int | None
) -> dict[str, Any]:
    kind = str(request.get("kind") or "")
    job_id = str(request.get("job_id") or "").strip()
    if kind not in reply_text.CODE_SENT_KINDS:
        raise ValueError(f"job_note_send sends only {sorted(reply_text.CODE_SENT_KINDS)} replies")
    binding = {"kind": kind, "job_id": job_id}
    row = _row(broker, kind, job_id)
    about = {
        "state": str((row or {}).get("state") or ""),
        "stop_class": stop_note.stop_class(kind, row) or "",
        "job_reason": str((row or {}).get("reason") or "")[:REASON_MAX],
        "key": reply_binding.reply_key(kind, job_id, row) if row else "",
    }

    def answer(outcome: str, why: str = "") -> dict[str, Any]:
        return {"ok": True, "outcome": outcome, **about, **({"why": why[:REASON_MAX]} if why else {})}

    if about["stop_class"]:
        return answer("not_owed", "a stopped job's requester is told nothing; SMD is paged")
    try:
        v = reply_binding.verify(broker, binding)
    except reply_binding.BindingRefused as exc:
        if exc.code == "already_replied":
            return answer("already_sent")
        if exc.code in ("job_failed", "job_held", "not_ended"):
            return answer("not_owed", str(exc))
        return answer("refused", str(exc))
    except (MsGraphRefused, MsGraphTransportError, ValueError) as exc:
        return answer("failed", f"the binding could not be checked: {type(exc).__name__}")
    texts = reply_text.fixed_texts(broker, kind, v.row)
    if len(texts) != 1 or not texts[0].strip():
        return answer("refused", "the record gives no single wording for this reply")
    try:
        reply_binding.reply_verb(
            broker,
            "msgraph_reply_bound",
            {"binding": binding, "payload": {"comment": texts[0]}, "audit_extra": {"sent_by": "job_note"}},
            pid,
            uid,
        )
    except reply_binding.BindingRefused as exc:
        if "already" in str(exc):
            return answer("already_sent")
        return answer("refused", str(exc))
    except (MsGraphRefused, MsGraphTransportError, ValueError, OSError) as exc:
        return answer("failed", f"the send failed: {type(exc).__name__}")
    return answer("sent")


__all__ = ["VERBS", "send_verb"]
