"""The participant fence at the transmit: read the anchor, then decide.

``participant_fence`` decides; this module supplies what it decides on, read by
the broker itself and never taken off the wire:

* a MESSAGE anchor (``graph_message`` / ``agentmail_message``) is read from the
  seat's own mailbox: who sent it, and who was on To and Cc;
* a JOB anchor (``medchron_job`` / ``demand_job`` / ``drafting_job``) is the
  job's ledger row: its request email (``request_ref``) is found in the Inbox
  and its From must be the job's ``requester``. A job with no request email
  is no anchor at all;
* a RULE anchor is the pending-rules row: the origin the broker recorded when
  the row was created, whose From must be the row's ``instructed_by``.

Every failure to read is ``FENCE_UNVERIFIABLE``: a refusal recorded as one,
never a transport error, because nothing was attempted.

WHO PASSES THE ANCHOR. The overlay, from code: this turn's prompt-carried
origin, the wake's job id, the rule row's id, or the anchor a held send was
captured with. Not the model. The broker cannot see the turn, so what it
guarantees is narrower and still the point: whatever anchor arrives, the people
it vouches for are the ones the MAILBOX says were on it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from . import msgraph_lookup
from .agentmail_ops import AgentMailRefused, AgentMailTransportError
from .broker_context import BrokerContext
from .msgraph_ops import MsGraphRefused, MsGraphTransportError
from .participant_fence import (
    FENCE_PARTICIPANTS,
    FENCE_UNVERIFIABLE,
    JOB_KINDS,
    MESSAGE_KINDS,
    RULE_KIND,
    Anchor,
    FenceRefused,
    Participants,
    check_reply,
    check_send,
    parse_anchor,
    parse_lane,
    seat_facts,
)
from .recipient_policy import normalize_address

#: The message anchor kind each channel's replies and lookups use.
CHANNEL_KIND = {"msgraph": "graph_message", "agentmail": "agentmail_message"}
_LOOKUP_ERRORS = (
    MsGraphRefused,
    MsGraphTransportError,
    AgentMailRefused,
    AgentMailTransportError,
    OSError,
    ValueError,
    KeyError,
    sqlite3.Error,
)


def _unverifiable(why: str) -> FenceRefused:
    return FenceRefused(FENCE_UNVERIFIABLE, f"the request this send answers could not be verified: {why}.")


def _as_participants(found: dict[str, Any], message_id: str) -> Participants:
    return Participants(
        sender=str(found["sender"]),
        to=tuple(str(a) for a in found.get("to") or []),
        cc=tuple(str(a) for a in found.get("cc") or []),
        conversation_id=str(found.get("conversation_id") or ""),
        message_id=message_id,
    )


def _message(broker: BrokerContext, channel: str, anchor: Anchor) -> Participants:
    if CHANNEL_KIND.get(channel) != anchor.kind:
        raise _unverifiable(f"a {anchor.kind} anchor cannot be read on the {channel} channel")
    if channel == "msgraph":
        if broker.msgraph is None:
            raise _unverifiable("this broker has no msgraph read path")
        return _as_participants(msgraph_lookup.participants_of(broker.msgraph, anchor.ident), anchor.ident)
    if broker.agentmail is None:
        raise _unverifiable("this broker has no AgentMail path")
    return _as_participants(broker.agentmail.participants_of(anchor.ident), anchor.ident)


def _job(broker: BrokerContext, channel: str, anchor: Anchor) -> Participants:
    # Lazy: reply_binding imports transmit_verbs, which imports this module.
    from .reply_binding import job_ledger

    if channel != "msgraph" or broker.msgraph is None or not broker.audit_db_path:
        raise _unverifiable("a job's request email is readable only on the msgraph channel")
    row = job_ledger(broker, anchor.kind).read(anchor.ident)
    if row is None:
        raise _unverifiable("there is no such job")
    ref = str(row.get("request_ref") or "").strip()
    requester = normalize_address(row.get("requester"))
    if not ref or not requester:
        raise _unverifiable("the job records no request email to anchor on")
    if "@" in ref:
        found = msgraph_lookup.find_received(broker.msgraph, ref)
        if found is None:
            raise _unverifiable("the job's request email is not in this seat's Inbox")
        graph_id = found["graph_message_id"]
    else:
        graph_id = ref
    home = _message(broker, channel, Anchor("graph_message", graph_id))
    if home.sender != requester:
        raise _unverifiable("the job's request email was not sent by the job's requester")
    return home


def _rule(broker: BrokerContext, channel: str, anchor: Anchor) -> Participants:
    store = broker.establishment.pending if broker.establishment is not None else None
    if store is None:
        raise _unverifiable("this broker keeps no rule rows")
    row = store.get(anchor.ident)
    if row is None:
        raise _unverifiable("there is no such rule or request")
    requester = normalize_address(row.get("instructed_by"))
    origin = parse_anchor(row.get("origin"), kinds=MESSAGE_KINDS)
    if origin is None:
        # A row recorded without its email (every row proposed before the
        # fence, 30-day replay 2026-10-07: six pilot outcome letters). Its
        # ``instructed_by`` is the broker's own record of who asked, which the
        # seat gate pinned to the turn's verified sender at propose; the letter
        # may reach that one person and nobody else.
        if not requester:
            raise _unverifiable("the rule records neither its request email nor who asked")
        return Participants(sender=requester)
    home = _message(broker, channel, origin)
    if home.sender != requester:
        raise _unverifiable("the rule's request email was not sent by the person who asked")
    return home


def resolver(broker: BrokerContext, channel: str) -> Callable[[Anchor], Participants]:
    """One read per anchor per transmit; every failure is FENCE_UNVERIFIABLE."""
    cache: dict[Anchor, Participants] = {}

    def resolve(anchor: Anchor) -> Participants:
        if anchor in cache:
            return cache[anchor]
        try:
            if anchor.kind in JOB_KINDS:
                found = _job(broker, channel, anchor)
            elif anchor.kind == RULE_KIND:
                found = _rule(broker, channel, anchor)
            else:
                found = _message(broker, channel, anchor)
        except FenceRefused:
            raise
        except _LOOKUP_ERRORS as exc:
            raise _unverifiable(str(exc)[:200]) from exc
        cache[anchor] = found
        return found

    return resolve


def enforce(
    broker: BrokerContext,
    action: str,
    request: dict[str, Any],
    payload: dict[str, Any],
    *,
    channel: str,
    recipients: list[str],
    internal_lane: str | None = None,
) -> dict[str, str]:
    """Refuse (``FenceRefused``) or pass; on a pass, return the audit fields.

    ``internal_lane`` is the broker's own (staff send-as); a request's ``lane``
    is never consulted when it is set."""
    try:
        facts = seat_facts(broker.customer_path)
    except (OSError, ValueError, AttributeError) as exc:
        raise _unverifiable(f"customer.yaml could not be read ({type(exc).__name__})") from exc
    anchor = parse_anchor(request.get("anchor"))
    lane = internal_lane or parse_lane(request.get("lane"))
    resolve = resolver(broker, channel)
    if action.endswith("_reply") or action == "msgraph_reply_bound":
        target = str(payload.get("message_id") or "").strip()
        if not target:
            raise FenceRefused(FENCE_PARTICIPANTS, "a reply must name the email it answers.")
        check_reply(facts, target, anchor=anchor, resolve=resolve, target_kind=CHANNEL_KIND[channel])
    else:
        check_send(facts, recipients, lane=lane, anchor=anchor, resolve=resolve)
    return {
        **({"anchor_kind": anchor.kind} if anchor else {}),
        **({"lane": lane} if lane else {}),
    }


__all__ = ["CHANNEL_KIND", "enforce", "resolver"]
