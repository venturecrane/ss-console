"""The ss#2258 transmit verbs: the ONLY transmit path on the Machine.

Gateway-PID-gated on the verb table (``verbs.py``) DELIBERATELY: unlike the
heartbeat/escalation verbs, these are not reachable by a cron pre_run child or
an execute_code turn. An agent-uid gate would let any future agent-uid process
dispatch a real message with no approval, which is a weaker posture than
today's; the gate has to tighten here, not relax.

The residual is named honestly: an in-gateway rogue path can still reach these
verbs. What changed is that it can now only reach people the seat's own config
names, only from the seat's own inbox, and never without leaving a row,
because the row is written here, by the process that holds the key, not by
best-effort plugin code that can be skipped.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .agentmail_ops import AgentMailRefused, AgentMailTransportError, collect_recipients
from .canon import canonical
from .msgraph_ops import MsGraphRefused, MsGraphTransportError
from .msgraph_ops import collect_recipients as collect_msgraph_recipients

VERBS: tuple[str, ...] = ("agentmail_send", "agentmail_reply", "msgraph_send", "msgraph_reply")

#: What a transmit ops verb may contribute to its own audit row, by name.
#: A closed list rather than "copy the result": the result is a vendor-shaped
#: dict, and a wholesale copy would let a future field land in the ledger
#: without anyone deciding it should.
#:
#: ``sender_key``       ss#2497: who a reply answered, hashed.
#: ``audit_row_token``  ss#2499: the ULID stamped on the message as
#:                      ``X-SMD-Audit-Row``. The join that survives a failed
#:                      vendor-id lookup, because it is on the message itself.
#: ``vendor_message_id`` ss#2497's field name for the provider's own id.
#: ``graph_message_id`` the mailbox-local Graph id, which is what a Graph query
#:                      addresses; the vendor id is the RFC2822 one, which is
#:                      what survives outside the mailbox. Both, because a firm
#:                      asking "is this ours?" may hold either.
#: ``lookup``           whether that resolution succeeded, and why not. Recorded
#:                      so a blank id is never mistaken for a mailbox that had
#:                      nothing to find.
_OPS_AUDIT_KEYS: tuple[str, ...] = (
    "sender_key",
    "audit_row_token",
    "vendor_message_id",
    "graph_message_id",
    "lookup",
)

#: Of those, the ones that stay in the ledger and never travel back to the agent.
_AUDIT_ONLY_KEYS = frozenset({"sender_key", "audit_row_token"})

#: What a CALLER may contribute to a transmit's audit row, by name (WS-RENDER).
#: Same posture as ``session_id``/``matter_ref`` (ss#2497): attribution the
#: agent asserts about its own send, never authorization; the broker still
#: decides recipients from the seat's own config. Closed list so a caller
#: cannot widen the ledger.
#:
#: ``routing_leg``          which case-alert-routing leg picked the recipients
#:                          (central | matter_staff_responsible |
#:                          matter_staff_assisting | fallback).
#: ``rendered_body_sha256`` canonical_body_sha256 of the text the gate allowed,
#:                          computed by the overlay PRE-mutation (before the
#:                          html/plain attach); the console's wake<->confirm
#:                          hash join (send_verify.py) compares it against the
#:                          EMITTED_WAKE stamps; arbiter fixture:
#:                          operator/contracts/fixtures/body-canon-vectors.json.
#: ``plain_body_sha256``    canonical_body_sha256 of the exact text/plain the
#:                          overlay handed the channel, computed POST-attach;
#:                          the console's confirm<->channel check compares it
#:                          against the body fetched back from the mailbox,
#:                          which stores the down-render, not the authored
#:                          markdown. OMITTED by the overlay when no down-render
#:                          happened, and that absence is MEANINGFUL to the
#:                          verifier ("text is still the authored bytes"), so it
#:                          must never be synthesized here.
#: ``body_variant``         full | skeleton; a skeleton match grades
#:                          ``degraded`` in the verifier, never BODY_DIVERGED.
#: ``skill_name``           the routine that authored the body, resolved by the
#:                          overlay from the CRON SESSION ID at emission
#:                          (cron_attribution.resolve_routine), a scheduler
#:                          fact, not an agent assertion. Goes to its COLUMN,
#:                          not into metadata (``append_send_row`` moves it),
#:                          because the console's wake<->confirm join
#:                          (send_verify.py) claims a wake BY SKILL and the
#:                          EMITTED_WAKE half already lives in that column.
#:                          Before this key, every CONFIRM row wrote NULL there
#:                          and the join fell through to hash-only attribution
#:                          (claims review 2026-09-04, B3).
#:
#: CLOSED ALLOWLIST, AND SILENTLY SO. The filter below drops any key not named
#: here with no error and no log, which is the right posture for an untrusted
#: caller-supplied dict but means a stamp the overlay adds WITHOUT a matching
#: entry here vanishes between the two repos and the verifier simply never sees
#: it. Adding a stamp is therefore a two-repo change; ``plain_body_sha256``
#: pairs with hermes-smd-overlay#338, ``skill_name`` with the overlay's
#: fix/prerendered-dispatch-skill-name pair PR.
_CALLER_AUDIT_KEYS: tuple[str, ...] = (
    "routing_leg",
    "rendered_body_sha256",
    "plain_body_sha256",
    "body_variant",
    "skill_name",
)


def append_send_row(
    broker: Any,
    action_type: str,
    verb: str,
    metadata: dict[str, Any],
    *,
    session_id: str = "",
    matter_ref: str | None = None,
) -> None:
    """Record a transmit attempt. Body digests only, never the body.

    Written by the broker itself so the row cannot be skipped. The four
    unaudited messages of 2026-08 were possible because emission lived in
    plugin code that returned early whenever its audit client was unset; a
    row written here has no such branch.

    THE TWO JOINS (ss#2497). Measured on the live A&P ledger 2026-08-21
    (``vfy_01M0H8DR6JAPYVHFMNJZXQZ517``): ``session_id`` appeared on 0 of 9
    CONFIRM_SEND_DISPATCHED rows and ``matter_ref`` on none of them, so a
    send could not be tied to the turn that composed it or to the matter it
    concerned. Neither is knowable HERE (this process has no session and
    does not read matters) so both travel on the request beside the payload
    and are written straight through, unexamined. That is deliberate: they
    are attribution the agent asserts about its own turn, not authorization,
    and the broker's authority is over WHO may be written to, which it still
    decides for itself from the seat's own config.

    ``matter_ref`` goes to its COLUMN (``LedgerWriter`` accepts it as an
    agent column) rather than into metadata, because the column is what the
    portal audit record filters and indexes on. Empty values are omitted
    rather than written as ``""``, which the hash chain canonicalizes
    distinctly from NULL and which reads as a reference that is present and
    blank.

    ``skill_name`` (B3, claims review 2026-09-04) arrives inside ``metadata``
    (it rides ``audit_extra`` through the closed allowlist with the body
    stamps) and is MOVED to its column here, for the same reason
    ``matter_ref`` has one: the console's wake<->confirm join reads
    ``skill_name`` off the column on both halves, and a value parked in
    metadata would be a fourth place to look. Popped, so it never appears
    twice; omitted when empty, like ``matter_ref``. The dict the caller
    passed is mutated on purpose: every caller hands over a literal.
    """
    if broker.ledger is None:
        return
    skill_name = metadata.pop("skill_name", "")
    row: dict[str, Any] = {
        "action_type": action_type,
        "actor": "operator",
        "actor_role": "agent",
        **({"skill_name": skill_name} if skill_name else {}),
        **({"matter_ref": matter_ref} if matter_ref else {}),
        "metadata": json.dumps(
            {
                "customer": broker.customer_slug,
                "verb": verb,
                **({"session_id": session_id} if session_id else {}),
                **metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    broker.ledger.append(row)


def _audit_extra(request: dict[str, Any]) -> dict[str, str]:
    # WS-RENDER: the caller's body-conformance stamps, read from the REQUEST
    # like the two joins, filtered through a closed allowlist (string values
    # only). Optional at both ends, so the overlay and this process deploy in
    # either order.
    raw_extra = request.get("audit_extra") if isinstance(request.get("audit_extra"), dict) else {}
    return {
        key: raw_extra[key].strip()
        for key in _CALLER_AUDIT_KEYS
        if isinstance(raw_extra.get(key), str) and raw_extra[key].strip()
    }


def _clean(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def dispatch_transmit(
    broker: Any,
    action: str,
    request: dict[str, Any],
    *,
    send: Any,
    reply: Any,
    refused: type[Exception],
    transport: type[Exception],
    attempted_for_send: Any,
    identity_key: str,
) -> dict[str, Any]:
    """Execute a fenced transmit and record its outcome either way.

    Shared by both mail channels ON PURPOSE. The row this writes is the only
    evidence that a send happened, and two hand-maintained copies of an audit
    writer drift, silently, and in the direction of the copy nobody is
    reading. Channel differences are parameters, not forks: which ops object,
    which exception pair, and what the sending identity is called
    (``inbox_id`` for AgentMail, ``mailbox`` for Graph).
    """
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"{action} requires a 'payload' object")
    # ss#2497: the audit joins, read from the REQUEST and never from the
    # payload. The payload is what reaches the vendor and is rebuilt from a
    # closed allowlist, so an audit field placed there would be dropped
    # silently. Both are optional: a caller that predates them writes exactly
    # the row it writes today, which is what lets the overlay and this
    # process be deployed in either order.
    session_id = _clean(request, "session_id")
    matter_ref = _clean(request, "matter_ref")
    audit_extra = _audit_extra(request)
    # Digest what the caller asked to send, computed here, so the row proves
    # which content went out without the ledger ever holding the content.
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    # A reply names no recipient (it is derived from the source message), so
    # there is nothing to pre-record for it. Whoever it reached appears on the
    # dispatched row, resolved by the broker.
    attempted = [] if action.endswith("_reply") else attempted_for_send(payload)
    failed_row = {"recipients": attempted, "input_digest": digest, **audit_extra}
    try:
        result = send(payload) if action.endswith("_send") else reply(payload)
    except refused as exc:
        append_send_row(
            broker,
            "CONFIRM_SEND_FAILED",
            action,
            {"outcome": "refused", "reason": str(exc), **failed_row},
            session_id=session_id,
            matter_ref=matter_ref,
        )
        raise
    except transport as exc:
        # A transport failure is NOT a policy refusal and must never read as
        # one: the seat was permitted to write and the vendor call failed.
        # The outcome is genuinely unknown (the message may have gone out),
        # which is exactly what the console-side reconciler exists to settle.
        append_send_row(
            broker,
            "CONFIRM_SEND_FAILED",
            action,
            {"outcome": "transport_error", "reason": str(exc), **failed_row},
            session_id=session_id,
            matter_ref=matter_ref,
        )
        raise
    append_send_row(
        broker,
        "CONFIRM_SEND_DISPATCHED",
        action,
        {
            "outcome": "sent",
            "recipients": result.get("recipients") or [],
            "message_id": result.get("message_id") or "",
            identity_key: result.get(identity_key) or "",
            "input_digest": digest,
            **audit_extra,
            # The ops verb's own contributions to the row, written through by
            # NAME rather than by wholesale copy, so a transmit result can
            # never quietly widen what the ledger records.
            #
            # ss#2497: on a REPLY the ops verb resolved the original sender
            # itself (it had to: a caller naming the sender could name any
            # sender), so the row can name the person it answered without an
            # address entering the ledger. A send names no such person and
            # contributes no key.
            #
            # ss#2499: and on msgraph it contributes the message's identity,
            # which Graph's 202 does not return and which the broker goes and
            # looks up. Every key here is OPTIONAL: AgentMail contributes none
            # of them and writes exactly the row it writes today.
            **{key: result[key] for key in _OPS_AUDIT_KEYS if isinstance(result.get(key), str) and result[key]},
        },
        session_id=session_id,
        matter_ref=matter_ref,
    )
    # ``sender_key`` and ``audit_row_token`` are audit provenance, not
    # transmit results: they stay in the row and do not travel back to the
    # agent. The fence deliberately does not tell the agent who it just wrote
    # to beyond what it already knew, and it does not hand back the audit key
    # that a later message could then be stamped with to borrow this row's
    # identity. The vendor ids DO go back: they are the agent's own message,
    # and naming it to the firm is the point of resolving them.
    return {"ok": True, **{k: v for k, v in result.items() if k not in _AUDIT_ONLY_KEYS}}


def agentmail(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    if broker.agentmail is None or broker.ledger is None:
        raise ValueError(
            "agentmail transmit is not configured on this broker "
            "(needs SMD_AGENTMAIL_CREDENTIAL_PATH and an audit ledger)"
        )
    return dispatch_transmit(
        broker,
        action,
        request,
        send=broker.agentmail.send,
        reply=broker.agentmail.reply,
        refused=AgentMailRefused,
        transport=AgentMailTransportError,
        attempted_for_send=collect_recipients,
        identity_key="inbox_id",
    )


def msgraph(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    if broker.msgraph is None or broker.ledger is None:
        raise ValueError(
            "msgraph transmit is not configured on this broker (needs SMD_MSGRAPH_CREDENTIAL_PATH and an audit ledger)"
        )
    return dispatch_transmit(
        broker,
        action,
        request,
        send=broker.msgraph.send,
        reply=broker.msgraph.reply,
        refused=MsGraphRefused,
        transport=MsGraphTransportError,
        attempted_for_send=collect_msgraph_recipients,
        identity_key="mailbox",
    )
