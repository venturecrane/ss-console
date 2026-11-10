"""The invite-code redeem verb (ADR 0106): gateway-only, one attempt per call.

The webhook router calls it when an unlisted sender's email on a guide or demo seat
that switches enrollment on carries a code-shaped token, before any model sees
the message. What the router may NOT decide, this verb re-derives:

* the seat switches enrollment on (``customer.yaml``, read here);
* the sender and the code: the broker re-reads the message from the mailbox,
  refuses when its sender is not the address the router named, and refuses
  when the code is not in that message's own text;
* whether the sender is already listed (the authored roster, a domain grant,
  an admin, or an earlier enrollment): then nothing is consumed;
* the code, its expiry, its uses, and the lockouts (``sender_enrollment``).

What it TRUSTS from the router is one fact: that the message's From carries an
aligned DKIM or DMARC pass (``shared/sender_auth.py`` on the overlay). The
residual is stated in ADR 0106: a process in the gateway that already holds a
valid code could claim a pass for a message whose From has none.

Every call writes one ``SENDER_ENROLLMENT_ATTEMPT`` row. It carries the
outcome, the sender's hashed key, the message id, and on a matched code its id
and label. Never the code, never a digest of it.
"""

from __future__ import annotations

import json
from typing import Any

from .broker_context import BrokerContext
from .recipient_policy import authored_policy, normalize_address, sender_key
from . import sender_enrollment as se

VERBS: tuple[str, ...] = ("sender_enrollment_redeem", "sender_enrollment_issue")
AUDIT_TYPE = "SENDER_ENROLLMENT_ATTEMPT"
ISSUED_TYPE = "SENDER_ENROLLMENT_CODE_ISSUED"
#: An administrator's code: how long, how many people, unless the tool says.
DEFAULT_DAYS = 14
DEFAULT_MAX_USES = 1
ISSUED_LABEL = "admin-issued"

#: The one message that carries an administrator's code, composed here and
#: nowhere else. The code is in the administrator's own mailbox, which is the
#: point; it is in no row, log or tool result.
_ISSUED_TEXT = (
    "Here is the invite code for {name}:\n\n"
    "{code}\n\n"
    "It works until {expires} UTC for {uses}. {name} connects by emailing {inbox} "
    "from their own address with the code anywhere in that first message. The seat "
    "then welcomes {name} by name and answers them from then on.\n\n"
    "Give the code only to {name}. SMD keeps only a one-way fingerprint of it, so this "
    "email is the only copy. If it is lost or sent to the wrong person, ask SMD to "
    "revoke code {code_id}.\n"
)
#: The closed set of authentication reasons the router may name (sender_auth).
_AUTH_REASONS = frozenset(
    {
        "dmarc_pass_aligned",
        "dkim_pass_aligned",
        "tenant_authenticated",
        "no_authentication_results",
        "unparseable_from",
        "no_aligned_pass",
        "authentication_results_unreadable",
    }
)


def _audit(broker: BrokerContext, meta: dict[str, Any]) -> str:
    if broker.ledger is None:
        return ""
    return broker.ledger.append(
        {
            "action_type": AUDIT_TYPE,
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
        }
    )


def _admins(customer_path: Any) -> frozenset[str]:
    try:
        import yaml

        data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
        scope = data.get("scope") or {}
        return frozenset(normalize_address(a) for a in scope.get("admins") or [] if normalize_address(a))
    except Exception:  # noqa: BLE001 - an unreadable admin list names nobody
        return frozenset()


def redeem_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("sender_enrollment_redeem requires a 'payload' object")
    message_id = str(payload.get("message_id") or "").strip()
    claimed = normalize_address(payload.get("sender"))
    authenticated = payload.get("authenticated") is True
    reason = payload.get("auth_reason")
    code = se.normalize_code(payload.get("code"))
    meta: dict[str, Any] = {"message_id": message_id}
    if isinstance(reason, str) and reason in _AUTH_REASONS:
        meta["auth_reason"] = reason

    def done(outcome: str, sender: str, attempt: se.Attempt | None = None) -> dict[str, Any]:
        row = {**meta, "outcome": outcome, "sender_key": sender_key(sender) or ""}
        if attempt is not None and attempt.code_id:
            row["code_id"] = attempt.code_id
            row["label"] = attempt.label
        _audit(broker, row)
        enrolled = outcome == se.ENROLLED
        out: dict[str, Any] = {"ok": True, "outcome": outcome, "enrolled": enrolled}
        if enrolled and attempt is not None:
            out["label"] = attempt.label
            out["address"] = sender
            out["code_id"] = attempt.code_id
            if attempt.name:
                out["name"] = attempt.name
        return out

    if not se.enabled_for(broker.customer_path):
        return done(se.DISABLED, claimed)
    if not message_id or not claimed or code is None:
        raise ValueError("sender_enrollment_redeem requires message_id, sender and a code-shaped code")
    if broker.agentmail is None:
        raise ValueError("enrollment needs the AgentMail channel on this broker")
    store = se.EnrollmentStore()
    source = broker.agentmail.enrollment_source(message_id)
    sender = str(source["sender"])
    if sender != claimed:
        store.record(claimed, se.SENDER_MISMATCH)
        return done(se.SENDER_MISMATCH, claimed)
    if not any(se.normalize_code(c) == code for t in source["texts"] for c in se.find_codes(t)):
        store.record(sender, se.NOT_IN_MESSAGE)
        return done(se.NOT_IN_MESSAGE, sender)
    if authored_policy(broker.customer_path).allows_reply_to(sender) or sender in _admins(broker.customer_path):
        store.record(sender, se.ALREADY_LISTED)
        return done(se.ALREADY_LISTED, sender)
    attempt = store.redeem(sender=sender, code=code, authenticated=authenticated)
    return done(attempt.outcome, sender, attempt)


def _int(value: Any, default: int, low: int, high: int, what: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{what} must be a whole number from {low} to {high}")
    return value


def issue_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """An administrator asked the guide for a code for a named person.

    Re-derived here, whatever the gateway said: the seat switches enrollment
    on; the message the request answers was sent, per the mailbox itself, by
    the address the gateway named, and that address is on ``scope.admins``
    exactly; the name is a plain name. Then the code is minted, the one message
    that carries it is sent as a reply in that administrator's thread through
    the fenced transmit path, and the result names only the code's id. A send
    that fails revokes the code, so no code exists that nobody received."""
    from .transmit_verbs import dispatch_transmit
    from .agentmail_ops import AgentMailRefused, AgentMailTransportError

    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("sender_enrollment_issue requires a 'payload' object")
    if not se.enabled_for(broker.customer_path):
        raise PermissionError("this seat does not issue invite codes")
    message_id = str(payload.get("message_id") or "").strip()
    admin = normalize_address(payload.get("admin"))
    name = payload.get("name")
    if not se.valid_name(name):
        raise ValueError("name: plain letters, spaces, apostrophes, hyphens and periods, at most 60")
    days = _int(payload.get("days"), DEFAULT_DAYS, 1, 90, "days")
    uses = _int(payload.get("max_uses"), DEFAULT_MAX_USES, 1, 100, "max_uses")
    if not message_id or not admin:
        raise ValueError("sender_enrollment_issue requires the message_id and the administrator")
    if broker.agentmail is None or broker.ledger is None:
        raise ValueError("issuing a code needs the AgentMail channel and the audit ledger")
    if admin not in _admins(broker.customer_path):
        raise PermissionError("only an administrator of this seat may ask for an invite code")
    if str(broker.agentmail.enrollment_source(message_id)["sender"]) != admin:
        raise PermissionError("that message was not sent by the administrator named")
    store = se.EnrollmentStore()
    code, code_id = store.issue(label=ISSUED_LABEL, days=days, max_uses=uses, name=str(name))
    expires = next(r.expires_at for r in store.codes() if r.code_id == code_id)
    text = _ISSUED_TEXT.format(
        name=name,
        code=code,
        expires=expires.replace("T", " ").replace("Z", ""),
        uses="one person" if uses == 1 else f"up to {uses} people",
        inbox=broker.agentmail.inbox_id(),
        code_id=code_id,
    )
    send_request = {
        "payload": {"message_id": message_id, "text": text},
        "anchor": {"kind": "agentmail_message", "message_id": message_id},
    }
    try:
        dispatch_transmit(
            broker,
            "agentmail_reply",
            send_request,
            send=broker.agentmail.send,
            reply=broker.agentmail.reply,
            refused=AgentMailRefused,
            transport=AgentMailTransportError,
            attempted_for_send=lambda _p: [],
            identity_key="inbox_id",
            channel="agentmail",
        )
    except Exception:
        store.revoke(code_id)
        _audit_issued(broker, admin, code_id, days, uses, sent=False)
        raise
    _audit_issued(broker, admin, code_id, days, uses, sent=True)
    return {"ok": True, "code_id": code_id, "sent": True, "expires_at": expires}


def _audit_issued(broker: BrokerContext, admin: str, code_id: str, days: int, uses: int, *, sent: bool) -> None:
    if broker.ledger is None:
        return
    meta = {
        "admin_key": sender_key(admin) or "",
        "code_id": code_id,
        "days": days,
        "max_uses": uses,
        "sent": sent,
    }
    broker.ledger.append(
        {
            "action_type": ISSUED_TYPE,
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
        }
    )


__all__ = ["AUDIT_TYPE", "ISSUED_TYPE", "VERBS", "issue_verb", "redeem_verb"]
