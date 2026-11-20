"""At most two read-backs per rule, then SMD (2026-11-20, approved redesign).

WHY. A person's reply that is not a plain yes is read back again, and that read-
back's reply can be not plain again: without a count, the two emails could
alternate forever (the loop a firm attorney was in). So the broker counts, per
proposal, the emails that owed a read-back of it (``readback_owed``), and owes
no more than :data:`MAX_READBACKS`. The rule's first read-back is round 1; a
restatement or the numbered list is round 2. After that the seat hands the rule
off (``establish_readback_handoff``): it records work SMD owes
(``smd_owed`` kind ``rule_needs_person``, keyed on the email that asked), marks
the proposal handed off so nothing of confirm-by-reply touches it again, and the
person is told the one stopgap sentence. Enforced here, in the store, so no
caller can owe a third.

Ids and email ids only, never a rule's words.
"""

from __future__ import annotations

import time
from typing import Any

from . import smd_owed
from .broker_context import BrokerContext
from .establishment_validation import EstablishmentValidationError, _require_proposal_id, require_address

#: Read-backs a rule may be sent: the read-back, then one restatement or list.
MAX_READBACKS = 2

VERB_HANDOFF = "establish_readback_handoff"
VERBS: tuple[str, ...] = (VERB_HANDOFF,)

_CREATE = (
    """CREATE TABLE IF NOT EXISTS readback_rounds (
  proposal_id TEXT NOT NULL, inbound_message_id TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (proposal_id, inbound_message_id))""",
    """CREATE TABLE IF NOT EXISTS readback_handed_off (
  proposal_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, created_at REAL NOT NULL)""",
)


def _connect(pending: Any) -> Any:
    conn = pending._connect()
    for sql in _CREATE:
        conn.execute(sql)
    return conn


def _counts(conn: Any, ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for proposal_id in ids:
        row = conn.execute("SELECT COUNT(*) FROM readback_rounds WHERE proposal_id = ?", (proposal_id,)).fetchone()
        out[proposal_id] = int(row[0] if row else 0)
    return out


def note_owed(pending: Any, ids: list[str], inbound: str) -> None:
    """Count this email's read-back of ``ids``; refuse any already at the cap
    (a second owe from the SAME email is the same round, not a new one)."""
    conn = _connect(pending)
    try:
        with conn:
            handed = {
                proposal_id
                for proposal_id in ids
                if conn.execute("SELECT 1 FROM readback_handed_off WHERE proposal_id = ?", (proposal_id,)).fetchone()
            }
            if handed:
                raise EstablishmentValidationError(f"{sorted(handed)[0]} was handed to SMD; no further read-back")
            for proposal_id in ids:
                seen = conn.execute(
                    "SELECT 1 FROM readback_rounds WHERE proposal_id = ? AND inbound_message_id = ?",
                    (proposal_id, inbound),
                ).fetchone()
                if seen is None and _counts(conn, [proposal_id])[proposal_id] >= MAX_READBACKS:
                    raise EstablishmentValidationError(
                        f"{proposal_id} has had {MAX_READBACKS} read-backs; hand it to SMD instead"
                    )
            for proposal_id in ids:
                conn.execute(
                    "INSERT OR IGNORE INTO readback_rounds (proposal_id, inbound_message_id, created_at) VALUES (?, ?, ?)",
                    (proposal_id, inbound, time.time()),
                )
    finally:
        conn.close()


def rounds_for(pending: Any, sender: str) -> dict[str, Any]:
    """``{"rounds": {proposal id: read-backs so far}, "handed_off": [...]}`` for
    the sender's own open rules (what ``establish_readback_for`` returns)."""
    from .readback_bindings import _open_own_ids

    ids = _open_own_ids(pending, sender)
    conn = _connect(pending)
    try:
        counts = _counts(conn, ids)
        handed = [
            str(r[0])
            for r in conn.execute(
                "SELECT proposal_id FROM readback_handed_off WHERE recipient = ?", (sender,)
            ).fetchall()
        ]
    finally:
        conn.close()
    return {"rounds": counts, "handed_off": [p for p in handed if p in ids]}


def handoff(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Hand the sender's own open rules in ``proposal_ids`` to SMD: owed, and
    marked so no further read-back of them is ever owed. ``smd_told`` says
    whether the owed record was written (the seat says the stopgap sentence
    only then)."""
    from .readback_bindings import _open_own_ids, _pending_store

    pending = _pending_store(broker)
    sender = require_address(request.get("sender"), "sender")
    raw = request.get("proposal_ids")
    if not isinstance(raw, list) or not raw:
        raise EstablishmentValidationError("proposal_ids must be a non-empty list")
    ids = [_require_proposal_id(p) for p in raw]
    open_ids = set(_open_own_ids(pending, sender))
    if any(p not in open_ids for p in ids):
        raise EstablishmentValidationError("only the sender's own open rules can be handed to SMD")
    ref = str(request.get("request_ref") or "")
    append = broker.ledger.append if broker.ledger is not None else None
    detail = f"rules {', '.join(ids)}: two read-backs, no plain yes; confirm with the person by hand"
    told = smd_owed.record(broker.audit_db_path, append, smd_owed.KIND_NEEDS_PERSON, ref, detail)
    conn = _connect(pending)
    try:
        with conn:
            for proposal_id in ids:
                conn.execute(
                    "INSERT OR IGNORE INTO readback_handed_off (proposal_id, recipient, created_at) VALUES (?, ?, ?)",
                    (proposal_id, sender, time.time()),
                )
    finally:
        conn.close()
    return {"ok": True, "smd_told": told, "handed_off": ids}


HANDLERS = {VERB_HANDOFF: handoff}

__all__ = ["HANDLERS", "MAX_READBACKS", "VERBS", "VERB_HANDOFF", "handoff", "note_owed", "rounds_for"]
