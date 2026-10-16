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
                      text, CC list) that the live record must repeat. For a
                      new-message note (``to`` + ``subject``): the recipient,
                      subject, matters, plain numbers and other identifiers,
                      and a digest over them. With ``scan_numbers`` it returns
                      only the words' digit runs, for the runner to look up.
    note_record       uid 0 only. The same checks, fresh; refuses unless the
                      caller's ``confirm`` equals a fresh preview digest (what
                      SMD read is what is recorded: a CC list that changed since
                      the dry run changes the digest). One NOTE_JOB_RECORDED
                      audit row: ids, the text's digest, the requester's key,
                      who approved it, the mode and (a new message) the matter
                      numbers. Never the text, the subject or a matter's name.
    note_job_status   gateway PID or uid 0: the job id, its state and its text,
                      which the DELIVER turn sends word for word.

THE CHECKS for a reply (each a refusal, none spends anything): the Graph id is a received
email in this mailbox's Inbox (``msgraph_lookup.received_by_graph_id``: not a
draft, not this mailbox's own, no foreign replyTo); its sender is someone this
seat may answer (``authored_policy().allows_reply_to``); it arrived within
``reply_binding.RECENCY_DAYS``; the text is 1 to 2000 characters with no em
dash; ``approved_by`` names who approved it; and every identifier in the text
appears in that email's subject or body (``note_identifiers.uncorroborated``).
The words are the same words twice only once per email (the ledger's UNIQUE
index), so a re-run of the command can never become a second send.

THE NEW-MESSAGE NOTE (2026-10-16). Most of what SMD tells a firm has no thread
of hers in this mailbox: she asked Scott directly, or nobody asked (the "it is
live" letter). A request that names ``to`` and ``subject`` instead of a
``graph_message_id`` records a note that goes as ONE new email, through the
reply binding's scheduled path (``reply_binding._verify_scheduled``): the
broker sets the recipient and the subject, and the participant fence admits
the recipient on the smd-note skill's authored lane. With no request email to
corroborate against, its checks are:

* ``to`` is a Named Administrator (``scope.admins``) AND listed in the smd-note
  skill's ``settings.scheduled_recipients`` (read live from the yaml);
* the subject is one line, 1 to 200 characters, no em dash;
* every bare digit run in the words (``note_identifiers.new_message_scan``) is
  either a Smokeball matter in the caller's ``matters`` list or a number in its
  ``plain_numbers`` list (a count, a year, a dollar figure);
* the other identifiers (dates, case numbers) are returned for the dry run and
  bound into the digest: nothing on the seat can vouch for them, so SMD reading
  them is the check.

WHO VOUCHES FOR ``matters``. ``medchron.note_by_smd`` resolves each number on
Smokeball (``lookup_matter``) and passes what it found. This verb is root only,
and it trusts root's list exactly as it trusts root's words: the guard is
against SMD typing the wrong matter number, not against root. The live run
resolves them again, so a matter that changed since the dry run changes the
digest.
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
from .note_ledger import AUDIT_TYPE, NEW_MESSAGE_REF_PREFIX, SKILL_NAME, NoteExists, NoteLedger, text_digest
from .recipient_policy import authored_policy, normalize_address, sender_key
from .reply_binding import RECENCY_DAYS

VERBS: tuple[str, ...] = ("note_preview", "note_record", "note_job_status")
MAX_TEXT = 2000
MAX_APPROVER = 120
MAX_SUBJECT = 200
MAX_MATTERS = 20
RECIPIENTS_KEY = "scheduled_recipients"
_RUN = re.compile(r"^\d{3,10}$")
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


def note_recipients(customer_path: Any) -> list[str] | None:
    """The smd-note skill's authored ``settings.scheduled_recipients``,
    canonical, or None when the yaml cannot be read. An unlisted or disabled
    skill authors nobody."""
    import yaml
    from pathlib import Path

    try:
        doc = yaml.safe_load(Path(str(customer_path)).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - unreadable config authors nobody, and says so
        return None
    for persona in (doc.get("personas") or []) if isinstance(doc, dict) else []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME or skill.get("enabled") is not True:
                continue
            settings = skill.get("settings")
            value = settings.get(RECIPIENTS_KEY) if isinstance(settings, dict) else None
            if isinstance(value, str):
                value = value.split(",")
            if not isinstance(value, list):
                return []
            return [normalize_address(v) for v in value if isinstance(v, str) and normalize_address(v)]
    return []


def _subject_check(subject: Any) -> str:
    if not isinstance(subject, str) or not subject.strip():
        raise NoteRefused("a new-message note needs a subject; nothing was recorded")
    subject = subject.strip()
    if len(subject) > MAX_SUBJECT or "\n" in subject or "\r" in subject:
        raise NoteRefused(f"the subject must be one line of at most {MAX_SUBJECT} characters")
    if EM_DASH in subject:
        raise NoteRefused("the subject contains an em dash; this seat's mail carries none. Rewrite it without one")
    return subject


def _matters_of(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_MATTERS:
        raise NoteRefused(f"matters must be a list of at most {MAX_MATTERS} entries")
    out: list[dict[str, str]] = []
    for m in raw:
        number = str((m or {}).get("number") or "").strip() if isinstance(m, dict) else ""
        name = str((m or {}).get("name") or "").strip()[:200] if isinstance(m, dict) else ""
        if not _RUN.match(number):
            raise NoteRefused("each matter carries its number (3 to 10 digits) and the name Smokeball gave it")
        out.append({"number": number, "name": name})
    return out


def _plain_of(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(v, str) and _RUN.match(v.strip()) for v in raw):
        raise NoteRefused("plain_numbers must be a list of 3-to-10-digit numbers")
    return sorted({v.strip() for v in raw})


def preview_new_message(
    broker: BrokerContext, request: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """A new-message note's checks, fresh from the live customer.yaml. Writes
    nothing; raises ``NoteRefused``."""
    to = normalize_address(request.get("to"))
    if not to:
        raise NoteRefused("to must be the recipient's address")
    authored = note_recipients(broker.customer_path)
    if authored is None:
        raise NoteRefused("this seat's customer.yaml could not be read; nothing was recorded")
    from .medchron_ledger import admins_from_customer_yaml

    admins = admins_from_customer_yaml(str(broker.customer_path)) or ()
    if to not in admins:
        raise NoteRefused(f"{to} is not a Named Administrator on this seat (scope.admins)")
    if to not in authored:
        raise NoteRefused(
            f"{to} is not authored for a new-message note on this seat (the smd-note skill's settings.{RECIPIENTS_KEY})"
        )
    subject = _subject_check(request.get("subject"))
    text, who = _text_checks(request.get("text"), request.get("approved_by"))
    matters = _matters_of(request.get("matters"))
    plain = _plain_of(request.get("plain_numbers"))
    runs, others = note_identifiers.new_message_scan(text, now=now)
    known = {m["number"] for m in matters}
    missing = [r for r in runs if r not in known and r not in plain]
    if missing:
        raise NoteRefused(
            "the note names a number that is neither a Smokeball matter nor declared a plain number ("
            + ", ".join(missing)
            + "); nothing was recorded"
        )
    unused = sorted(known.union(plain) - set(runs))
    if unused:
        raise NoteRefused("these numbers were passed but are not in the note: " + ", ".join(unused))
    identifiers = sorted({f"{h.kind}: {h.canonical}" for h in others})
    digest = hashlib.sha256(
        json.dumps(
            {
                "to": to,
                "subject": subject,
                "text": text,
                "approved_by": who,
                "matters": sorted(matters, key=lambda m: m["number"]),
                "plain_numbers": plain,
                "identifiers": identifiers,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "mode": "new_message",
        "to": to,
        "sender": to,
        "subject": subject,
        "request_ref": NEW_MESSAGE_REF_PREFIX + str(sender_key(to)),
        "matters": matters,
        "plain_numbers": plain,
        "identifiers": identifiers,
        "cc": [],
        "text": text,
        "approved_by": who,
        "digest": digest,
    }


def preview(broker: BrokerContext, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Every check, fresh, from the mailbox and the live customer.yaml. Writes
    nothing. Raises ``NoteRefused``; a mailbox that cannot be read raises its
    transport error (nothing was decided). A request naming ``to`` is a
    new-message note (``preview_new_message``)."""
    if request.get("to") is not None:
        if request.get("graph_message_id"):
            raise NoteRefused("a note either answers an email (graph_message_id) or is a new message (to), not both")
        return preview_new_message(broker, request, now=now)
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
        "mode": "reply",
        "graph_message_id": gid,
        "request_ref": gid,
        "subject": subject,
        "sender": sender,
        "received_at": found["received_at"],
        "cc": cc,
        "text": text,
        "approved_by": who,
        "digest": digest,
    }


def numbers_of(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """``scan_numbers``: the bare digit runs a new-message note's words carry,
    so the runner knows what to look up on Smokeball. The runner's venv cannot
    import this package, and the broker's scan is the one it refuses on, so the
    runner asks rather than keeping a second copy of the regexes. The words
    pass the same text checks first; nothing else is decided here."""
    text, _ = _text_checks(request.get("text"), request.get("approved_by"))
    runs, _ = note_identifiers.new_message_scan(text)
    return {"ok": True, "accepted": True, "numbers": runs}


def preview_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    if request.get("scan_numbers") is True:
        try:
            return numbers_of(broker, request)
        except NoteRefused as exc:
            return _refusal(str(exc))
    try:
        p = preview(broker, request)
    except NoteRefused as exc:
        return _refusal(str(exc))
    existing = ledger(broker).find(p["request_ref"], p["text"], _ledger_subject(p))
    if existing:
        return _refusal(f"{_already(p)} as note job {existing}", job_id=existing)
    return {"ok": True, "accepted": True, **p}


def _ledger_subject(p: dict[str, Any]) -> str | None:
    """The subject the ledger keys a note on: a new message's own; a reply's none."""
    return p["subject"] if p["mode"] == "new_message" else None


def _already(p: dict[str, Any]) -> str:
    if p["mode"] == "new_message":
        return "these words, under this subject, were already recorded for that person"
    return "these words were already recorded on that email"


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
            "confirm does not match a fresh preview of this note (the words, the email, its copy list or "
            "a matter changed since the dry run); run the dry run again and read it"
        )
    try:
        job_id = ledger(broker).create(
            requester=p["sender"],
            request_ref=p["request_ref"],
            text=p["text"],
            approved_by=p["approved_by"],
            recorded_by=str(request.get("recorded_by") or "root")[:MAX_APPROVER],
            subject=_ledger_subject(p),
        )
    except NoteExists as exc:
        return _refusal(f"{_already(p)} as note job {exc.job_id}", job_id=exc.job_id)
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
                        "mode": p["mode"],
                        # Numbers only, never a matter's name: the audit
                        # record is a file the firm exports.
                        **(
                            {"matter_numbers": [m["number"] for m in p["matters"]]}
                            if p["mode"] == "new_message"
                            else {}
                        ),
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
        "mode": p["mode"],
        "digest": p["digest"],
        # The person the note will reach (her thread's sender, or the admin a
        # new email goes to): the runner's reply watch keys on it.
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


__all__ = [
    "MAX_TEXT",
    "VERBS",
    "NoteRefused",
    "ledger",
    "note_recipients",
    "preview",
    "preview_new_message",
    "preview_verb",
    "record_verb",
    "status_verb",
]
