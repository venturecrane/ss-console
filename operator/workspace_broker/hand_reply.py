"""A job's reply SMD sends by hand is recorded as the job's reply (2026-11-06).

THE DEFECT (A&P). A drafting job's completion reply went out as an SMD hand
release (overlay ``shared.matter_file_review``: a held row, then root's
``approve``). That is a plain ``msgraph_reply``, outside the bound-reply claim
(``reply_binding.py``): the job kept ``reply_sent_at`` empty, ``bound_replies``
held no claim for its key, and a ``jobs rewake`` would have sent the
code-written completion a second time.

Two pieces, both on the binding's own rules (its ``verify``, its claim at the
POST, its settling), kept here so ``reply_binding.py`` stays within its size:

* :func:`hand_reply`: a plain reply that names ``for_job`` takes the job's
  once-only claim and ledger mark AT the POST, exactly as the bound reply does;
* :func:`thread_status`: what ``delivery_reply_status`` tells ``jobs rewake``
  and the drafting DELIVER wake before they start a completion turn.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import bound_replies, msgraph_lookup
from .broker_context import BrokerContext
from .msgraph_ops import MsGraphRefused, MsGraphTransportError
from .reply_binding import (
    _GRAPH_ID,
    _NOUN,
    JOB_KINDS,
    MODE_REPLY,
    BindingRefused,
    _audit,
    _claiming_ops,
    _ops,
    _outcome,
    _settle_failed_post,
    _settle_sent,
    _verified_or_audited,
    verify,
)
from .transmit_verbs import dispatch_transmit

logger = logging.getLogger(__name__)


def hand_reply(broker: BrokerContext, action: str, request: dict[str, Any]) -> dict[str, Any]:
    """An ordinary ``msgraph_reply`` that IS a job's reply (``for_job``), sent
    from SMD's review release (overlay ``shared.matter_file_review approve
    --job``), recorded exactly as the bound reply would have been: the job's
    once-only claim and its ledger mark are taken AT the POST, so a later
    ``jobs rewake`` or DELIVER wake finds the job answered and sends nothing.

    WHY (A&P, 2026-11-06): a hand release went out as a plain reply, the claim
    was never taken, and a rewake would have emailed the attorney a second
    completion. The words are SMD's, root-approved, so the job's fixed wording
    is not held to here; everything else is the bound reply's: the job must be
    replyable and unanswered (the same ``verify``), and the email this replies to
    must be in the job's own thread."""
    if action != "msgraph_reply":
        raise BindingRefused("only a reply in the job's thread can be recorded as the job's reply")
    ops = _ops(broker)
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"{action} requires a 'payload' object")
    raw = request.get("for_job")
    if not isinstance(raw, dict) or raw.get("kind") not in JOB_KINDS:
        raise BindingRefused("for_job names a job: {kind, job_id}")
    v = _verified_or_audited(broker, raw, "hand")
    if v.mode != MODE_REPLY:
        raise BindingRefused(f"{_NOUN[v.kind]} job {v.job_id} has no request email to reply in")
    target = str(payload.get("message_id") or "").strip()
    if target != v.graph_message_id:
        found = msgraph_lookup.received_by_graph_id(ops, target) if _GRAPH_ID.match(target) else None
        if found is None or not v.conversation_id or found["conversation_id"] != v.conversation_id:
            _audit(broker, "hand_refused", raw, v=v, reason="not in the job's thread")
            raise BindingRefused(
                f"that email is not in {_NOUN[v.kind]} job {v.job_id}'s thread; it cannot be recorded as its reply"
            )
    db_path = str(broker.audit_db_path)
    if bound_replies.claimed_for_graph_id(db_path, target):
        raise BindingRefused("that email has already had its bound reply; this reply would answer it twice")
    session_id = str(request.get("session_id") or "").strip()
    bound_ops, state = _claiming_ops(broker, ops, v, session_id)
    _audit(broker, "hand_bound", raw, v=v)
    try:
        result = dispatch_transmit(
            broker,
            action,
            request,
            send=ops.send,
            reply=bound_ops.reply,
            refused=MsGraphRefused,
            transport=MsGraphTransportError,
            attempted_for_send=lambda _payload: [v.sender],
            identity_key="mailbox",
            channel="msgraph",
        )
    except Exception as exc:
        if state["claimed"]:
            _settle_failed_post(broker, ops, v, raw, exc, str(state["claimed_at"]))
        raise
    _settle_sent(broker, db_path, v, result)
    return result


def thread_status(broker: BrokerContext, kind: str, ident: str) -> dict[str, Any]:
    """Whether job ``ident``'s completion reply may still be sent, for the
    runner's ``rewake`` and DELIVER wake (2026-11-06): ``{"checked": bool,
    "answered": sentence}``. ``answered`` is "" when nothing stands in the way;
    ``checked`` is False when the mailbox or the ledger could not be read, which
    is never "nothing stands in the way" (the bind re-checks at the send)."""
    try:
        v = verify(broker, {"kind": kind, "job_id": ident})
    except BindingRefused as exc:
        if exc.code in ("already_replied", "thread_answered"):
            return {"checked": True, "answered": str(exc)}
        # Every other refusal (not ended, failed, no such job) is the bind's own
        # to say at the send; it is not a reply already in the thread.
        return {"checked": True, "answered": ""}
    except (MsGraphRefused, MsGraphTransportError, OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("thread_status: could not check %s %s: %s", kind, ident, exc)
        return {"checked": False, "answered": ""}
    row = v.row if isinstance(v.row, dict) else {}
    # The ledger's mark for THIS outcome (a resumed job's earlier held reply
    # carries an older key and owes nothing now).
    if row.get("reply_sent_at") and row.get("reply_key") == _outcome(v):
        return {
            "checked": True,
            "answered": f"{_NOUN[kind]} job {ident}'s reply is already recorded as sent at {row['reply_sent_at']}; "
            "nothing was sent.",
        }
    return {"checked": True, "answered": ""}


__all__ = ["hand_reply", "thread_status"]
