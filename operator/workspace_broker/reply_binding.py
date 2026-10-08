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
    - a demand job: the job has ended (delivered or held; never failed, which is
      SMD's to resolve), the email is
      the job's ``request_ref`` and its sender the job's ``requester``; one reply
      per (job, ending), so a job that fails, is resumed and delivers can still
      say it delivered;
    - a drafting job: exactly the demand job's rules, on the drafting ledger;
    - a litigation status job (2026-10-07): a REQUESTED job takes the drafting
      rules on the litigation ledger (a reply in the requester's thread, never
      a new message). A SCHEDULED job has no request email, so it is the one
      job binding that sends ONE NEW email: to the job's requester, who must
      still be a Named Administrator and the first authored
      ``scheduled_recipients`` entry (re-read at bind time and at send time),
      under the fixed subject ``scheduled_subject`` composes. Nobody names the
      recipient or the subject, the model least of all;
    - a chronology job (2026-10-07): the same rules on the medchron ledger, one
      reply per (job, outcome). That ledger has no attempt counter, so a job
      held, resumed and held AGAIN gets no second hold reply: the requester
      already has one, and SMD's shortfall alert carries the rest;
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
from .drafting_ledger import DraftingLedger
from .litigation_ledger import LitigationLedger
from .medchron_ledger import MedchronLedger
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import authored_policy, normalize_address, sender_key
from .transmit_verbs import dispatch_transmit

KINDS = ("demand_job", "drafting_job", "litigation_job", "medchron_job", "message")
#: The job kinds: each binds on its own ledger with the same rules.
JOB_KINDS = frozenset({"demand_job", "drafting_job", "litigation_job", "medchron_job"})
#: The kinds whose ledger carries its own reply mark (``mark_replied``). The
#: medchron ledger predates it; its once-only rests on the bound_replies claim.
_LEDGER_MARKED_KINDS = frozenset({"demand_job", "drafting_job", "litigation_job"})
_NOUN = {
    "demand_job": "demand",
    "drafting_job": "drafting",
    "litigation_job": "litigation status",
    "medchron_job": "chronology",
}
#: How a binding reaches its person: a reply in their thread, or (a scheduled
#: litigation job only) one new email to them. The overlay's scheduled wake
#: sends that one email with smd_send_message under the same fixed subject
#: (hermes-smd-reply binding.py SCHEDULED_SUBJECT_PREFIX); this mode is the
#: broker-held equivalent, for a caller that binds instead.
MODE_REPLY = "reply"
MODE_NEW_MESSAGE = "new_message"
#: The outcomes a requester is told about, for EVERY job kind (demand and
#: drafting alike; the name predates the drafting lane). NOT ``failed``: a
#: failed job is resumable and SMD's to resolve (a live demand job, 2026-10-06,
#: told the firm to narrow its request after our own stage failed), so its
#: reply is refused here and the failure goes to SMD's shortfall alert instead.
REPLYABLE_DEMAND_STATES = frozenset({"delivered", "held"})
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
    mode: str = MODE_REPLY
    subject: str = ""


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


def _drafting_ledger(broker: BrokerContext) -> DraftingLedger:
    # Read-only here; the queue dir is never written on this path.
    queue = os.environ.get("SMD_DRAFTING_QUEUE_DIR") or "/run/smd-medchron/drafting-queue"
    return DraftingLedger(str(broker.audit_db_path), queue)


def _litigation_ledger(broker: BrokerContext) -> LitigationLedger:
    # Read-only here; the queue dir is never written on this path.
    queue = os.environ.get("SMD_LITIGATION_QUEUE_DIR") or "/run/smd-medchron/litigation-queue"
    return LitigationLedger(str(broker.audit_db_path), queue)


def scheduled_subject(request_ref: str) -> str:
    """The fixed subject of a scheduled litigation run's one email, from its
    ``scheduled:<YYYY-MM-DD>`` ref."""
    try:
        day = datetime.strptime(request_ref.split(":", 1)[1], "%Y-%m-%d")
    except (IndexError, ValueError) as exc:
        raise BindingRefused("that scheduled job's date cannot be read; nothing was sent") from exc
    return f"Litigation status list, {day.date().isoformat()}"


def _scheduled_recipient_ok(broker: BrokerContext, requester: str) -> bool:
    """The scheduled job's requester is still a Named Administrator AND the
    first authored ``scheduled_recipients`` entry, read from the live yaml."""
    from .litigation_intake import scheduled_recipients, settings_of, skill_entry
    from .medchron_ledger import admins_from_customer_yaml

    who = normalize_address(requester)
    recipients = scheduled_recipients(settings_of(skill_entry(broker.customer_path)))
    admins = admins_from_customer_yaml(str(broker.customer_path)) or ()
    return bool(recipients) and recipients[0] == who and who in admins


def _medchron_ledger(broker: BrokerContext) -> MedchronLedger:
    # Read-only here; the queue dir is never written on this path.
    queue = os.environ.get("SMD_MEDCHRON_QUEUE_DIR") or "/run/smd-medchron/queue"
    return MedchronLedger(str(broker.audit_db_path), queue)


def _job_ledger(broker: BrokerContext, kind: str) -> DemandLedger | DraftingLedger | LitigationLedger | MedchronLedger:
    if kind == "medchron_job":
        return _medchron_ledger(broker)
    return _marking_ledger(broker, kind)


def _marking_ledger(broker: BrokerContext, kind: str) -> DemandLedger | DraftingLedger | LitigationLedger:
    """The ledger of a kind in ``_LEDGER_MARKED_KINDS`` (it carries mark_replied)."""
    if kind == "litigation_job":
        return _litigation_ledger(broker)
    return _drafting_ledger(broker) if kind == "drafting_job" else _demand_ledger(broker)


def _parse(raw: Any) -> tuple[str, str]:
    """(kind, identifier), where kind is a job kind, message or message_graph."""
    if not isinstance(raw, dict):
        raise BindingRefused(
            "a reply binding names a demand, drafting, litigation status or chronology job, or an email"
        )
    kind = raw.get("kind")
    if kind in JOB_KINDS:
        job_id = str(raw.get("job_id") or "").strip()
        if set(raw) != {"kind", "job_id"} or not _JOB_ID.match(job_id):
            raise BindingRefused(f"a {kind} binding carries exactly the job id")
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


def _verify_job(broker: BrokerContext, kind: str, ident: str, db_path: str) -> tuple[dict[str, Any], str]:
    """A job binding's ledger checks: (the row, its reply key), or refused."""
    noun = _NOUN[kind]
    row = _job_ledger(broker, kind).read(ident)
    if row is None:
        raise BindingRefused(f"there is no {noun} job with that id")
    if row["state"] == "failed":
        raise BindingRefused(
            f"{noun} job {ident} failed on SMD's side; the requester is told nothing until it is "
            "delivered or held, and SMD has been alerted. Send nothing to anyone."
        )
    if row["state"] not in REPLYABLE_DEMAND_STATES:
        raise BindingRefused(f"{noun} job {ident} has not ended (it is {row['state']}); its reply waits for that")
    # One reply per (attempt, outcome): a resumed job (attempt + 1) that
    # ends again is owed a reply for its new outcome.
    key = f"{kind}:{ident}:{row.get('attempt') or 1}:{row['state']}"
    if bound_replies.claimed(db_path, key):
        raise BindingRefused(f"{noun} job {ident} has already had its reply for this outcome")
    return row, key


def _verify_scheduled(broker: BrokerContext, kind: str, ident: str, key: str, row: dict[str, Any]) -> Verified:
    """A scheduled litigation job has no request email: ONE new email to its
    requester, who must still be the authored scheduled recipient, or nothing."""
    if not _scheduled_recipient_ok(broker, str(row["requester"])):
        raise BindingRefused(
            f"{_NOUN[kind]} job {ident}'s recipient is no longer the authored scheduled recipient "
            "and a Named Administrator; nothing was sent"
        )
    return Verified(
        kind=kind,
        key=key,
        job_id=ident,
        internet_message_id="",
        graph_message_id="",
        sender=normalize_address(str(row["requester"])),
        conversation_id="",
        mode=MODE_NEW_MESSAGE,
        subject=scheduled_subject(str(row["request_ref"])),
    )


def _verify_bare_message(ops: MsGraphOps, db_path: str, found: dict[str, Any], now: datetime | None) -> str:
    """A bare message binding's once-only and recency checks; its reply key."""
    key = f"message:{found['internet_message_id']}"
    if bound_replies.claimed(db_path, key):
        raise BindingRefused("that email has already had its one bound reply")
    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(days=RECENCY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not found["received_at"] or found["received_at"] < since:
        raise BindingRefused(f"that email is older than {RECENCY_DAYS} days; a bound reply answers recent mail only")
    why = _inbound_turn_did_not_reply(db_path, found["graph_message_id"], since)
    if why:
        raise BindingRefused(why)
    if msgraph_lookup.sent_in_conversation_since(ops, found["conversation_id"], found["received_at"]):
        raise BindingRefused("that email has already been answered from this mailbox")
    return key


def verify(broker: BrokerContext, raw: Any, *, now: datetime | None = None) -> Verified:
    """Every check, fresh, from the mailbox, the ledgers and the live customer.yaml."""
    ops = _ops(broker)
    db_path = str(broker.audit_db_path)
    kind, ident = _parse(raw)
    job_id, expected_sender, imid, key = "", "", "", ""
    if kind in JOB_KINDS:
        row, key = _verify_job(broker, kind, ident, db_path)
        if kind == "litigation_job" and row.get("trigger") == "scheduled":
            return _verify_scheduled(broker, kind, ident, key, row)
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
    if kind in JOB_KINDS:
        if sender != normalize_address(expected_sender):
            raise BindingRefused("that email's sender is not the person who requested the job")
    else:
        key = _verify_bare_message(ops, db_path, found, now)
        imid, kind = found["internet_message_id"], "message"
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
        # "new_message" only for a scheduled litigation job: the broker sets
        # the recipient (``sender``) and this subject; the caller sends a body.
        "mode": v.mode,
        **({"subject": v.subject} if v.subject else {}),
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
            try:
                marked = v.kind not in _LEDGER_MARKED_KINDS or _marking_ledger(broker, v.kind).mark_replied(
                    v.job_id, _outcome(v)
                )
            except Exception:
                # The ledger could not be written: nothing has been sent, so
                # the one reply is given back rather than silently spent.
                bound_replies.release(db_path, v.key)
                raise
            if not marked:
                # The ledger's own compare-and-set disagrees (this outcome was
                # already replied to): honor it, give back the claim, send nothing.
                bound_replies.release(db_path, v.key)
                raise BindingRefused("the job's record shows this outcome was already replied to")
            state["claimed"] = True
            state["claimed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return original(path, method, body, **kw)

    # A per-call copy, so the claim hook never touches the shared ops object
    # another broker thread is using.
    bound_ops = copy.copy(ops)
    bound_ops._request = claiming_request  # type: ignore[method-assign]
    html = {"html": payload["html"]} if isinstance(payload.get("html"), str) and payload["html"].strip() else {}
    if v.mode == MODE_NEW_MESSAGE:
        # The recipient and subject are the broker's, never the caller's; a
        # caller's to/cc/bcc/subject/attachments are not read at all.
        body: dict[str, Any] = {
            "to": [v.sender],
            "subject": v.subject,
            "body_text": str(payload.get("comment") or ""),
            **html,
        }
        if not (body["body_text"].strip() or html):
            raise BindingRefused("refusing to send an empty message")
        transmit = bound_ops.send
    else:
        body = {"message_id": v.graph_message_id, "comment": str(payload.get("comment") or ""), **html}
        transmit = bound_ops.reply
    extra = request.get("audit_extra")
    audit_extra = dict(extra) if isinstance(extra, dict) else {}
    audit_extra["reply_binding"] = v.key
    try:
        result = dispatch_transmit(
            broker,
            action,
            {**request, "payload": body, "audit_extra": audit_extra},
            send=transmit,
            reply=transmit,
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
    return result


def _outcome(v: Verified) -> str:
    """``<attempt>:<state>`` out of a job key, the ledger's reply key."""
    return v.key.split(":", 2)[2]


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
            if v.kind in _LEDGER_MARKED_KINDS:
                _marking_ledger(broker, v.kind).unmark_replied(v.job_id, _outcome(v))
            _audit(
                broker, "send_released", raw, v=v, reason=f"Graph refused the reply (HTTP {status}); nothing was sent"
            )
            return
    bound_replies.settle(db_path, v.key, "unknown")
    _audit(broker, "send_unknown", raw, v=v, reason=str(exc))


VERBS: tuple[str, ...] = ("msgraph_reply_bind", "msgraph_reply_bound")

__all__ = [
    "AUDIT_TYPE",
    "KINDS",
    "MODE_NEW_MESSAGE",
    "MODE_REPLY",
    "VERBS",
    "BindingRefused",
    "Verified",
    "bind_verb",
    "reply_verb",
    "scheduled_subject",
    "verify",
]
