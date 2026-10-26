"""The verified reply binding: a turn that no inbound email opened may answer one
earlier email, once, and only to the person who sent it (copying the others on
that email whom this seat may answer: MsGraphOps._request_participants).

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
      a new message). A SCHEDULED job has no request email, so it sends ONE NEW
      email: to the job's requester, who must
      still be a Named Administrator and the first authored
      ``scheduled_recipients`` entry (re-read at bind time and at send time),
      under the fixed subject ``scheduled_subject`` composes. Nobody names the
      recipient or the subject, the model least of all;
    - a negotiation notice (2026-10-09): the negotiation watch's one email per
      new offer. A scheduled run has no request email, so it sends ONE NEW
      email: to the notice's requester, who must
      still be a Named Administrator and the first authored
      ``scheduled_recipients`` of ``negotiation-watch`` (re-read at bind time
      and at send time), under the subject ``negotiation_ledger.subject``
      composes. Nobody names the recipient or the subject, the model least of
      all. A negotiation JOB never binds: only its notices are announced;
    - an SMD note (2026-10-15): words SMD approved, recorded by root against a
      colleague's recent request email (``note_verbs.note_record``, which
      checked the email, its sender, its age and that every identifier in the
      words appears in it). It binds exactly as a delivered job does: the email
      is the note's ``request_ref`` (a Graph id) and its sender the note's
      ``requester``; one reply per note. Nothing about an email's earlier
      answers matters here: the note is SMD's own, recorded once. A
      NEW-MESSAGE note (2026-10-16, ``trigger='scheduled'``, ``request_ref``
      ``new:<key>``) has no request email: it sends ONE NEW email to its
      requester, who must still be a Named Administrator and ANY authored
      ``scheduled_recipients`` entry of ``smd-note`` (re-read at bind and at
      send time), under the subject the note recorded;
    - a chronology job (2026-10-07): the same rules on the medchron ledger, one
      reply per (job, outcome), and ONLY on delivery (2026-10-24, the Captain:
      the firm hears about a chronology only when it is delivered). A held
      chronology is refused its reply exactly as a failed job is; SMD was
      paged when it stopped;
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
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import bound_replies, delivery_proof, msgraph_lookup
from .broker_context import BrokerContext
from .demand_ledger import DemandLedger
from .drafting_ledger import DraftingLedger
from .litigation_ledger import LitigationLedger
from .medchron_ledger import MedchronLedger
from .negotiation_ledger import NegotiationLedger
from .negotiation_ledger import subject as negotiation_subject
from .note_ledger import NoteLedger
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import authored_policy, normalize_address, sender_key
from .casefile_verbs import after_bound_reply, casefile_ledger_of
from .reply_scheduled import scheduled_recipient_ok as _scheduled_recipient_ok
from .transmit_verbs import dispatch_transmit

KINDS = ("casefile_job", "demand_job", "drafting_job", "litigation_job", "medchron_job", "negotiation_job", "note_job", "message")  # fmt: skip
#: The job kinds: each binds on its own ledger with the same rules.
JOB_KINDS = frozenset(
    {"casefile_job", "demand_job", "drafting_job", "litigation_job", "medchron_job", "negotiation_job", "note_job"}
)
#: The kinds whose ledger carries its own reply mark (``mark_replied``). The
#: medchron ledger predates it; its once-only rests on the bound_replies claim.
_LEDGER_MARKED_KINDS = frozenset(
    {"casefile_job", "demand_job", "drafting_job", "litigation_job", "negotiation_job", "note_job"}
)
_NOUN = {
    "casefile_job": "case file",
    "demand_job": "demand",
    "drafting_job": "drafting",
    "litigation_job": "litigation status",
    "medchron_job": "chronology",
    "negotiation_job": "negotiation",
    "note_job": "SMD note",
}
#: How a binding reaches its person: a reply in their thread, or (a scheduled
#: litigation job, a negotiation notice, or a new-message SMD note) one new
#: email to them, whose recipient and subject are the broker's (the overlay's
#: scheduled wake takes only this mode).
MODE_REPLY = "reply"
MODE_NEW_MESSAGE = "new_message"
#: Each scheduled kind's authored lane (the skill whose scheduled_recipients it
#: may reach).
SCHEDULED_LANES = {
    "litigation_job": "skill:litigation-status",
    "negotiation_job": "skill:negotiation-watch",
    # 2026-10-16: a note with no request email (``note_ledger``
    # trigger='scheduled'), to an admin the smd-note skill authors.
    "note_job": "skill:smd-note",
}
#: The outcomes a requester is told about, for EVERY job kind (demand and
#: drafting alike; the name predates the drafting lane). NOT ``failed``: a
#: failed job is resumable and SMD's to resolve (a live demand job, 2026-10-06,
#: told the firm to narrow its request after our own stage failed), so its
#: reply is refused here and the failure goes to SMD's shortfall alert instead.
REPLYABLE_DEMAND_STATES = frozenset({"delivered", "held"})
#: The chronology lane's narrower set (2026-10-24, the Captain): the firm hears
#: from the Operator about a chronology ONLY when it is delivered. A held
#: chronology is SMD's to finish, paged at the hold, and its wake turn (only a
#: RUN rerouted to an UPDATE still has one) sends nothing.
REPLYABLE_MEDCHRON_STATES = frozenset({"delivered"})


def replyable_states(kind: str) -> frozenset[str]:
    """The ledger states a job of `kind` may be replied on."""
    return REPLYABLE_MEDCHRON_STATES if kind == "medchron_job" else REPLYABLE_DEMAND_STATES


logger = logging.getLogger(__name__)
#: How old an email a bare message binding may still answer.
RECENCY_DAYS = 14
AUDIT_TYPE = "REPLY_BINDING"
_JOB_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
#: Graph ids are URL-safe base64; no '/' or '+', which could restructure a path.
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")


def _is_graph_ref(ref: str) -> bool:
    """A job's ``request_ref`` that is the mailbox's Graph id, not an RFC
    Message-ID: no '@', and only the characters a Graph id is made of."""
    return "@" not in ref and bool(_GRAPH_ID.match(ref))


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
    #: The job's ledger row as verified (job kinds only): what the held
    #: wording check reads. Never a body, never sent.
    row: Any = None


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


def _negotiation_ledger(broker: BrokerContext) -> NegotiationLedger:
    # Read here; the binding marks a notice replied, never writes the queue.
    queue = os.environ.get("SMD_NEGOTIATION_QUEUE_DIR") or "/run/smd-medchron/negotiation-queue"
    return NegotiationLedger(str(broker.audit_db_path), queue)


def _medchron_ledger(broker: BrokerContext) -> MedchronLedger:
    # Read-only here; the queue dir is never written on this path.
    queue = os.environ.get("SMD_MEDCHRON_QUEUE_DIR") or "/run/smd-medchron/queue"
    return MedchronLedger(str(broker.audit_db_path), queue)


def job_ledger(broker: BrokerContext, kind: str) -> Any:
    if kind == "medchron_job":
        return _medchron_ledger(broker)
    return _marking_ledger(broker, kind)


def _marking_ledger(broker: BrokerContext, kind: str) -> Any:
    """The ledger of a kind in ``_LEDGER_MARKED_KINDS`` (it carries mark_replied)."""
    if kind == "litigation_job":
        return _litigation_ledger(broker)
    if kind == "negotiation_job":
        return _negotiation_ledger(broker)
    if kind == "note_job":
        # 2026-10-15: without this branch a note_job fell through to the demand
        # ledger, which has no such id, so every note bind read "no job".
        return NoteLedger(str(broker.audit_db_path))
    if kind == "drafting_job":
        return _drafting_ledger(broker)
    if kind == "casefile_job":
        return casefile_ledger_of(broker)
    if kind == "demand_job":
        return _demand_ledger(broker)
    raise ValueError(f"{kind} has no ledger that marks its reply")


def _parse(raw: Any) -> tuple[str, str]:
    """(kind, identifier), where kind is a job kind, message or message_graph."""
    if not isinstance(raw, dict):
        raise BindingRefused(
            "a reply binding names a case file, demand, drafting, litigation status, negotiation, chronology or "
            "SMD note job, "
            "or an email"
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


def reply_key(kind: str, ident: str, row: dict[str, Any]) -> str:
    """The bound-reply key a job's reply is claimed under. One reply per
    (attempt, outcome): a resumed job (attempt + 1) that ends again is owed a
    reply for its new outcome. Also what the runner daemon's reply watch
    matches (``delivery_proof.reply_status_verb``), so the two never disagree.

    The per-reason key a held chronology carried (2026-10-23) is gone with its
    reply (2026-10-24): a held chronology is never replied on, so it is never
    keyed."""
    return f"{kind}:{ident}:{row.get('attempt') or 1}:{row['state']}"


def _verify_job(broker: BrokerContext, kind: str, ident: str, db_path: str) -> tuple[dict[str, Any], str]:
    """A job binding's ledger checks: (the row, its reply key), or refused."""
    noun = _NOUN[kind]
    row = job_ledger(broker, kind).read(ident)
    if row is None:
        raise BindingRefused(f"there is no {noun} job with that id")
    if row["state"] == "failed":
        raise BindingRefused(
            f"{noun} job {ident} failed on SMD's side; the requester is told nothing until it is "
            f"{' or '.join(sorted(replyable_states(kind)))}, and SMD has been alerted. Send nothing to anyone."
        )
    if kind == "medchron_job" and row["state"] == "held":
        raise BindingRefused(
            f"{noun} job {ident} stopped and SMD is finishing it; the requester hears about a chronology only "
            "when it is delivered, and SMD has been alerted. Send nothing to anyone."
        )
    if row["state"] not in replyable_states(kind):
        raise BindingRefused(f"{noun} job {ident} has not ended (it is {row['state']}); its reply waits for that")
    key = reply_key(kind, ident, row)
    if bound_replies.claimed(db_path, key):
        raise BindingRefused(f"{noun} job {ident} has already had its reply for this outcome")
    return row, key


def _verify_scheduled(broker: BrokerContext, kind: str, ident: str, key: str, row: dict[str, Any]) -> Verified:
    """A scheduled run has no request email: ONE new email to its requester,
    who must still be the authored scheduled recipient, or nothing. A
    negotiation job is announced by its notices, never itself."""
    if kind == "negotiation_job":
        if not row.get("notice"):
            raise BindingRefused(
                f"{_NOUN[kind]} job {ident} is announced by its notices, never itself; nothing was sent"
            )
        subject = negotiation_subject(row)
    elif kind == "note_job":
        subject = str(row.get("subject") or "").strip()
        if not subject:
            raise BindingRefused(f"{_NOUN[kind]} job {ident} records no subject; nothing was sent")
    else:
        subject = scheduled_subject(str(row["request_ref"]))
    if not _scheduled_recipient_ok(broker, kind, str(row["requester"])):
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
        subject=subject,
        row=row,
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
    row: dict[str, Any] | None = None
    if kind in JOB_KINDS:
        row, key = _verify_job(broker, kind, ident, db_path)
        if kind in SCHEDULED_LANES and row.get("trigger") == "scheduled":
            return _verify_scheduled(broker, kind, ident, key, row)
        job_id, expected_sender = ident, row["requester"]
        ref = str(row["request_ref"] or "").strip()
        if _is_graph_ref(ref):
            # 2026-10-14: the Operator recorded a chronology request by the
            # mailbox's own Graph id (job 01M4VJK2, and the 09-22 jobs), and an
            # RFC lookup of that id can never match, so the requester could
            # never be answered in her thread. A Graph id resolves the same
            # email through the same Inbox, sender and replyTo checks.
            found = msgraph_lookup.received_by_graph_id(ops, ref)
        else:
            found = msgraph_lookup.find_received(ops, f"<{ref.strip('<>')}>")
        if found is not None:
            imid = found["internet_message_id"]
    elif kind == "message_graph":
        found = msgraph_lookup.received_by_graph_id(ops, ident)
    else:
        found = msgraph_lookup.find_received(ops, ident)
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
        row=row,
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
        # "new_message" only for a scheduled litigation job, a negotiation notice or a new-message
        # SMD note: the broker sets
        # the recipient (``sender``) and this subject; the caller sends a body.
        "mode": v.mode,
        **({"subject": v.subject} if v.subject else {}),
        # A held litigation or chronology job: the exact texts its reply may
        # be (held_wording). The send refuses any other words.
        **_held_texts(v),
    }


def _held_texts(v: Verified) -> dict[str, Any]:
    from . import held_wording  # held_wording imports BindingRefused from here

    if v.kind not in held_wording.KIND_LANE or not v.row or v.row.get("state") != "held":
        return {}
    return {"held_wording": held_wording.allowed(v.kind, v.row)}


def _check_held_wording(broker: BrokerContext, raw: Any, v: Verified, payload: dict[str, Any]) -> None:
    """A held litigation or chronology job's reply is its fixed wording
    (2026-10-24, job 01M5MWGB: the model sent its own words on a held job and
    nothing checked them). Refused BEFORE the claim, so the retry with the
    quoted text still has its one reply. The audit row names the class, never
    the body."""
    from . import held_wording, hold_class

    if v.kind not in held_wording.KIND_LANE or not v.row:
        return
    raw_html = payload.get("html")
    html = raw_html if isinstance(raw_html, str) else ""
    try:
        held_wording.check(v.kind, v.row, str(payload.get("comment") or ""), html)
    except held_wording.HeldWordingRefused:
        cls = hold_class.classify(held_wording.KIND_LANE[v.kind], v.row.get("reason"))[0]
        _audit(broker, "send_refused", raw, v=v, reason=f"held_wording: the body is not the {cls} wording")
        raise


def _bound_body(v: Verified, payload: dict[str, Any], bound_ops: MsGraphOps) -> tuple[dict[str, Any], Any]:
    """The wire body and the ops call for a verified binding: a reply on the
    verified email, or (a scheduled litigation job, a negotiation notice or a new-message SMD
    note) one new message whose
    recipient and subject are the broker's; a caller's to/cc/bcc/subject and
    attachments are never read."""
    html = {"html": payload["html"]} if isinstance(payload.get("html"), str) and payload["html"].strip() else {}
    comment = str(payload.get("comment") or "")
    if v.mode != MODE_NEW_MESSAGE:
        return {"message_id": v.graph_message_id, "comment": comment, **html}, bound_ops.reply
    if not (comment.strip() or html):
        raise BindingRefused("refusing to send an empty message")
    return {"to": [v.sender], "subject": v.subject, "body_text": comment, **html}, bound_ops.send


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
    _check_held_wording(broker, raw, v, payload)
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
    body, transmit = _bound_body(v, payload, bound_ops)
    extra = request.get("audit_extra")
    audit_extra = dict(extra) if isinstance(extra, dict) else {}
    audit_extra["reply_binding"] = v.key
    try:
        result = dispatch_transmit(
            broker,
            action,
            # The participant fence's anchor is the email the broker just
            # verified, never one the caller named: a bound reply answers it.
            # A scheduled litigation job, a negotiation notice or a new-message SMD note has no
            # such email: its one new
            # message is fenced as a send on the skill's authored lane
            # (settings.scheduled_recipients), and names no anchor.
            {
                **request,
                "payload": body,
                "audit_extra": audit_extra,
                "anchor": None
                if v.mode == MODE_NEW_MESSAGE
                else {"kind": "graph_message", "graph_message_id": v.graph_message_id},
                "lane": None,
            },
            send=transmit,
            reply=transmit,
            internal_lane=SCHEDULED_LANES.get(v.kind) if v.mode == MODE_NEW_MESSAGE else None,
            refused=MsGraphRefused,
            transport=MsGraphTransportError,
            attempted_for_send=lambda _payload: [v.sender],
            identity_key="mailbox",
            channel="msgraph",
        )
    except Exception as exc:
        # Nothing to release for a failure before the POST: the claim is taken
        # only AT the POST, so such a failure never took one. A claim that was
        # taken means the POST was attempted, and the reply may have gone.
        if state["claimed"]:
            _settle_failed_post(broker, ops, v, raw, exc, str(state["claimed_at"]))
        raise
    _settle_sent(broker, db_path, v, result)
    after_bound_reply(broker, v, session_id)
    return result


def _settle_sent(broker: BrokerContext, db_path: str, v: Verified, result: Any) -> None:
    """Settle a POST that returned. The send's own Sent Items lookup
    (``msgraph_ops._locate_sent``, retried with backoff) is the requester-side
    proof. Not found is not a failed send (the message is gone; resending would
    duplicate it), so it is settled ``sent`` and, for a job's reply, SMD is
    paged to look (proven delivery, 2026-10-13)."""
    lookup = str(result.get("lookup") or "") if isinstance(result, dict) else ""
    located = lookup.startswith("ok") if lookup else None
    bound_replies.settle(db_path, v.key, "sent", located)
    if located is not False or v.kind not in JOB_KINDS:
        return
    try:
        delivery_proof.append_alert(
            broker,
            lane=v.kind[: -len("_job")],
            job_id=v.job_id or v.key.split(":")[1],
            code="reply_not_in_sent_items",
            attempt=int(v.key.split(":")[2]) if v.key.count(":") >= 3 else 1,
            detail=f"the reply was sent and its Sent Items lookup said: {lookup}",
        )
    except (ValueError, OSError, sqlite3.Error):
        logger.exception("could not record the unlocated reply for %s", v.key)


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
    "job_ledger",
    "reply_verb",
    "scheduled_subject",
    "verify",
]
