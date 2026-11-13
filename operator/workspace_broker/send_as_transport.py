"""The send-as lane's plumbing: its configuration check, its Graph handle, and
the audited seat-mailbox send its approval emails and notices ride.

Split out of ``send_as_acts.py`` (module-size ratchet) when the handlers were
typed ``BrokerContext`` (code review 2026-09-25, Code Quality 1). Typing them
exposed seven reads of ``broker.msgraph`` / ``broker.audit_db_path`` that
relied on ``require_configured`` having run in ANOTHER function, which narrows
nothing for a type checker; ``graph_of`` is the narrowing, with the same
refusal if it ever runs unguarded. The acts (propose, decide, the link, the
reply match) stay in ``send_as_acts.py``; nothing here decides anything.

THE TRANSPORT SEAM (ADR 0089 section 9, 2026-11-13). A send-as row can leave
through Microsoft Graph (Exchange Send As on the Operator's mailbox) or through
a Google Workspace mailbox (``gmail_send_as``). The acts never name either:
they ask :func:`transport_for` for the seat's one authored transport and call
the protocol below. :class:`GraphSendAs` is a pure delegation to the code that
ran before the seam, so a Graph seat runs what it ran (the gate is
``tests/test_send_as.py`` passing unmodified).
"""

from __future__ import annotations

from typing import Any, Protocol

from . import seat_config

from .broker_context import BrokerContext
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError, collect_recipients
from .participant_fence import LANE_SEND_AS
from .send_intent import CONFIRM_FLAG, record_confirm, record_intent
from .transmit_verbs import append_send_row, dispatch_transmit

NOT_CONFIGURED = "send-as is not configured on this broker (needs msgraph, an audit ledger, and an audit db)"
_DECIDE_VERB = "send_as_decide"

#: The transports a ``scope.staff_send_as`` entry may author as ``via``.
VIA_MSGRAPH = "msgraph"
VIA_GMAIL = "gmail"
VIAS: tuple[str, ...] = (VIA_MSGRAPH, VIA_GMAIL)

UNREADABLE_VIA = (
    "send-as is not configured on this broker: scope.staff_send_as must author one transport"
    " (via: msgraph or via: gmail) for every entry"
)


class DraftRefused(ValueError):
    """A proposal a transport cannot prepare (``prepare``). The acts re-raise it
    as their own ``SendAsRefused``, so it is audited like every other refusal."""


class OriginRefused(ValueError):
    """The answer's origin check ran and said no. A failure to RUN the check is
    the transport's own error class; both refuse."""


class SendAsTransport(Protocol):
    """What the send-as acts need from a transport, and nothing else."""

    via: str
    #: Approve buttons are allowed (ADR 0089 5a). False where the seat can read
    #: the approver's mailbox, because the seat could then lift its own link.
    links: bool
    #: A failure that certainly sent nothing ("Not sent", never "Not confirmed").
    refused: type[Exception]
    #: Every error a transport call may raise; each one refuses or is reported.
    failures: tuple[type[Exception], ...]

    def reply_mailbox(self) -> str: ...
    def approvers_for(self, approver: str, data: dict[str, Any]) -> set[str]: ...
    def prepare(self, msg: dict[str, Any], request: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]: ...
    def send_as(self, msg: dict[str, Any], from_address: str) -> dict[str, Any]: ...
    def check_answer_origin(self, request: dict[str, Any], approver: str) -> None: ...
    def conversation_of(self, result: dict[str, Any]) -> str: ...
    def reply_keys(self, request: dict[str, Any]) -> list[str]: ...
    def notify(self, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]: ...


def authored_via(data: dict[str, Any]) -> str | None:
    """The one transport ``scope.staff_send_as`` authors, or ``None``.

    Absent ``via`` is ``msgraph`` (every entry authored before ADR 0089
    section 9). ``None`` for an unknown value or for entries that disagree: one
    seat sends as staff through one transport, so a mixed list is refused, never
    guessed at.
    """
    scope = data.get("scope") if isinstance(data, dict) else None
    entries = scope.get("staff_send_as") if isinstance(scope, dict) else None
    vias = {
        str(entry.get("via") or VIA_MSGRAPH).strip().lower()
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
    }
    if not vias:
        return VIA_MSGRAPH
    if len(vias) != 1 or not vias <= set(VIAS):
        return None
    return vias.pop()


def seat_data(broker: BrokerContext) -> dict[str, Any]:
    data = seat_config.load(broker.customer_path, encoding="utf-8", on_missing="raise") or {}
    return data if isinstance(data, dict) else {}


def transport_for(broker: BrokerContext) -> SendAsTransport:
    """The seat's authored send-as transport, ready to use, or ``ValueError``.

    Read from customer.yaml on every call, the file the acts read the roster
    from. Any shortfall refuses: an unknown or mixed ``via``, no ledger or audit
    db, no Graph on a Graph seat, no Google credential or AgentMail inbox on a
    Gmail seat.
    """
    if broker.ledger is None or not broker.audit_db_path:
        raise ValueError(NOT_CONFIGURED)
    try:
        via = authored_via(seat_data(broker))
    except Exception as exc:
        raise ValueError(NOT_CONFIGURED) from exc
    if via is None:
        raise ValueError(UNREADABLE_VIA)
    if via == VIA_GMAIL:
        from .gmail_send_as import GmailSendAs  # imported here: it imports this module

        return GmailSendAs(broker)
    if broker.msgraph is None:
        raise ValueError(NOT_CONFIGURED)
    return GraphSendAs(broker)


def require_configured(broker: BrokerContext) -> None:
    transport_for(broker)


class GraphSendAs:
    """The Graph transport: a pure delegation to ``MsGraphOps``, the calls
    ``send_as_acts`` made before the seam existed."""

    via = VIA_MSGRAPH
    links = True
    refused: type[Exception] = MsGraphRefused
    failures: tuple[type[Exception], ...] = (MsGraphRefused, MsGraphTransportError, OriginRefused)

    def __init__(self, broker: BrokerContext) -> None:
        self._broker = broker
        self._graph = graph_of(broker)

    def reply_mailbox(self) -> str:
        return self._graph.mailbox()

    def approvers_for(self, approver: str, data: dict[str, Any]) -> set[str]:
        return {approver}

    def prepare(self, msg: dict[str, Any], request: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        return msg

    def send_as(self, msg: dict[str, Any], from_address: str) -> dict[str, Any]:
        return self._graph.send_as_staff(msg, from_address)

    def check_answer_origin(self, request: dict[str, Any], approver: str) -> None:
        wanted = request.get("internet_message_id")
        wanted = wanted.strip()[:998] if isinstance(wanted, str) else ""
        if self._graph.sent_items_holds(wanted):
            raise OriginRefused("that answer was sent from the Operator's own mailbox")

    def conversation_of(self, result: dict[str, Any]) -> str:
        return self._graph.conversation_of(result.get("graph_message_id") or "")

    def reply_keys(self, request: dict[str, Any]) -> list[str]:
        return []

    def notify(self, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        return graph_audited_send(self._broker, payload, session_id)


def graph_of(broker: BrokerContext) -> MsGraphOps:
    """The Graph transport, narrowed. Every entry point runs
    ``require_configured`` first, but that check does not narrow an attribute
    for the helpers it calls; this does, with the same refusal."""
    if broker.msgraph is None:
        raise ValueError(NOT_CONFIGURED)
    return broker.msgraph


def audited_send(broker: BrokerContext, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    """A seat-mailbox send (approval email or notice) through the seat's
    authored transport's audited dispatch."""
    return transport_for(broker).notify(payload, session_id)


def graph_audited_send(broker: BrokerContext, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
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
    "DraftRefused",
    "GraphSendAs",
    "NOT_CONFIGURED",
    "seat_data",
    "OriginRefused",
    "SendAsTransport",
    "UNREADABLE_VIA",
    "VIAS",
    "VIA_GMAIL",
    "VIA_MSGRAPH",
    "authored_via",
    "graph_audited_send",
    "transport_for",
    "audit",
    "audited_send",
    "graph_of",
    "notice",
    "open_intent",
    "require_configured",
    "settle",
]
