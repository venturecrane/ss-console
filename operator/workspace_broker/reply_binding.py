"""The verified reply binding: a turn that no inbound email opened may answer one
earlier email, once, and only to the person who sent it.

WHY IT EXISTS (2026-10-06). The reply lane (overlay ``hermes-smd-reply``) is keyed
on the inbound message that opened the turn. A job's completion turn (a webhook
handoff) and a one-shot cron turn have no such message, so a chronology package
could be delivered and its requester never told in her thread: every operator
chronology thread was two messages, the request and "queued". The fix cannot be
"let the agent name a message id and reply to it", because then the agent picks
the recipient. So the broker, which the agent cannot steer, decides:

* the message is a received message in the operator mailbox's INBOX, not a
  draft, not sent by this mailbox, and with no ``replyTo`` naming anyone other
  than its sender (Graph's ``/reply`` goes to ``replyTo``); see msgraph_lookup;
* its sender may be replied to (``scope.inbound_allow_from``), re-read from
  customer.yaml at bind time AND again at send time;
* it has not been answered, and is not answered twice:
    - a demand job: the job has ended (delivered, held or failed), the email is
      the job's ``request_ref`` and its sender the job's ``requester``; one reply
      per (job, ending), so a job that fails, is resumed and delivers can still
      say it delivered;
    - a bare message: the inbound turn that received it left an
      ``INBOUND_RECEIVED`` row and no ``REPLY_SENT`` row, it arrived within
      ``RECENCY_DAYS``, and nothing in its conversation was sent from this
      mailbox since it arrived (Sent Items, so a reply by any path counts);
* the reply goes out through ``MsGraphOps.reply`` on the Graph id the broker
  resolved, so Graph derives the recipient from that email. Nobody names it.

THE CLAIM IS TWO-PHASE (bound_replies.py). It is taken immediately before the
POST and nowhere earlier, so every refusal and lookup failure before it spends
nothing (there is no claim to release); a POST that
fails in transport keeps the claim (the reply may have gone) and records
``unknown`` for a person; a POST that returns records ``sent``. The ordinary
reply verb refuses an email that has had a bound reply, so a held inbound reply
released later cannot answer it a second time.

Every verdict, bound or refused, writes a ``REPLY_BINDING`` audit row.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import bound_replies, msgraph_lookup
from .broker_context import BrokerContext
from .demand_ledger import DemandLedger
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import authored_policy, normalize_address, sender_key
from .transmit_verbs import dispatch_transmit

KINDS = ("demand_job", "message")
REPLYABLE_DEMAND_STATES = frozenset({"delivered", "held", "failed"})
#: How old an email a bare message binding may still answer.
RECENCY_DAYS = 14
AUDIT_TYPE = "REPLY_BINDING"
_JOB_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
#: Graph ids are URL-safe base64; no '/' or '+', which could restructure a path.
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")


class BindingRefused(MsGraphRefused):
    """The broker will not let a reply bind. The message is the sentence to relay."""


@dataclass(frozen=True)
class Verified:
    kind: str
    key: str
    job_id: str
    internet_message_id: str
    graph_message_id: str
    sender: str
    conversation_id: str


def _ops(broker: BrokerContext) -> MsGraphOps:
    if broker.msgraph is None or broker.ledger is None or not broker.audit_db_path:
        raise ValueError(
            "the reply binding needs the msgraph transmit and the audit ledger "
            "(SMD_MSGRAPH_CREDENTIAL_PATH, SMD_AUDIT_DB_PATH)"
        )
    return broker.msgraph


def _demand_ledger(broker: BrokerContext) -> DemandLedger:
    # Read-only here; the queue dir is never written on this path.
    queue = os.environ.get("SMD_DEMAND_QUEUE_DIR") or "/run/smd-medchron/demand-queue"
    return DemandLedger(str(broker.audit_db_path), queue)


def _parse(raw: Any) -> tuple[str, str]:
    """(kind, identifier), where kind is demand_job, message or message_graph."""
    if not isinstance(raw, dict):
        raise BindingRefused("a reply binding names a demand job or an email")
    kind = raw.get("kind")
    if kind == "demand_job":
        job_id = str(raw.get("job_id") or "").strip()
        if set(raw) != {"kind", "job_id"} or not _JOB_ID.match(job_id):
            raise BindingRefused("a demand_job binding carries exactly the job id")
        return kind, job_id
    if kind == "message":
        if set(raw) == {"kind", "graph_message_id"}:
            gid = str(raw.get("graph_message_id") or "").strip()
            if not _GRAPH_ID.match(gid):
                raise BindingRefused("graph_message_id must be the email's Graph id")
            return "message_graph", gid
        imid = str(raw.get("internet_message_id") or "").strip()
        if set(raw) != {"kind", "internet_message_id"} or not _MESSAGE_ID.match(imid):
            raise BindingRefused(
                "a message binding carries exactly the email's internetMessageId or its graph_message_id"
            )
        return kind, f"<{imid.strip('<>')}>"
    raise BindingRefused(f"a reply binding's kind is one of {list(KINDS)}")


def _inbound_turn_did_not_reply(db_path: str, graph_message_id: str, since: str) -> str | None:
    """None when the ledger shows the email arrived and was never answered;
    otherwise the refusal sentence. Read from the broker's own audit log."""
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT action_type, metadata FROM audit_log WHERE ts >= ? AND action_type IN "
                "('INBOUND_RECEIVED','REPLY_SENT')",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return "the audit log could not be read, so whether that email was answered is unknown"
    received = answered = False
    for action_type, metadata in rows:
        try:
            meta = json.loads(metadata or "{}")
        except ValueError:
            continue
        if action_type == "INBOUND_RECEIVED" and meta.get("vendor_message_id") == graph_message_id:
            received = True
        if action_type == "REPLY_SENT" and graph_message_id in (meta.get("in_reply_to"), meta.get("message_id")):
            answered = True
    if answered:
        return "the audit log shows that email was already answered"
    if not received:
        return f"the audit log holds no record of that email arriving in the last {RECENCY_DAYS} days"
    return None


def verify(broker: BrokerContext, raw: Any, *, now: datetime | None = None) -> Verified:
    """Every check, fresh, from the mailbox, the ledgers and the live customer.yaml."""
    ops = _ops(broker)
    db_path = str(broker.audit_db_path)
    kind, ident = _parse(raw)
    job_id, expected_sender, imid, key = "", "", "", ""
    if kind == "demand_job":
        row = _demand_ledger(broker).read(ident)
        if row is None:
            raise BindingRefused("there is no demand job with that id")
        if row["state"] not in REPLYABLE_DEMAND_STATES:
            raise BindingRefused(f"demand job {ident} has not ended (it is {row['state']}); its reply waits for that")
        key = f"demand_job:{ident}:{row['state']}"
        if bound_replies.claimed(db_path, key):
            raise BindingRefused(f"demand job {ident} has already had its reply for this outcome")
        job_id, imid, expected_sender = ident, f"<{str(row['request_ref']).strip('<>')}>", row["requester"]
    if kind == "message_graph":
        found = msgraph_lookup.received_by_graph_id(ops, ident)
    else:
        found = msgraph_lookup.find_received(ops, imid or ident)
    if found is None or not found["graph_message_id"]:
        raise BindingRefused("that email is not in this seat's Inbox as a received message")
    sender = found["sender"]
    if not authored_policy(broker.customer_path).allows_reply_to(sender):
        raise BindingRefused(
            "the sender of that email is not someone this seat may reply to (scope.inbound_allow_from)"
        )
    if kind == "demand_job":
        if sender != normalize_address(expected_sender):
            raise BindingRefused("that email's sender is not the person who requested the job")
    else:
        imid = found["internet_message_id"]
        key = f"message:{imid}"
        if bound_replies.claimed(db_path, key):
            raise BindingRefused("that email has already had its one bound reply")
        moment = now or datetime.now(timezone.utc)
        since = (moment - timedelta(days=RECENCY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not found["received_at"] or found["received_at"] < since:
            raise BindingRefused(
                f"that email is older than {RECENCY_DAYS} days; a bound reply answers recent mail only"
            )
        why = _inbound_turn_did_not_reply(db_path, found["graph_message_id"], since)
        if why:
            raise BindingRefused(why)
        if msgraph_lookup.sent_in_conversation_since(ops, found["conversation_id"], found["received_at"]):
            raise BindingRefused("that email has already been answered from this mailbox")
        kind = "message"
    return Verified(
        kind=kind,
        key=key,
        job_id=job_id,
        internet_message_id=imid,
        graph_message_id=found["graph_message_id"],
        sender=sender,
        conversation_id=found["conversation_id"],
    )


def _audit(broker: BrokerContext, outcome: str, raw: Any, *, v: Verified | None = None, reason: str = "") -> None:
    """One REPLY_BINDING row per verdict. Ids and a hashed sender, never a body."""
    if broker.ledger is None:
        return
    binding = raw if isinstance(raw, dict) else {}
    meta: dict[str, Any] = {
        "outcome": outcome,
        "kind": str(binding.get("kind") or ""),
        **({"job_id": binding["job_id"]} if isinstance(binding.get("job_id"), str) else {}),
    }
    if v is not None:
        meta.update(
            {
                "binding_key": v.key,
                "graph_message_id": v.graph_message_id,
                "internet_message_id": v.internet_message_id,
                "sender_key": sender_key(v.sender),
            }
        )
    if reason:
        meta["reason"] = reason[:300]
    broker.ledger.append(
        {
            "action_type": AUDIT_TYPE,
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
        }
    )


def _verified_or_audited(broker: BrokerContext, raw: Any, outcome_prefix: str) -> Verified:
    try:
        return verify(broker, raw)
    except MsGraphRefused as exc:
        _audit(broker, f"{outcome_prefix}_refused", raw, reason=str(exc))
        if isinstance(exc, BindingRefused):
            raise
        raise BindingRefused(str(exc)) from exc
    except MsGraphTransportError as exc:
        # The mailbox could not be read. Not a refusal (nothing was decided),
        # but still a verdict someone may need to find: recorded, then raised.
        _audit(broker, f"{outcome_prefix}_error", raw, reason=str(exc))
        raise


def bind_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Check a binding without sending. A refusal is a verdict, not an error,
    so the agent can relay the reason; a transport fault still raises."""
    raw = request.get("binding")
    try:
        v = _verified_or_audited(broker, raw, "bind")
    except BindingRefused as exc:
        return {"ok": True, "bound": False, "reason": str(exc)}
    _audit(broker, "bound", raw, v=v)
    return {
        "ok": True,
        "bound": True,
        "kind": v.kind,
        "job_id": v.job_id,
        "internet_message_id": v.internet_message_id,
        # The overlay records this as the reply's in_reply_to.
        "graph_message_id": v.graph_message_id,
        # The person the reply will reach. The overlay locks the draft to it
        # and runs its floors against it; it cannot change it.
        "sender": v.sender,
        "conversation_id": v.conversation_id,
    }


def reply_verb(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Verify again, claim at the POST, reply through the audited transmit."""
    ops = _ops(broker)
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"{action} requires a 'payload' object")
    raw = request.get("binding")
    v = _verified_or_audited(broker, raw, "send")
    db_path = str(broker.audit_db_path)
    session_id = str(request.get("session_id") or "").strip()
    state: dict[str, Any] = {"claimed": False, "claimed_at": ""}
    original = ops._request

    def claiming_request(path: str, method: str, body: Any, **kw: Any) -> dict[str, Any]:
        # The claim rides the FIRST POST and nothing earlier: every GET and
        # every refusal before it leaves the one reply unspent.
        if method == "POST" and not state["claimed"]:
            if not bound_replies.claim(db_path, v.key, v.graph_message_id, v.internet_message_id, session_id):
                raise BindingRefused("that reply was already sent")
            state["claimed"] = True
            state["claimed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return original(path, method, body, **kw)

    # A per-call copy, so the claim hook never touches the shared ops object
    # another broker thread is using.
    bound_ops = copy.copy(ops)
    bound_ops._request = claiming_request  # type: ignore[method-assign]
    body = {
        "message_id": v.graph_message_id,
        "comment": str(payload.get("comment") or ""),
        **({"html": payload["html"]} if isinstance(payload.get("html"), str) and payload["html"].strip() else {}),
    }
    extra = request.get("audit_extra")
    audit_extra = dict(extra) if isinstance(extra, dict) else {}
    audit_extra["reply_binding"] = v.key
    try:
        result = dispatch_transmit(
            broker,
            action,
            {**request, "payload": body, "audit_extra": audit_extra},
            send=bound_ops.reply,
            reply=bound_ops.reply,
            refused=MsGraphRefused,
            transport=MsGraphTransportError,
            attempted_for_send=lambda _payload: [v.sender],
            identity_key="mailbox",
        )
    except Exception as exc:
        # Nothing to release for a failure before the POST: the claim is taken
        # only AT the POST, so such a failure never took one. A claim that was
        # taken means the POST was attempted, and the reply may have gone.
        if state["claimed"]:
            _settle_failed_post(broker, ops, v, raw, exc, str(state["claimed_at"]))
        raise
    bound_replies.settle(db_path, v.key, "sent")
    if v.kind == "demand_job":
        _demand_ledger(broker).mark_replied(v.job_id)
    return result


def _settle_failed_post(
    broker: BrokerContext, ops: MsGraphOps, v: Verified, raw: Any, exc: Exception, claimed_at: str
) -> None:
    """A POST that failed. A 4xx is Graph refusing the request, which sends
    nothing; if Sent Items also shows nothing in the conversation since the
    claim, the claim is RELEASED so the one reply is not silently spent. Every
    other failure, and a 4xx whose outcome cannot be confirmed, stays
    ``unknown`` for a person: a second email to a client is the worse error."""
    db_path = str(broker.audit_db_path)
    status = getattr(exc, "status", None)
    if isinstance(status, int) and 400 <= status < 500 and claimed_at:
        try:
            delivered = msgraph_lookup.sent_in_conversation_since(ops, v.conversation_id, claimed_at)
        except (MsGraphTransportError, MsGraphRefused):
            delivered = True
        if not delivered:
            bound_replies.release(db_path, v.key)
            _audit(
                broker, "send_released", raw, v=v, reason=f"Graph refused the reply (HTTP {status}); nothing was sent"
            )
            return
    bound_replies.settle(db_path, v.key, "unknown")
    _audit(broker, "send_unknown", raw, v=v, reason=str(exc))


VERBS: tuple[str, ...] = ("msgraph_reply_bind", "msgraph_reply_bound")

__all__ = ["AUDIT_TYPE", "KINDS", "VERBS", "BindingRefused", "Verified", "bind_verb", "reply_verb", "verify"]
