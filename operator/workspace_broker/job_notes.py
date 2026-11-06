"""``job_note_send``: the runner daemon has the broker send a job's reply in the
broker's own words, with no model and no gateway turn.

WHY (2026-11-06, the answer key). When an A&P drafting job held on our side,
failed, or waited on its lane's proof run, what the attorney heard was the
model's own words or nothing: a failed job's DELIVER turn was told to send the
firm nothing, a job waiting on its lane's proof run has no turn at all, and a
lost DELIVER wake leaves no turn to send anything. So the note is code's
(``fixed_wording.STOPPED``) and so is the send.

WHAT IT DOES. Root only (the runner daemon). For a kind in
``reply_text.CODE_SENT_KINDS``: verify the job's binding exactly as the
gateway's bind does (``reply_binding.verify``: the requester's own email in
this seat's Inbox, the sender check, once per ``attempt:state``), render the
job's one text (``reply_text.fixed_texts``), and send it through the same bound
reply verb the gateway uses, so the send check, the claim, the participant
fence, the audit row and the Sent Items proof are the same ones. A reply
already sent for that key (by the gateway's turn or an earlier call) is
``already_sent``; nothing goes twice.

It answers what the daemon needs for SMD's one alert (``jobs/job_notes.py``):
the outcome, the reply key, the job's state, the ``STOPPED`` class (empty for a
delivered or firm-worded reply) and the job's own reason. It writes no alert
itself: the daemon owns the alert path and its de-duplication.
"""

from __future__ import annotations

import logging
from typing import Any

from . import fixed_wording, reply_binding, reply_text
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
        "stop_class": fixed_wording.stopped_class(kind, row) or "",
        "job_reason": str((row or {}).get("reason") or "")[:REASON_MAX],
        "key": reply_binding.reply_key(kind, job_id, row) if row else "",
    }

    def answer(outcome: str, why: str = "") -> dict[str, Any]:
        return {"ok": True, "outcome": outcome, **about, **({"why": why[:REASON_MAX]} if why else {})}

    try:
        v = reply_binding.verify(broker, binding)
    except reply_binding.BindingRefused as exc:
        if exc.code == "already_replied":
            return answer("already_sent")
        if exc.code in ("job_failed", "not_ended"):
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
