"""Which of a person's own rule proposals each sent read-back carried (2026-11-19).

WHY. A person stated two rules, the Operator read both back in one email, and he
answered "[rule A] yes and [rule B] yes". Nothing was recorded, because one
confirmation per reply was enforced, and the Operator then quoted rule ids at him
four times in six minutes. A tag is the wrong handle for a person: he answers the
EMAIL in front of him, and his mail client already says which one, in the
reply's ``In-Reply-To`` header. So the binding is that header.

TWO VERBS, both agent-uid, both under the establishment lock:

* ``establish_readback_sent`` records, after the relay has sent a read-back to a
  person, which of THEIR OWN open rule proposals it carried, keyed by the sent
  message's RFC 5322 id (what both mail paths return: Graph's id resolved out of
  Sent Items, and AgentMail's own message id). Every proposal must be a rule
  (not an act, not an operations request), not waiting on an administrator,
  stated by the recipient, and still open; anything else is refused, so a
  binding can never reach a rule of somebody else's. ``listed`` marks the
  numbered list, so "1 and 3" resolves against the list that message showed.
* ``establish_readback_for`` answers, for an inbound reply, which recorded
  read-back it replies to. The BROKER reads the reply's headers from the mailbox
  itself (Graph ``internetMessageHeaders`` on the read credential; AgentMail's
  message ``in_reply_to`` / ``references``), checks the reply is from the person
  the binding was sent to, and matches ``In-Reply-To`` (else the last
  ``References`` entry, which RFC 5322 makes the parent). Nothing the model
  wrote, and nothing in the message body, chooses the read-back.

WHAT HAPPENS WITHOUT A HEADER. A reply whose header is absent, unreadable or
names no recorded read-back is "not bound", and the seat asks with a numbered
list rendered by code. That list is itself recorded here, so the next reply to
it binds. The failure direction is one more question, never a guessed rule.

STORAGE. One table, ``readback_bindings``, in the broker's audit DB (the file
``pending_rules`` lives in), opened through the rule store's own connection
helper so the journal mode and busy timeout are the store's. It holds ids and
an address, never a rule's words. Rows older than :data:`RETENTION_DAYS` are
dropped on each write; a proposal lapses long before that.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .broker_context import BrokerContext
from .establishment_validation import (
    EstablishmentValidationError,
    _require_proposal_id,
    require_address,
)
from .recipient_policy import normalize_address

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS readback_bindings (
  sent_message_id TEXT NOT NULL,
  position        INTEGER NOT NULL,
  proposal_id     TEXT NOT NULL,
  recipient       TEXT NOT NULL,
  listed          INTEGER NOT NULL DEFAULT 0,
  created_at      REAL NOT NULL,
  PRIMARY KEY (sent_message_id, proposal_id)
)
"""
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_readback_bindings_created ON readback_bindings(created_at)"
#: The read-back a turn owes, recorded when it is RENDERED (durable from then):
#: one row per (email answered, recipient), replaced as the turn's read-back
#: grows, and turned into bindings in the same step as the relay's send.
CREATE_PENDING_SQL = """
CREATE TABLE IF NOT EXISTS readback_pending (
  inbound_message_id TEXT NOT NULL,
  recipient          TEXT NOT NULL,
  proposal_ids       TEXT NOT NULL,
  listed             INTEGER NOT NULL DEFAULT 0,
  created_at         REAL NOT NULL,
  PRIMARY KEY (inbound_message_id, recipient)
)
"""
#: Proposals whose sentence code found verbatim in the person's own email: the
#: only sentences exempt from the citation scan (R2.4, review 2026-11-19).
CREATE_VERIFIED_SQL = """
CREATE TABLE IF NOT EXISTS readback_verified (
  proposal_id TEXT PRIMARY KEY,
  recipient   TEXT NOT NULL,
  created_at  REAL NOT NULL
)
"""
logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
#: The most proposals one read-back may carry; the list code renders is capped
#: at the same number (``shared.rule_confirm.MAX_PICK``).
MAX_CARRIED = 20
_MAX_MESSAGE_ID = 500

VERB_OWED = "establish_readback_owed"
VERB_SENT = "establish_readback_sent"
VERB_FOR = "establish_readback_for"
VERBS: tuple[str, ...] = (VERB_OWED, VERB_SENT, VERB_FOR)


def normalize_message_id(value: Any) -> str:
    """An RFC 5322 message id, bare (no angle brackets), or ``""``.

    The two halves of a match are spelled by different systems (Sent Items, the
    vendor's reply response, the person's mail client), and the brackets are
    the only thing they disagree on."""
    if not isinstance(value, str):
        return ""
    bare = value.strip().strip("<>").strip()
    if not bare or "@" not in bare or len(bare) > _MAX_MESSAGE_ID or any(c.isspace() for c in bare):
        return ""
    return bare


def _references(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [v for v in value if isinstance(v, str)]
    elif isinstance(value, str):
        raw = value.split()
    else:
        raw = []
    return [n for n in (normalize_message_id(r) for r in raw) if n]


def parent_id(in_reply_to: Any, references: Any) -> str:
    """The message a reply answers: ``In-Reply-To``, else the last reference."""
    first = normalize_message_id(in_reply_to)
    if first:
        return first
    refs = _references(references)
    return refs[-1] if refs else ""


class ReadbackBindings:
    """The table, on the rule store's connection."""

    def __init__(self, pending: Any) -> None:
        self._pending = pending
        conn = pending._connect()
        try:
            conn.execute(CREATE_SQL)
            conn.execute(CREATE_INDEX_SQL)
            conn.execute(CREATE_PENDING_SQL)
            conn.execute(CREATE_VERIFIED_SQL)
            conn.commit()
        finally:
            conn.close()

    def record(self, sent_message_id: str, recipient: str, proposal_ids: list[str], listed: bool) -> None:
        now = time.time()
        conn = self._pending._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM readback_bindings WHERE created_at < ?",
                    (now - RETENTION_DAYS * 86400,),
                )
                for position, proposal_id in enumerate(proposal_ids, start=1):
                    conn.execute(
                        "INSERT OR IGNORE INTO readback_bindings "
                        "(sent_message_id, position, proposal_id, recipient, listed, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (sent_message_id, position, proposal_id, recipient, 1 if listed else 0, now),
                    )
        finally:
            conn.close()

    def owe(self, inbound: str, recipient: str, proposal_ids: list[str], listed: bool, verified: list[str]) -> None:
        now = time.time()
        conn = self._pending._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM readback_pending WHERE created_at < ?",
                    (now - RETENTION_DAYS * 86400,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO readback_pending "
                    "(inbound_message_id, recipient, proposal_ids, listed, created_at) VALUES (?, ?, ?, ?, ?)",
                    (inbound, recipient, json.dumps(proposal_ids), 1 if listed else 0, now),
                )
                for proposal_id in verified:
                    conn.execute(
                        "INSERT OR IGNORE INTO readback_verified (proposal_id, recipient, created_at) VALUES (?, ?, ?)",
                        (proposal_id, recipient, now),
                    )
        finally:
            conn.close()

    def take_owed(self, inbound: str, recipient: str) -> tuple[list[str], bool] | None:
        """The read-back owed for this email, removed in the same transaction."""
        conn = self._pending._connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT proposal_ids, listed FROM readback_pending WHERE inbound_message_id = ? AND recipient = ?",
                    (inbound, recipient),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "DELETE FROM readback_pending WHERE inbound_message_id = ? AND recipient = ?",
                    (inbound, recipient),
                )
        finally:
            conn.close()
        ids = json.loads(row[0])
        return [str(i) for i in ids if isinstance(i, str)], bool(row[1])

    def verified(self, recipient: str, among: list[str]) -> list[str]:
        """The verified proposals among ``among`` (the rules the reply being
        answered carried): the citation exemption reaches no other sentence."""
        if not among:
            return []
        conn = self._pending._connect()
        try:
            rows = conn.execute(
                "SELECT proposal_id FROM readback_verified WHERE recipient = ? AND created_at >= ?",
                (recipient, time.time() - RETENTION_DAYS * 86400),
            ).fetchall()
        finally:
            conn.close()
        found = {str(r[0]) for r in rows}
        return [p for p in among if p in found]

    def carried(self, sent_message_id: str, recipient: str) -> tuple[list[str], bool]:
        conn = self._pending._connect()
        try:
            rows = conn.execute(
                "SELECT proposal_id, listed FROM readback_bindings "
                "WHERE sent_message_id = ? AND recipient = ? ORDER BY position ASC",
                (sent_message_id, recipient),
            ).fetchall()
        finally:
            conn.close()
        return [str(r[0]) for r in rows], any(bool(r[1]) for r in rows)


def _open_own_rule(row: dict[str, Any] | None, recipient: str, now: float) -> str:
    """``""`` when the row may be carried by a read-back to ``recipient``, else why not."""
    if row is None:
        return "no such proposal"
    if str(row.get("kind") or "rule") != "rule":
        return "not a rule"
    if row.get("for_admin"):
        return "waiting on an administrator"
    if normalize_address(row.get("instructed_by")) != recipient:
        return "not stated by the recipient"
    if row.get("consumed_at") is not None or row.get("declined_at") is not None:
        return "already answered"
    if row.get("lapsed_at") is not None or float(row.get("expires_at") or 0) < now:
        return "lapsed"
    return ""


def _pending_store(broker: BrokerContext) -> Any:
    store = broker.establishment
    if store is None:
        raise ValueError("establishment spool not configured on this broker")
    return store._require_pending()


def _own_ids(pending: Any, raw_ids: Any, recipient: str) -> list[str]:
    """The ids, each the recipient's own open rule; refused otherwise."""
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_CARRIED:
        raise EstablishmentValidationError(f"proposal_ids must be a list of 1 to {MAX_CARRIED} ids")
    ids: list[str] = []
    now = time.time()
    for raw in raw_ids:
        proposal_id = _require_proposal_id(raw)
        why = _open_own_rule(pending.get(proposal_id), recipient, now)
        if why:
            raise EstablishmentValidationError(f"{proposal_id} cannot be read back to this person: {why}")
        if proposal_id not in ids:
            ids.append(proposal_id)
    return ids


def _inbound(request: dict[str, Any]) -> str:
    inbound = str(request.get("inbound_message_id") or "").strip()
    if not inbound or len(inbound) > 512:
        raise EstablishmentValidationError("inbound_message_id is required")
    return inbound


def readback_owed(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Record, durably, the read-back this turn owes the person for this email:
    which of their own open rules it carries, in its order, and which of those
    sentences code found verbatim in their own email (``verified_ids``)."""
    pending = _pending_store(broker)
    recipient = require_address(request.get("recipient"), "recipient")
    ids = _own_ids(pending, request.get("proposal_ids"), recipient)
    listed = request.get("listed", False)
    if not isinstance(listed, bool):
        raise EstablishmentValidationError("listed must be a boolean")
    verified = [str(v) for v in request.get("verified_ids") or [] if isinstance(v, str) and v in ids]
    from . import readback_rounds

    # At most two read-backs per rule (readback_rounds): past the cap nothing is
    # owed, and the seat hands those rules to SMD instead.
    capped = readback_rounds.note_owed(pending, ids, _inbound(request))
    if capped:
        return {"ok": False, "capped": capped}
    ReadbackBindings(pending).owe(_inbound(request), recipient, ids, listed, verified)
    return {"ok": True, "owed": ids}


def readback_sent(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """The relay sent its reply to this email: bind the read-back it owed to the
    sent message's id, in the same step. ``bound: false`` when it owed none."""
    pending = _pending_store(broker)
    sent_message_id = normalize_message_id(request.get("sent_message_id"))
    recipient = require_address(request.get("recipient"), "recipient")
    inbound = _inbound(request)
    store = ReadbackBindings(pending)
    owed = store.take_owed(inbound, recipient)
    if owed is None:
        return {"ok": True, "bound": False}
    ids, listed = owed
    if not sent_message_id:
        logger.warning(
            "readback_bindings: a read-back carrying %s was sent with no usable message id; no binding formed",
            ",".join(ids),
        )
        return {"ok": True, "bound": False}
    store.record(sent_message_id, recipient, ids, listed)
    return {"ok": True, "bound": True, "sent_message_id": sent_message_id, "recorded": ids}


def _graph_headers(ops: Any, message_id: str) -> tuple[str, str, Any]:
    """``(sender, in_reply_to, references)`` of a received Graph message."""
    from .msgraph_lookup import _read

    found = _read(ops, ops._mail_path("messages", message_id) + "?$select=id,from,internetMessageHeaders")
    if not isinstance(found, dict) or str(found.get("id") or "") != message_id:
        raise EstablishmentValidationError("the mailbox answered for a different message")
    headers = found.get("internetMessageHeaders")
    by_name: dict[str, str] = {}
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict):
            name = str(header.get("name") or "").strip().lower()
            if name in ("in-reply-to", "references") and name not in by_name:
                by_name[name] = str(header.get("value") or "")
    return normalize_address(found.get("from")), by_name.get("in-reply-to", ""), by_name.get("references", "")


def _agentmail_headers(ops: Any, message_id: str) -> tuple[str, str, Any]:
    """``(sender, in_reply_to, references)`` of a received AgentMail message."""
    found = ops._request(ops._path("messages", message_id), "GET", None)
    if not isinstance(found, dict):
        raise EstablishmentValidationError("the mailbox returned no message")
    sender = normalize_address(found.get("from") or found.get("from_"))
    in_reply_to = found.get("in_reply_to") or found.get("inReplyTo") or ""
    references = found.get("references")
    return sender, str(in_reply_to or ""), references


def readback_for(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Which recorded read-back an inbound reply answers. Never raises on a
    failed mailbox read: that is "not bound", and the seat asks with a list."""
    pending = _pending_store(broker)
    sender = require_address(request.get("sender"), "sender")
    message_id = str(request.get("inbound_message_id") or "").strip()
    if not message_id or len(message_id) > 512:
        raise EstablishmentValidationError("inbound_message_id is required")
    unbound = {"ok": True, "bound": False, "header": False, "proposal_ids": [], "listed": False}
    reader = _agentmail_headers if "@" in message_id else _graph_headers
    ops = getattr(broker, "agentmail" if reader is _agentmail_headers else "msgraph", None)
    if ops is None:
        return {**unbound, **_extras(pending, sender), "lookup": "no mailbox on this broker"}
    try:
        from_address, in_reply_to, references = reader(ops, message_id)
    except Exception as exc:  # noqa: BLE001 - could not look: ask, never guess
        logger.warning("readback_bindings: could not read the headers of %s (%s)", message_id, type(exc).__name__)
        return {**unbound, **_extras(pending, sender), "lookup": f"failed: {type(exc).__name__}"}
    if from_address != sender:
        return {**unbound, "lookup": "the reply is not from that person"}
    store = ReadbackBindings(pending)
    # No fallback to a list sent earlier (2026-11-20): a reply with no header
    # naming a recorded read-back is unbound, and the seat lists again. The
    # citation exemption reaches only the sentences this reply's answer shows:
    # the rules it answers when bound, the sender's open own rules (the list)
    # when not. Every answer carries the sender's learned signature block
    # (sender_block) and how many read-backs each open rule has had
    # (readback_rounds), which caps them at two.
    extra = _extras(pending, sender)
    listing = {"verified_ids": store.verified(sender, _open_own_ids(pending, sender)), **extra}
    parent = parent_id(in_reply_to, references)
    if not parent:
        return {**unbound, **listing, "lookup": "no reply header"}
    ids, listed = store.carried(parent, sender)
    if not ids:
        logger.warning(
            "readback_bindings: a reply from %s answers %s, which carried no recorded read-back",
            sender,
            parent,
        )
        return {**unbound, **listing, "header": True, "lookup": "replies to no recorded read-back"}
    bound = {"verified_ids": store.verified(sender, ids), **extra}
    return {"ok": True, "bound": True, "header": True, "proposal_ids": ids, "listed": listed, **bound}


def _extras(pending: Any, sender: str) -> dict[str, Any]:
    from . import readback_rounds, sender_block

    return {"block": sender_block.block_for(pending, sender), **readback_rounds.rounds_for(pending, sender)}


def _open_own_ids(pending: Any, sender: str) -> list[str]:
    """The sender's own open rule proposals, in the order they were made."""
    now = time.time()
    conn = pending._connect()
    try:
        rows = conn.execute(
            "SELECT proposal_id FROM pending_rules WHERE kind='rule' AND for_admin=0 AND instructed_by=? "
            "AND consumed_at IS NULL AND declined_at IS NULL AND lapsed_at IS NULL AND expires_at >= ? "
            "ORDER BY created_at",
            (sender, now),
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


__all__ = [
    "CREATE_SQL",
    "MAX_CARRIED",
    "RETENTION_DAYS",
    "ReadbackBindings",
    "VERBS",
    "VERB_FOR",
    "VERB_OWED",
    "VERB_SENT",
    "normalize_message_id",
    "parent_id",
    "readback_for",
    "readback_owed",
    "readback_sent",
]
