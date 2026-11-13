"""Client for the broker's staff send-as verbs (ss ADR 0089).

Split out of ``msgraph_broker`` (2026-11-13, ADR 0089 section 9) because these
verbs are not Graph's: the broker picks the seat's authored transport (Microsoft
Graph, or a Google Workspace mailbox) behind the same four verbs, so a seat
whose mail rides AgentMail and Gmail calls exactly these. ``msgraph_broker``
still answers every name below (a lazy re-export), so its callers and the
gate's ``msgraph_broker.send_as_*`` reads are unchanged.

These use the broker socket only (``msgraph_broker.SOCKET_ENV``), never a
Graph credential: nothing here is gated on the seat having Graph.
"""

from __future__ import annotations

import json
import os
from typing import Any

from shared import broker_socket
from shared.msgraph_broker import SEND_TIMEOUT_SECONDS, SOCKET_ENV, MsGraphBrokerUnavailable

# ---------------------------------------------------------------------------
# Staff send-as (ss ADR 0089)
#
# Three verbs, and none of them sends on the gateway's word. ``send_as_propose``
# stores a DRAFT row the broker composes an approval email about and sends to
# the one person the row names; ``send_as_decide`` carries that person's
# answer, and the broker -- not this process -- checks who answered, consumes
# the row and transmits; ``send_as_match_reply`` tells the broker an inbound
# arrived on a conversation it may have sent into.
#
# VERDICTS, NOT EXCEPTIONS. Unlike ``msgraph_broker._call``, a refusal here must reach
# the caller as the value it is (``{"ok": false, "reason": ...}`` or a
# ``status``), because the caller turns it into a sentence for the person. So
# these use their own framing and raise only on a transport fault, which is
# :class:`MsGraphBrokerUnavailable` -- the outcome is unknown, never "refused".
# ---------------------------------------------------------------------------

ACTION_SEND_AS_PROPOSE = "send_as_propose"
ACTION_SEND_AS_DECIDE = "send_as_decide"
ACTION_SEND_AS_MATCH_REPLY = "send_as_match_reply"
ACTION_SEND_AS_DECIDE_LINK = "send_as_decide_link"

#: The decisions ``send_as_decide`` accepts. Closed: anything else is a bug in
#: the caller, refused here before it can reach a verb that transmits.
SEND_AS_DECISIONS: frozenset[str] = frozenset({"send", "change", "cancel"})

#: The terminal statuses ``send_as_decide`` answers with.
SEND_AS_STATUSES: frozenset[str] = frozenset(
    {"DISPATCHED", "FAILED", "REVISED", "CANCELLED", "REFUSED", "EXPIRED", "SUPERSEDED"}
)


def _verdict(payload: dict[str, Any], *, timeout: float = SEND_TIMEOUT_SECONDS) -> dict[str, Any]:
    """One newline-framed request over the broker socket, verdict verbatim.

    Same framing as ``shared.act_broker.verdict``. Raises
    :class:`MsGraphBrokerUnavailable` on any transport or decode fault.
    """
    socket_path = os.environ.get(SOCKET_ENV, "").strip()
    if not socket_path:
        raise MsGraphBrokerUnavailable(
            f"{SOCKET_ENV} is unset; this seat has no broker transmit path"
        )
    try:
        raw = broker_socket.request(socket_path, payload, timeout=timeout, lone_surrogates="raise")
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise MsGraphBrokerUnavailable(f"broker unreachable: {exc}") from exc
    if not isinstance(decoded, dict):
        raise MsGraphBrokerUnavailable("broker returned a malformed verdict")
    return decoded


def send_as_propose(
    *,
    session_id: str,
    instructed_by: str,
    payload: dict[str, Any],
    gate_pass: dict[str, Any],
    tainted: bool,
    sources: list[str],
) -> dict[str, Any]:
    """Ask the broker to store a send-as draft and email its approver.

    ``payload`` is ``{from, to, cc, subject, body_text}`` and nothing else: the
    broker forces ``reply_to``, renders ``body_html`` from ``body_text``, and
    takes the digest over what it will transmit. ``gate_pass`` records that the
    fabrication, matter and identifier gates passed on THIS session, the one
    that holds the read provenance. Returns the broker's verdict:
    ``{"ok": true, "tag", "act_id", "digest", "expires_at", "notified"}`` or
    ``{"ok": false, "reason"}``.
    """
    return _verdict(
        {
            "action": ACTION_SEND_AS_PROPOSE,
            "session_id": session_id,
            "instructed_by": instructed_by,
            "payload": payload,
            "gate_pass": gate_pass,
            "tainted": bool(tainted),
            "sources": list(sources),
        }
    )


def send_as_decide(
    *,
    tag_or_act_id: str,
    decision: str,
    decided_by: str,
    internet_message_id: str,
    instruction: str | None = None,
    graph_message_id: str = "",
) -> dict[str, Any]:
    """Carry a person's ``send`` / ``change`` / ``cancel`` to the broker.

    The broker decides everything that matters: whether the row is open and
    unexpired, whether ``decided_by`` may make this decision, whether the
    answering message is a forgery out of the Operator's own Sent Items, and
    only then consumes and transmits. ``tag_or_act_id`` is the bare 8-hex id
    or the full ``[draft xxxxxxxx]`` tag; the broker accepts either. These two
    verbs are gateway-only at the broker; call them from the gateway process
    (``pre_tool_call`` / ``pre_llm_call``). Returns ``{"status", "reason",
    "instruction", "replaced_by"}``.

    ``graph_message_id`` is additive to the contract: the Graph id of the
    answering message in the Operator's mailbox, sent beside the RFC 2822 id so
    the forgery check can look the item up directly when the internet id is
    absent. A broker that does not read it ignores it.
    """
    if decision not in SEND_AS_DECISIONS:
        raise ValueError(f"send_as_decide: unknown decision {decision!r}")
    request: dict[str, Any] = {
        "action": ACTION_SEND_AS_DECIDE,
        "tag_or_act_id": tag_or_act_id,
        "decision": decision,
        "decided_by": decided_by,
        "internet_message_id": internet_message_id,
        "instruction": instruction,
    }
    if graph_message_id:
        request["graph_message_id"] = graph_message_id
    return _verdict(request)


def send_as_decide_link(*, token: str) -> dict[str, Any]:
    """Carry an approve-button click to the broker (ss ADR 0089 amendment 5a).

    The token IS the authorization and the broker holds the key, so this call
    carries nothing else: no approver, no decision, nothing a caller could
    choose. The broker re-reads the row and applies every check the emailed lane
    applies. Callable from any broker peer on purpose — the seat's web gate is
    not the gateway process — which is safe only because the key is broker-owned
    and unreadable here.
    """
    return _verdict({"action": ACTION_SEND_AS_DECIDE_LINK, "token": token})


def send_as_match_reply(
    *,
    conversation_id: str,
    internet_message_id: str,
    from_addr: str,
    in_reply_to: str = "",
    references: str = "",
) -> dict[str, Any]:
    """Tell the broker an inbound arrived on ``conversation_id``.

    The broker matches it against a DISPATCHED send-as row and, on a match,
    emails that row's approver itself. Returns ``{"matched", "tag"}``.
    ``in_reply_to`` / ``references`` (the inbound's own headers) are how a
    Gmail-transport row is found (ADR 0089 section 9): sent only when present,
    so a broker that predates them reads exactly what it read.
    """
    request: dict[str, Any] = {
        "action": ACTION_SEND_AS_MATCH_REPLY,
        "conversation_id": conversation_id,
        "internet_message_id": internet_message_id,
        "from": from_addr,
    }
    if in_reply_to:
        request["in_reply_to"] = in_reply_to
    if references:
        request["references"] = references
    return _verdict(request)


__all__ = [
    "ACTION_SEND_AS_DECIDE",
    "ACTION_SEND_AS_DECIDE_LINK",
    "ACTION_SEND_AS_MATCH_REPLY",
    "ACTION_SEND_AS_PROPOSE",
    "SEND_AS_DECISIONS",
    "SEND_AS_STATUSES",
    "send_as_decide",
    "send_as_decide_link",
    "send_as_match_reply",
    "send_as_propose",
]
