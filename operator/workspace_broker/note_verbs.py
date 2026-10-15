"""The ``note_*`` broker verbs: SMD has the Operator tell a firm colleague
something, in her own thread, in words SMD approved.

WHY (2026-10-15). The Operator filed a document on SMD's request and could not
tell the colleague who asked: a turn an inbound email opened may answer only
its sender, and the only SMD-initiated way for the Operator to speak was a
job's completion wake. A note job is that wake with no job behind it. The root
command ``medchron.note_by_smd`` records the note here, then wakes the
smd-note skill, whose DELIVER mode binds ``note_job`` (``reply_binding.py``)
and sends the recorded words, verbatim, as a reply on the colleague's email,
copying that email's vetted participants (``MsGraphOps._request_participants``).

Peer gating, per verb (``verbs.py`` declares and checks it first):

    note_preview      uid 0 only. Every record check, writes NOTHING: returns the
                      anchor's subject, sender and received time, the exact CC
                      list the reply will carry, and a digest over (Graph id,
                      text, CC list) that the live record must repeat.
    note_record       uid 0 only. The same checks, fresh; refuses unless the
                      caller's ``confirm`` equals a fresh preview digest (what
                      SMD read is what is recorded: a CC list that changed since
                      the dry run changes the digest). One NOTE_JOB_RECORDED
                      audit row: ids, the text's digest, the requester's key and
                      who approved it. Never the text.
    note_job_status   gateway PID or uid 0: the job id, its state and its text,
                      which the DELIVER turn sends word for word.

THE CHECKS (each a refusal, none spends anything): the Graph id is a received
email in this mailbox's Inbox (``msgraph_lookup.received_by_graph_id``: not a
draft, not this mailbox's own, no foreign replyTo); its sender is someone this
seat may answer (``authored_policy().allows_reply_to``); it arrived within
``reply_binding.RECENCY_DAYS``; the text is 1 to 2000 characters with no em
dash; ``approved_by`` names who approved it; and every identifier in the text
appears in that email's subject or body (``note_identifiers.uncorroborated``).
The words are the same words twice only once per email (the ledger's UNIQUE
index), so a re-run of the command can never become a second send.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import msgraph_lookup, note_identifiers
from .broker_context import BrokerContext
from .msgraph_ops import MsGraphOps, MsGraphRefused
from .note_ledger import AUDIT_TYPE, SKILL_NAME, NoteExists, NoteLedger, text_digest
from .recipient_policy import authored_policy, sender_key
from .reply_binding import RECENCY_DAYS

VERBS: tuple[str, ...] = ("note_preview", "note_record", "note_job_status")
MAX_TEXT = 2000
MAX_APPROVER = 120
EM_DASH = "—"
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")
_JOB_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_ANCHOR_SELECT = "id,subject,body,toRecipients,ccRecipients"


class NoteRefused(ValueError):
    """The note cannot be recorded. The message is the sentence for SMD."""


def _refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


def ledger(broker: BrokerContext) -> NoteLedger:
    if not broker.audit_db_path:
        raise ValueError("note verbs need the audit ledger (SMD_AUDIT_DB_PATH)")
    return NoteLedger(str(broker.audit_db_path))


def _ops(broker: BrokerContext) -> MsGraphOps:
    if broker.msgraph is None or broker.ledger is None or not broker.audit_db_path:
        raise ValueError("note verbs need the msgraph transmit and the audit ledger on a Graph seat")
    return broker.msgraph


def _body_text(body: Any) -> str:
    """The email body as plain text: tags dropped, entities decoded."""
    if not isinstance(body, dict):
        return ""
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", content)
        content = re.sub(r"<[^>]+>", " ", content)
    return html.unescape(content)


def _text_checks(text: Any, approved_by: Any) -> tuple[str, str]:
    if not isinstance(text, str) or not text.strip():
        raise NoteRefused("the note has no text; nothing was recorded")
    if len(text) > MAX_TEXT:
        raise NoteRefused(f"the note is {len(text)} characters; a note is at most {MAX_TEXT}")
    if EM_DASH in text:
        raise NoteRefused("the note contains an em dash; this seat's mail carries none. Rewrite it without one")
    who = approved_by.strip() if isinstance(approved_by, str) else ""
    if not who or len(who) > MAX_APPROVER or "\n" in who:
        raise NoteRefused("approved_by must name who approved these exact words (one line)")
    return text, who


def preview(broker: BrokerContext, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Every check, fresh, from the mailbox and the live customer.yaml. Writes
    nothing. Raises ``NoteRefused``; a mailbox that cannot be read raises its
    transport error (nothing was decided)."""
    ops = _ops(broker)
    gid = str(request.get("graph_message_id") or "").strip()
    if not _GRAPH_ID.match(gid):
        raise NoteRefused("graph_message_id must be the request email's Graph id")
    text, who = _text_checks(request.get("text"), request.get("approved_by"))
    try:
        found = msgraph_lookup.received_by_graph_id(ops, gid)
    except MsGraphRefused as exc:
        raise NoteRefused(str(exc)) from exc
    if found is None or not found["graph_message_id"]:
        raise NoteRefused("that email is not in this seat's Inbox as a received message")
    sender = found["sender"]
    policy = authored_policy(broker.customer_path)
    if not policy.allows_reply_to(sender):
        raise NoteRefused("the sender of that email is not someone this seat may reply to (scope.inbound_allow_from)")
    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(days=RECENCY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not found["received_at"] or found["received_at"] < since:
        raise NoteRefused(f"that email is older than {RECENCY_DAYS} days; a note answers a recent request only")
    source = msgraph_lookup._read(ops, ops._mail_path("messages", gid) + f"?$select={_ANCHOR_SELECT}")
    if not isinstance(source, dict) or str(source.get("id") or "") != gid:
        raise NoteRefused("the mailbox answered for a different message than the one named; refusing")
    subject = str(source.get("subject") or "")
    missing = note_identifiers.uncorroborated(text, f"{subject}\n{_body_text(source.get('body'))}", now=moment)
    if missing:
        raise NoteRefused(
            "the note names an identifier that email does not carry ("
            + ", ".join(sorted({h.raw for h in missing}))
            + "); a note may state only the request's own matter numbers, dates and ids"
        )
    cc = ops._request_participants(source, sender, policy)
    digest = hashlib.sha256(
        json.dumps(
            {"graph_message_id": gid, "text": text, "cc": cc, "approved_by": who}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return {
        "graph_message_id": gid,
        "subject": subject,
        "sender": sender,
        "received_at": found["received_at"],
        "cc": cc,
        "text": text,
        "approved_by": who,
        "digest": digest,
    }


def preview_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    try:
        p = preview(broker, request)
    except NoteRefused as exc:
        return _refusal(str(exc))
    existing = ledger(broker).find(p["graph_message_id"], p["text"])
    if existing:
        return _refusal(f"these words were already recorded on that email as note job {existing}", job_id=existing)
    return {"ok": True, "accepted": True, **p}


def record_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    confirm = str(request.get("confirm") or "").strip()
    try:
        p = preview(broker, request)
    except NoteRefused as exc:
        return _refusal(str(exc))
    if not confirm or confirm != p["digest"]:
        return _refusal(
            "confirm does not match a fresh preview of this note (the words, the email or its copy list "
            "changed since the dry run); run the dry run again and read it"
        )
    try:
        job_id = ledger(broker).create(
            requester=p["sender"],
            request_ref=p["graph_message_id"],
            text=p["text"],
            approved_by=p["approved_by"],
            recorded_by=str(request.get("recorded_by") or "root")[:MAX_APPROVER],
        )
    except NoteExists as exc:
        return _refusal(str(exc), job_id=exc.job_id)
    if broker.ledger is not None:
        broker.ledger.append(
            {
                "action_type": AUDIT_TYPE,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": None,
                "metadata": json.dumps(
                    {
                        "job_id": job_id,
                        "text_digest": text_digest(p["text"]),
                        "requester_key": sender_key(p["sender"]),
                        "approved_by": p["approved_by"],
                        "cc_count": len(p["cc"]),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return {
        "ok": True,
        "accepted": True,
        "job_id": job_id,
        "state": "delivered",
        "digest": p["digest"],
        # The person the reply will reach: the runner's reply watch keys on it.
        "requester": p["sender"],
    }


def status_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    ident = str(request.get("job_id") or "").strip()
    if not _JOB_ID.match(ident):
        raise ValueError("note_job_status requires the note job's id")
    row = ledger(broker).read(ident)
    if row is None:
        return {"ok": True, "job": None}
    return {"ok": True, "job": {"job_id": row["id"], "state": row["state"], "text": row["text"]}}


__all__ = ["MAX_TEXT", "VERBS", "NoteRefused", "ledger", "preview", "preview_verb", "record_verb", "status_verb"]
