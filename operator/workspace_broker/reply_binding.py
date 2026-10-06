"""The verified reply binding: a turn that no inbound email opened may answer one
earlier email, once, and only to the person who sent it.

WHY IT EXISTS (2026-10-06). The reply lane (overlay ``hermes-smd-reply``) is keyed
on the inbound message that opened the turn. A job's completion turn (a webhook
handoff) and a one-shot cron turn have no such message, so a chronology package
could be delivered and its requester never told in her thread: every operator
chronology thread was two messages, the request and "queued". The fix cannot be
"let the agent name a message id and reply to it", because then the agent picks
the recipient. So the broker, which the agent cannot steer, decides:

* the message must EXIST in the operator mailbox as a received message (not a
  draft; anything holding ``Mail.ReadWrite`` can write a draft with any From);
* its sender, as the MAILBOX recorded it, must be one this seat may reply to
  (``scope.inbound_allow_from``, the rule every reply already obeys), re-read
  from customer.yaml at bind time AND again at send time;
* it must not have been answered: for a demand job the ledger's own
  ``reply_sent_at`` compare-and-set (one completion reply per job, durable); for
  a bare message, a row in ``bound_replies`` here AND nothing sent by this
  mailbox in that conversation since the message arrived (Sent Items, so a reply
  by any path counts);
* the reply goes out through ``MsGraphOps.reply`` on the Graph id the broker
  resolved, so the recipient is derived by Graph from that message. Nobody
  names it, and the agent never can.

TWO KINDS OF BINDING (``kind``):

``demand_job``  ``{"kind": "demand_job", "job_id": ...}``. The completion reply
                to the request that queued the job. The message is the job row's
                ``request_ref``, its sender must equal the row's ``requester``
                (which the overlay set from the verified inbound, never from the
                model), and the job must have ended (delivered, held or failed):
                the request turn already acknowledged it, so this binding is the
                one reply that says how it ended.
``message``     ``{"kind": "message", "internet_message_id": "<...>"}`` or
                ``{"kind": "message", "graph_message_id": "AAMk..."}`` (the id a
                gateway turn is handed). One reply to an email that has had none:
                the one-shot answer to a message whose turn failed to reply. The
                claim is keyed on the RFC 5322 id the MAILBOX returns for either
                form, so the two spellings of one email share one claim.

ORDER AT SEND: verify, claim, send. The claim is taken BEFORE the transmit, so a
transport failure after it leaves the binding spent rather than risking a second
email to a client. A spent binding whose send failed is on the CONFIRM_SEND_FAILED
row for a person to see; that is the right failure direction for client mail.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit_ledger import _iso_utc
from .broker_context import BrokerContext
from . import msgraph_lookup
from .demand_ledger import DemandLedger
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import authored_policy, normalize_address
from .transmit_verbs import dispatch_transmit

KINDS = ("demand_job", "message")
#: A demand job may be answered only once it has ended.
REPLYABLE_DEMAND_STATES = frozenset({"delivered", "held", "failed"})
_JOB_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
_GRAPH_ID = re.compile(r"^[A-Za-z0-9+/=_-]{16,512}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS bound_replies ("
    "binding_key TEXT PRIMARY KEY, "
    "internet_message_id TEXT NOT NULL, "
    "claimed_at TEXT NOT NULL, "
    "session_id TEXT"
    ")"
)


class BindingRefused(ValueError):
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


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(CREATE_SQL)
    return conn


def _claimed(db_path: str, key: str) -> bool:
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT 1 FROM bound_replies WHERE binding_key=?", (key,)).fetchone() is not None
    finally:
        conn.close()


def _claim_message(db_path: str, key: str, imid: str, session_id: str) -> bool:
    """True exactly once per key: the PRIMARY KEY is the compare-and-set."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO bound_replies (binding_key, internet_message_id, claimed_at, session_id) "
            "VALUES (?,?,?,?)",
            (key, imid, _iso_utc(), session_id or None),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _demand_ledger(broker: BrokerContext) -> DemandLedger:
    # The queue dir is never written on this path (read + mark_replied only);
    # the runner's entrypoint exports it, and an unset one names a path that
    # this code does not touch.
    queue = os.environ.get("SMD_DEMAND_QUEUE_DIR") or "/run/smd-medchron/demand-queue"
    return DemandLedger(str(broker.audit_db_path), queue)


def _parse(raw: Any) -> tuple[str, str]:
    """(kind, identifier) from the caller's binding, or BindingRefused."""
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


def verify(broker: BrokerContext, raw: Any) -> Verified:
    """Every check, fresh, from the mailbox, the ledger and the live customer.yaml."""
    ops = _ops(broker)
    kind, ident = _parse(raw)
    job_id, expected_sender, imid = "", "", ""
    if kind == "demand_job":
        row = _demand_ledger(broker).read(ident)
        if row is None:
            raise BindingRefused("there is no demand job with that id")
        if row["state"] not in REPLYABLE_DEMAND_STATES:
            raise BindingRefused(f"demand job {ident} has not ended (it is {row['state']}); its reply waits for that")
        if row.get("reply_sent_at"):
            raise BindingRefused(f"demand job {ident} has already had its reply")
        job_id, imid, expected_sender = ident, f"<{str(row['request_ref']).strip('<>')}>", row["requester"]
        key = f"demand_job:{ident}"
    try:
        if kind == "message_graph":
            found = msgraph_lookup.received_by_graph_id(ops, ident)
        else:
            found = msgraph_lookup.find_received(ops, imid or ident)
    except MsGraphRefused as exc:
        raise BindingRefused(str(exc)) from exc
    if found is None or not found["graph_message_id"]:
        raise BindingRefused("that email is not in this seat's mailbox as a received message")
    if kind != "demand_job":
        # Either spelling of the email keys ONE claim: the mailbox's own id.
        kind = "message"
        imid = found["internet_message_id"]
        key = f"message:{imid}"
        if _claimed(str(broker.audit_db_path), key):
            raise BindingRefused("that email has already had its one bound reply")
    sender = found["sender"]
    if not authored_policy(broker.customer_path).allows_reply_to(sender):
        raise BindingRefused(
            "the sender of that email is not someone this seat may reply to (scope.inbound_allow_from)"
        )
    if kind == "demand_job" and sender != normalize_address(expected_sender):
        raise BindingRefused("that email's sender is not the person who requested the job")
    if kind == "message" and msgraph_lookup.sent_in_conversation_since(
        ops, found["conversation_id"], found["received_at"]
    ):
        raise BindingRefused("that email has already been answered from this mailbox")
    return Verified(
        kind=kind,
        key=key,
        job_id=job_id,
        internet_message_id=imid,
        graph_message_id=found["graph_message_id"],
        sender=sender,
        conversation_id=found["conversation_id"],
    )


def bind_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Check a binding without sending. A refusal is a verdict, not an error,
    so the agent can relay the reason; a transport fault still raises."""
    try:
        v = verify(broker, request.get("binding"))
    except BindingRefused as exc:
        return {"ok": True, "bound": False, "reason": str(exc)}
    return {
        "ok": True,
        "bound": True,
        "kind": v.kind,
        "job_id": v.job_id,
        "internet_message_id": v.internet_message_id,
        # The person the reply will reach. The overlay locks the draft to it
        # and runs its floors against it; it cannot change it.
        "sender": v.sender,
        "conversation_id": v.conversation_id,
    }


def reply_verb(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Verify again, claim once, then reply through the audited transmit."""
    ops = _ops(broker)
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"{action} requires a 'payload' object")
    v = verify(broker, request.get("binding"))
    session_id = str(request.get("session_id") or "").strip()
    db_path = str(broker.audit_db_path)
    if v.kind == "demand_job":
        claimed = _demand_ledger(broker).mark_replied(v.job_id)
    else:
        claimed = _claim_message(db_path, v.key, v.internet_message_id, session_id)
    if not claimed:
        raise BindingRefused("that reply was already sent")
    body = {
        "message_id": v.graph_message_id,
        "comment": str(payload.get("comment") or ""),
        **({"html": payload["html"]} if isinstance(payload.get("html"), str) and payload["html"].strip() else {}),
    }
    return dispatch_transmit(
        broker,
        action,
        {**request, "payload": body},
        send=ops.reply,
        reply=ops.reply,
        refused=MsGraphRefused,
        transport=MsGraphTransportError,
        attempted_for_send=lambda _payload: [v.sender],
        identity_key="mailbox",
    )


VERBS: tuple[str, ...] = ("msgraph_reply_bind", "msgraph_reply_bound")

__all__ = ["KINDS", "VERBS", "BindingRefused", "Verified", "bind_verb", "reply_verb", "verify"]
