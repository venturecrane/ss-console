"""Staff send-as on that staff member's emailed approval (ADR 0089).

The Operator drafts an email to an outside party; the named staff member is
emailed the exact draft; their reply sends it FROM them, asks for a change, or
cancels it. This module is the broker half: the durable row, the approval email,
the decision, and the one transmit that sets a From.

WHY THE BROKER OWNS ALL OF IT. The send app's mailbox holds Exchange Send As on
every staff member the seat may send for, so whoever can reach that credential
can put a staff member's name on a message. The broker is the only holder. So
the rules that make impersonation safe live here, beside the key, and not in
plugin code the agent's process runs:

* a From is set ONLY on a row the named staff member approved, by digest, once;
* the approval email goes to the row's approver and nobody else;
* the approver alone may send; an administrator may cancel but never send;
* an approval that this mailbox itself sent (its Sent Items hold it) is refused,
  and a failed check refuses too.

WHAT THE ROW IS. A proposal is a DRAFT (ADR 0089): it may be made on a turn that
read outside material, because nothing leaves until a person approves the exact
text. The overlay runs the fabrication, matter and identifier gates on the
proposing session, where the read provenance lives, and the broker refuses a
proposal whose gate pass is not all true. At approval time only
session-independent facts are re-checked: digest, expiry, approver, unconsumed.

Tag word ``draft``, not ``act``: ``[draft 1a2b3c4d]``. The establishment matcher
owns ``[act x]`` and the pending-rules table; this lane has its own table and its
own word so neither can answer for the other.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from .canon import canonical
from .msgraph_ops import MsGraphRefused, MsGraphTransportError, collect_recipients
from .recipient_policy import authored_policy, domain_of, normalize_address, split_blocks
from .transmit_verbs import append_send_row, dispatch_transmit

#: How long a staff member has to answer. Its own constant, deliberately not the
#: act TTL: the act window was authorized for matter creation and widening it
#: would widen a commitment nobody widened.
SEND_AS_TTL_SECONDS = 86_400

#: How far back a new proposal may claim to replace a revised one.
_REVISE_LINK_WINDOW_S = SEND_AS_TTL_SECONDS

STATUS_OPEN = "open"
STATUS_REVISED = "revised"
STATUS_CANCELLED = "cancelled"
STATUS_DISPATCHED = "dispatched"
STATUS_FAILED = "failed"
STATUS_SENDING = "sending"

DECISIONS = ("send", "change", "cancel")

_TAG_RE = re.compile(r"^\s*(?:\[\s*draft\s+)?([0-9a-f]{8})\s*\]?\s*$", re.IGNORECASE)
_MAX_SUBJECT = 998
_MAX_BODY = 100_000
_MAX_RECIPIENTS = 20
_MAX_INSTRUCTION = 4_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS send_as_acts (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL,
    approver TEXT NOT NULL,
    instructed_by TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0,
    sources_json TEXT NOT NULL DEFAULT '[]',
    decided_by TEXT,
    decided_at REAL,
    instruction TEXT,
    replaced_by TEXT,
    conversation_id TEXT,
    result TEXT
)
"""


class SendAsRefused(ValueError):
    """A proposal or decision the seat's rules do not permit. Audited as such."""


def tag_for(act_id: str) -> str:
    return f"[draft {act_id}]"


def parse_tag(value: Any) -> str:
    """The 8-hex id from ``[draft xxxxxxxx]`` or a bare id; refused otherwise."""
    match = _TAG_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise SendAsRefused("not a draft tag; expected [draft xxxxxxxx]")
    return match.group(1).lower()


def render_html(body_text: str) -> str:
    """The HTML part, derived from the text by the broker so the digest covers it.

    Escaped, newlines kept. Link targets stay as written text (no anchors are
    minted), so what the approver read is what the recipient can see.
    """
    return "<div>" + html.escape(body_text).replace("\n", "<br>\n") + "</div>"


def canonical_payload(payload: dict[str, Any], mailbox: str) -> dict[str, Any]:
    """The exact message a proposal would send, from a CLOSED set of fields.

    ``bcc`` and ``reply_to`` are not accepted from the caller: a draft that a
    person approves must carry no recipient they cannot see, and replies go to
    the staff member and this mailbox, set here.
    """
    sender = normalize_address(payload.get("from"))
    if not sender or "@" not in sender:
        raise SendAsRefused("a send-as draft needs a from address")
    to = _addresses(payload.get("to"), "to")
    cc = _addresses(payload.get("cc"), "cc")
    if not to:
        raise SendAsRefused("a send-as draft needs at least one To recipient")
    if len(to) + len(cc) > _MAX_RECIPIENTS:
        raise SendAsRefused(f"a send-as draft may name at most {_MAX_RECIPIENTS} recipients")
    subject = payload.get("subject")
    body_text = payload.get("body_text")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > _MAX_SUBJECT:
        raise SendAsRefused("a send-as draft needs a subject")
    if not isinstance(body_text, str) or not body_text.strip() or len(body_text) > _MAX_BODY:
        raise SendAsRefused("a send-as draft needs a body")
    return {
        "from": sender,
        "to": to,
        "cc": cc,
        "subject": subject.strip(),
        "body_text": body_text,
        "html": render_html(body_text),
        "reply_to": [sender, normalize_address(mailbox)],
    }


def digest_of(canonical_msg: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(canonical_msg)).hexdigest()


def _addresses(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value) if isinstance(value, list) else None
    if items is None:
        raise SendAsRefused(f"{field} must be an address or a list of addresses")
    out: list[str] = []
    for item in items:
        address = normalize_address(item)
        if not address or "@" not in address:
            raise SendAsRefused(f"{field} holds something that is not an address")
        if address not in out:
            out.append(address)
    return out


def _seat(customer_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    """``value`` when it is a mapping, else an empty one (never None)."""
    return value if isinstance(value, dict) else {}


def staff_roster(data: dict[str, Any]) -> dict[str, str]:
    """``scope.staff_send_as`` as {address: name}; unusable entries dropped."""
    scope = _mapping(data.get("scope"))
    roster: dict[str, str] = {}
    for entry in scope.get("staff_send_as") or []:
        if not isinstance(entry, dict):
            continue
        address = normalize_address(entry.get("address"))
        name = entry.get("name")
        if address and "@" in address and isinstance(name, str) and name.strip():
            roster[address] = name.strip()
    return roster


def _admins(data: dict[str, Any]) -> set[str]:
    scope = _mapping(data.get("scope"))
    return {normalize_address(a) for a in scope.get("admins") or [] if normalize_address(a)}


def _authors_confirm(data: dict[str, Any]) -> bool:
    """Some persona authors ``exposure.external_send_as_staff: confirm``.

    The weaker of two checks, like the act lane's: the overlay's gate knows the
    running persona; the broker only knows whether the seat authorizes staff
    send-as at all, and a seat that authorizes none must not be able to write a
    row through any path.
    """
    for persona in data.get("personas") or []:
        exposure = ((persona or {}).get("entitlements") or {}).get("exposure") if isinstance(persona, dict) else None
        if isinstance(exposure, dict) and exposure.get("external_send_as_staff") == "confirm":
            return True
    return False


class SendAsStore:
    """The ``send_as_acts`` table, in the seat's audit database."""

    def __init__(self, db_path: str | Path, *, now: Any | None = None) -> None:
        self._db_path = str(db_path)
        self._now = now or time.time
        with self._connect() as db:
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def insert(self, row: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO send_as_acts (id, created_at, expires_at, status, approver, instructed_by,"
                " session_id, payload_json, digest, tainted, sources_json)"
                " VALUES (:id, :created_at, :expires_at, :status, :approver, :instructed_by,"
                " :session_id, :payload_json, :digest, :tainted, :sources_json)",
                row,
            )

    def get(self, act_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            found = db.execute("SELECT * FROM send_as_acts WHERE id = ?", (act_id,)).fetchone()
        return dict(found) if found else None

    def claim(self, act_id: str, *, from_status: str, to_status: str, fields: dict[str, Any]) -> bool:
        """Move a row between states atomically; False if it was not in ``from_status``.

        The consume-once property rests here: two answers racing for the same
        row cannot both see it open, because the UPDATE's WHERE is the check.
        """
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                f"UPDATE send_as_acts SET status = :to_status{', ' + assignments if assignments else ''}"  # noqa: S608 - keys are code-supplied
                " WHERE id = :id AND status = :from_status",
                {"id": act_id, "to_status": to_status, "from_status": from_status, **fields},
            )
            db.execute("COMMIT")
            return cursor.rowcount == 1

    def link_revision(self, approver: str, to: list[str], new_id: str) -> None:
        """Point the newest revised, unreplaced row for this approver and these
        recipients at the proposal that replaces it."""
        since = self._now() - _REVISE_LINK_WINDOW_S
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, payload_json FROM send_as_acts WHERE status = ? AND approver = ?"
                " AND replaced_by IS NULL AND created_at >= ? ORDER BY created_at DESC",
                (STATUS_REVISED, approver, since),
            ).fetchall()
            for row in rows:
                if sorted(json.loads(row["payload_json"]).get("to") or []) == sorted(to):
                    db.execute("UPDATE send_as_acts SET replaced_by = ? WHERE id = ?", (new_id, row["id"]))
                    return

    def dispatched_on(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            found = db.execute(
                "SELECT * FROM send_as_acts WHERE status = ? AND conversation_id = ? ORDER BY created_at DESC",
                (STATUS_DISPATCHED, conversation_id),
            ).fetchone()
        return dict(found) if found else None


def _approval_email(
    row_id: str, msg: dict[str, Any], policy: Any, tainted: bool, sources: list[str], name: str
) -> dict[str, Any]:
    tag = tag_for(row_id)
    lines = [
        f"{name}, the Operator has drafted this email to send from your address. Nothing has been sent.",
        "",
    ]
    for field in ("to", "cc"):
        for address in msg[field]:
            mark = "" if policy.allows_recipient(address) else "  (not on your firm's roster)"
            lines.append(f"{field.upper()}: {address}{mark}")
    lines += [f"SUBJECT: {msg['subject']}", "Replies will come to you and to the Operator.", ""]
    if tainted:
        shown = ", ".join(sources) if sources else "an outside message or document"
        lines += [f"Prepared after reading outside material: {shown}.", ""]
    lines += ["----- draft -----", msg["body_text"], "----- end of draft -----", ""]
    lines += [
        "Reply with one of these as the first line:",
        f"{tag} send",
        f"{tag} change: what to change",
        f"{tag} cancel",
        "",
        f"This draft expires in {SEND_AS_TTL_SECONDS // 3600} hours if you do not answer.",
    ]
    return {"to": [msg["from"]], "subject": f"Approve: {msg['subject']} {tag}", "body_text": "\n".join(lines)}


def _audited_send(broker: Any, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    """A seat-mailbox send (approval email or notice) through the SAME audited
    transmit every other broker send uses: recipient-fenced, and written to the
    ledger as CONFIRM_SEND_DISPATCHED with its audit header, so the console's
    send reconciler (operator/bin/reconcile-sends.py) joins it by identity
    rather than flagging the Operator's own approval mail as unaudited."""
    return dispatch_transmit(
        broker,
        "msgraph_send",
        {"payload": payload, "session_id": session_id},
        send=broker.msgraph.send,
        reply=broker.msgraph.reply,
        refused=MsGraphRefused,
        transport=MsGraphTransportError,
        attempted_for_send=collect_recipients,
        identity_key="mailbox",
    )


def _notice(broker: Any, to: str, subject: str, text: str) -> None:
    """A fixed-recipient notice to a staff member, through the audited send."""
    _audited_send(broker, {"to": [to], "subject": subject, "body_text": text})


def _require_configured(broker: Any) -> None:
    if broker.msgraph is None or broker.ledger is None or not getattr(broker, "audit_db_path", None):
        raise ValueError("send-as is not configured on this broker (needs msgraph, an audit ledger, and an audit db)")


def _store(broker: Any) -> SendAsStore:
    return SendAsStore(broker.audit_db_path, now=getattr(broker, "send_as_now", None))


def _audit(broker: Any, action_type: str, verb: str, meta: dict[str, Any], session_id: str = "") -> None:
    append_send_row(broker, action_type, verb, meta, session_id=session_id)


def _clean(value: Any, limit: int = 320) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _validated_draft(broker: Any, request: dict[str, Any], instructed_by: str) -> tuple[dict[str, Any], str]:
    """The canonical draft and the approver's name, or ``SendAsRefused`` naming why."""
    data = _seat(broker.customer_path)
    if not _authors_confirm(data):
        raise SendAsRefused("this seat authors no persona with exposure.external_send_as_staff: confirm")
    roster = staff_roster(data)
    msg = canonical_payload(_mapping(request.get("payload")), broker.msgraph.mailbox())
    if msg["from"] not in roster:
        raise SendAsRefused(f"{msg['from']} is not on scope.staff_send_as")
    if not instructed_by or (instructed_by != msg["from"] and instructed_by not in _admins(data)):
        raise SendAsRefused(
            "a draft may be sent only as the staff member who asked for it, or at an administrator's request"
        )
    gate = _mapping(request.get("gate_pass"))
    if not all(gate.get(k) is True for k in ("fabrication", "matter", "identifier")):
        raise SendAsRefused("the draft did not pass the fabrication, matter, and identifier gates")
    blocked = split_blocks(_mapping(data.get("scope")).get("domain_blocks"))
    refused = [a for a in msg["to"] + msg["cc"] if domain_of(a) in blocked]
    if refused:
        raise SendAsRefused("recipient(s) in a blocked domain: " + ", ".join(sorted(refused)))
    return msg, roster[msg["from"]]


def _insert_draft(
    store: SendAsStore, msg: dict[str, Any], instructed_by: str, session_id: str, tainted: bool, sources: list[str]
) -> tuple[str, float]:
    now = store._now()
    act_id = secrets.token_hex(4)
    store.insert(
        {
            "id": act_id,
            "created_at": now,
            "expires_at": now + SEND_AS_TTL_SECONDS,
            "status": STATUS_OPEN,
            "approver": msg["from"],
            "instructed_by": instructed_by,
            "session_id": session_id,
            "payload_json": json.dumps(msg, sort_keys=True),
            "digest": digest_of(msg),
            "tainted": 1 if tainted else 0,
            "sources_json": json.dumps(sources),
        }
    )
    store.link_revision(msg["from"], msg["to"], act_id)
    return act_id, now


def propose(broker: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Store a draft for a staff member's approval and email it to them."""
    _require_configured(broker)
    session_id = _clean(request.get("session_id"), 200)
    instructed_by = normalize_address(request.get("instructed_by"))
    try:
        msg, name = _validated_draft(broker, request, instructed_by)
    except SendAsRefused as exc:
        _audit(broker, "SEND_AS_REFUSED", "send_as_propose", {"stage": "propose", "reason": str(exc)}, session_id)
        return {"ok": False, "reason": str(exc)}
    tainted = bool(request.get("tainted"))
    sources = [s for s in (_clean(x, 200) for x in (request.get("sources") or [])) if s][:10]
    store = _store(broker)
    act_id, now = _insert_draft(store, msg, instructed_by, session_id, tainted, sources)
    email = _approval_email(act_id, msg, authored_policy(broker.customer_path), tainted, sources, name)
    try:
        _audited_send(broker, email, session_id)
    except (MsGraphRefused, MsGraphTransportError) as exc:
        store.claim(
            act_id,
            from_status=STATUS_OPEN,
            to_status=STATUS_CANCELLED,
            fields={"result": f"approval email failed: {exc}"},
        )
        _audit(
            broker,
            "SEND_AS_REFUSED",
            "send_as_propose",
            {"stage": "notify", "act": act_id, "reason": str(exc)},
            session_id,
        )
        return {"ok": False, "reason": f"the approval email to {msg['from']} could not be sent: {exc}"}
    digest = digest_of(msg)
    proposed = {
        "act": act_id,
        "approver": msg["from"],
        "recipients": msg["to"] + msg["cc"],
        "digest": digest,
        "tainted": tainted,
    }
    _audit(broker, "SEND_AS_PROPOSED", "send_as_propose", proposed, session_id)
    return {
        "ok": True,
        "tag": tag_for(act_id),
        "act_id": act_id,
        "digest": digest,
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + SEND_AS_TTL_SECONDS)),
        "notified": True,
        "approver_name": name,
    }


def _terminal_status(row: dict[str, Any], now: float) -> dict[str, Any] | None:
    if row["status"] == STATUS_OPEN and now > row["expires_at"]:
        return {"status": "EXPIRED", "reason": "this draft expired; nothing was sent"}
    if row["status"] == STATUS_REVISED:
        return {
            "status": "SUPERSEDED",
            "reason": "this draft was revised",
            "replaced_by": tag_for(row["replaced_by"]) if row.get("replaced_by") else None,
        }
    if row["status"] != STATUS_OPEN:
        return {"status": "REFUSED", "reason": f"this draft is already {row['status']}"}
    return None


_BASE: dict[str, Any] = {"instruction": None, "replaced_by": None}


def _refused(reason: str) -> dict[str, Any]:
    return {**_BASE, "status": "REFUSED", "reason": reason}


def _open_row(store: SendAsStore, request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(row, None) for an answerable draft, else (None, the answer to give)."""
    try:
        act_id = parse_tag(request.get("tag_or_act_id"))
    except SendAsRefused as exc:
        return None, _refused(str(exc))
    row = store.get(act_id)
    if row is None:
        return None, _refused("no such draft")
    if request.get("decision") not in DECISIONS:
        return None, _refused("decision must be send, change, or cancel")
    ended = _terminal_status(row, store._now())
    return (None, {**_BASE, **ended}) if ended is not None else (row, None)


def _authority_refusal(broker: Any, row: dict[str, Any], request: dict[str, Any], meta: dict[str, Any]) -> str | None:
    """Why this person may not give this answer, or None. Audits a refusal.

    The approver alone may send or change; the approver or an administrator may
    cancel. Then the forgery guard: this mailbox can send AS the approver, so an
    answer it sent itself must not count, and a check that cannot run refuses.
    """
    decided_by = normalize_address(request.get("decided_by"))
    approver = row["approver"]
    if request.get("decision") in ("send", "change"):
        allowed = decided_by == approver
    else:
        allowed = decided_by in ({approver} | _admins(_seat(broker.customer_path)))
    if not allowed:
        reason = "only the staff member this draft would be sent as may send or change it"
        _audit(broker, "SEND_AS_REFUSED", "send_as_decide", {**meta, "stage": "authority", "reason": reason})
        return reason
    try:
        if broker.msgraph.sent_items_holds(_clean(request.get("internet_message_id"), 998)):
            raise SendAsRefused("that answer was sent from the Operator's own mailbox")
    except (SendAsRefused, MsGraphTransportError, MsGraphRefused) as exc:
        reason = f"the answer's origin could not be confirmed: {exc}"
        _audit(broker, "SEND_AS_REFUSED", "send_as_decide", {**meta, "stage": "origin", "reason": reason})
        return reason
    return None


def _apply_cancel(
    broker: Any, store: SendAsStore, row: dict[str, Any], decided_by: str, meta: dict[str, Any]
) -> dict[str, Any]:
    store.claim(
        row["id"],
        from_status=STATUS_OPEN,
        to_status=STATUS_CANCELLED,
        fields={"decided_by": decided_by, "decided_at": store._now()},
    )
    _audit(broker, "SEND_AS_CANCELLED", "send_as_decide", meta)
    return {**_BASE, "status": "CANCELLED", "reason": "cancelled; nothing was sent"}


def _apply_change(
    broker: Any, store: SendAsStore, row: dict[str, Any], decided_by: str, instruction: str, meta: dict[str, Any]
) -> dict[str, Any]:
    if not instruction:
        return _refused("say what to change after 'change:'")
    fields = {"decided_by": decided_by, "decided_at": store._now(), "instruction": instruction}
    if not store.claim(row["id"], from_status=STATUS_OPEN, to_status=STATUS_REVISED, fields=fields):
        return _refused("this draft was answered already")
    _audit(broker, "SEND_AS_REVISED", "send_as_decide", meta)
    return {**_BASE, "status": "REVISED", "reason": "revision requested", "instruction": instruction}


def _report_failure(
    broker: Any, store: SendAsStore, row: dict[str, Any], msg: dict[str, Any], exc: Exception, meta: dict[str, Any]
) -> dict[str, Any]:
    """No automatic retry: a transport failure's outcome is unknown and a retry
    could deliver twice. The approver is told, never left guessing."""
    store.claim(row["id"], from_status=STATUS_SENDING, to_status=STATUS_FAILED, fields={"result": str(exc)[:500]})
    _audit(broker, "SEND_AS_FAILED", "send_as_decide", {**meta, "reason": str(exc)})
    try:
        _notice(
            broker,
            row["approver"],
            f"Not sent: {msg['subject']} {tag_for(row['id'])}",
            f"The email was not sent: {exc}. Nothing was retried.",
        )
    except (MsGraphRefused, MsGraphTransportError):
        pass
    return {**_BASE, "status": "FAILED", "reason": str(exc)}


def _apply_send(
    broker: Any, store: SendAsStore, row: dict[str, Any], decided_by: str, meta: dict[str, Any]
) -> dict[str, Any]:
    approver = row["approver"]
    msg = json.loads(row["payload_json"])
    if (
        digest_of(msg) != row["digest"]
        or msg.get("from") != approver
        or approver not in staff_roster(_seat(broker.customer_path))
    ):
        reason = "the stored draft no longer matches what was approved, or its sender is off the roster"
        _audit(broker, "SEND_AS_REFUSED", "send_as_decide", {**meta, "stage": "integrity", "reason": reason})
        return _refused(reason)
    if not store.claim(
        row["id"],
        from_status=STATUS_OPEN,
        to_status=STATUS_SENDING,
        fields={"decided_by": decided_by, "decided_at": store._now()},
    ):
        return _refused("this draft was answered already")
    try:
        result = broker.msgraph.send_as_staff(msg, approver)
    except (MsGraphRefused, MsGraphTransportError) as exc:
        return _report_failure(broker, store, row, msg, exc, meta)
    conversation = broker.msgraph.conversation_of(result.get("graph_message_id") or "")
    fields = {"conversation_id": conversation, "result": result.get("lookup") or ""}
    store.claim(row["id"], from_status=STATUS_SENDING, to_status=STATUS_DISPATCHED, fields=fields)
    # The reconciler's exact joins (reconcile-sends.py pass 1): the header value
    # stamped on the message itself, and the vendor id.
    joins = {
        k: result[k]
        for k in ("audit_row_token", "vendor_message_id", "graph_message_id", "lookup")
        if isinstance(result.get(k), str) and result[k]
    }
    sent = {
        **meta,
        "from": approver,
        "recipients": collect_recipients(msg),
        "mailbox": result.get("mailbox") or "",
        **joins,
    }
    _audit(broker, "SEND_AS_SENT", "send_as_decide", sent)
    return {**_BASE, "status": "DISPATCHED", "reason": f"sent from {approver}"}


def decide(broker: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Apply a staff member's answer: send it as them, revise it, or cancel it."""
    _require_configured(broker)
    store = _store(broker)
    row, answer = _open_row(store, request)
    if row is None:
        return answer or _refused("no such draft")
    decision = request.get("decision")
    meta = {"act": row["id"], "decision": decision, "digest": row["digest"]}
    refusal = _authority_refusal(broker, row, request, meta)
    if refusal is not None:
        return _refused(refusal)
    decided_by = normalize_address(request.get("decided_by"))
    if decision == "cancel":
        return _apply_cancel(broker, store, row, decided_by, meta)
    if decision == "change":
        return _apply_change(broker, store, row, decided_by, _clean(request.get("instruction"), _MAX_INSTRUCTION), meta)
    return _apply_send(broker, store, row, decided_by, meta)


def match_reply(broker: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Tell the approver when an outside party answers a draft sent as them."""
    _require_configured(broker)
    conversation = _clean(request.get("conversation_id"), 500)
    if not conversation:
        return {"matched": False, "tag": None}
    row = _store(broker).dispatched_on(conversation)
    if row is None:
        return {"matched": False, "tag": None}
    sender = normalize_address(request.get("from"))
    if sender == row["approver"]:
        return {"matched": False, "tag": None}
    msg = json.loads(row["payload_json"])
    tag = tag_for(row["id"])
    try:
        _notice(
            broker,
            row["approver"],
            f"Reply received: {msg['subject']} {tag}",
            f"{sender or 'The recipient'} replied to the email sent from your address ({tag}). The reply is in your inbox and the Operator's.",
        )
    except (MsGraphRefused, MsGraphTransportError):
        return {"matched": True, "tag": tag}
    _audit(broker, "SEND_AS_REPLY_NOTICED", "send_as_match_reply", {"act": row["id"]})
    return {"matched": True, "tag": tag}


def _verb(fn: Any) -> Any:
    def handler(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
        return fn(broker, request)

    return handler


propose_verb = _verb(propose)
decide_verb = _verb(decide)
match_reply_verb = _verb(match_reply)

VERBS: tuple[str, ...] = ("send_as_propose", "send_as_decide", "send_as_match_reply")
