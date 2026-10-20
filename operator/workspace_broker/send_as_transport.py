"""The send-as lane's plumbing: its configuration check, its Graph handle, and
the audited seat-mailbox send its approval emails and notices ride.

Split out of ``send_as_acts.py`` (module-size ratchet) when the handlers were
typed ``BrokerContext`` (code review 2026-09-25, Code Quality 1). Typing them
exposed seven reads of ``broker.msgraph`` / ``broker.audit_db_path`` that
relied on ``require_configured`` having run in ANOTHER function, which narrows
nothing for a type checker; ``graph_of`` is the narrowing, with the same
refusal if it ever runs unguarded. The acts (propose, decide, the link, the
reply match) stay in ``send_as_acts.py``; nothing here decides anything.
"""

from __future__ import annotations

from typing import Any

from .broker_context import BrokerContext
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError, collect_recipients
from .participant_fence import LANE_SEND_AS
from .send_intent import CONFIRM_FLAG, record_confirm, record_intent
from .transmit_verbs import append_send_row, dispatch_transmit

NOT_CONFIGURED = "send-as is not configured on this broker (needs msgraph, an audit ledger, and an audit db)"
_DECIDE_VERB = "send_as_decide"


def require_configured(broker: BrokerContext) -> None:
    if broker.msgraph is None or broker.ledger is None or not broker.audit_db_path:
        raise ValueError(NOT_CONFIGURED)


def graph_of(broker: BrokerContext) -> MsGraphOps:
    """The Graph transport, narrowed. Every entry point runs
    ``require_configured`` first, but that check does not narrow an attribute
    for the helpers it calls; this does, with the same refusal."""
    if broker.msgraph is None:
        raise ValueError(NOT_CONFIGURED)
    return broker.msgraph


def audited_send(broker: BrokerContext, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    """A seat-mailbox send (approval email or notice) through the SAME audited
    transmit every other broker send uses: recipient-fenced, and written to the
    ledger as CONFIRM_SEND_DISPATCHED with its audit header, so the console's
    send reconciler (operator/bin/reconcile-sends.py) joins it by identity
    rather than flagging the Operator's own approval mail as unaudited."""
    keep_copy = not payload.pop("no_sent_copy", False)
    graph = graph_of(broker)
    return dispatch_transmit(
        broker,
        "msgraph_send",
        {"payload": payload, "session_id": session_id},
        send=lambda message: graph.send(message, save_to_sent_items=keep_copy),
        reply=graph.reply,
        refused=MsGraphRefused,
        transport=MsGraphTransportError,
        attempted_for_send=collect_recipients,
        identity_key="mailbox",
        channel="msgraph",
        internal_lane=LANE_SEND_AS,
    )


def notice(broker: BrokerContext, to: str, subject: str, text: str) -> None:
    """A fixed-recipient notice to a staff member, through the audited send."""
    audited_send(broker, {"to": [to], "subject": subject, "body_text": text})


def audit(broker: BrokerContext, action_type: str, verb: str, meta: dict[str, Any], session_id: str = "") -> None:
    append_send_row(broker, action_type, verb, meta, session_id=session_id)


def open_intent(broker: BrokerContext, meta: dict[str, Any]) -> str:
    """The send-as transmit's intent row (send_intent.py, 2026-10-20), written
    before ``send_as_staff``. ``""`` when it could not land, and then nothing
    may be sent: the caller refuses with ``send_intent.NOT_SENT``."""
    try:
        return record_intent(append_send_row, broker, _DECIDE_VERB, meta, refused=MsGraphRefused)
    except MsGraphRefused:
        return ""


def settle(broker: BrokerContext, action_type: str, meta: dict[str, Any], intent_id: str) -> dict[str, bool]:
    """Write the SEND_AS_SENT / SEND_AS_FAILED row naming its intent. Returns
    the response flag to spread: empty when the row landed, and
    ``confirm_row_written: False`` when it did not, so a send that went out is
    never reported as one that failed."""
    if record_confirm(append_send_row, broker, action_type, _DECIDE_VERB, meta, intent_id):
        return {}
    return {CONFIRM_FLAG: False}


__all__ = [
    "NOT_CONFIGURED",
    "audit",
    "audited_send",
    "graph_of",
    "notice",
    "open_intent",
    "require_configured",
    "settle",
]
