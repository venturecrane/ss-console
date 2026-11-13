"""The ``send_as_acts`` table (ADR 0089): one row per staff send-as draft.

Split out of ``send_as_acts.py`` (module-size ratchet) when ADR 0089 section 9
added the Gmail transport. Nothing here decides who may send; it is the row and
its atomic state moves. The consume-once property rests on :meth:`claim`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .send_as_links import SEND_AS_TTL_SECONDS

#: How far back a new proposal may claim to replace a revised one.
REVISE_LINK_WINDOW_S = SEND_AS_TTL_SECONDS

STATUS_OPEN = "open"
STATUS_REVISED = "revised"
STATUS_CANCELLED = "cancelled"
STATUS_DISPATCHED = "dispatched"
STATUS_FAILED = "failed"
STATUS_SENDING = "sending"

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
        """Close the draft this proposal replaces, and point it at the replacement.

        The candidate is the newest OPEN row for this approver and these
        recipients that carries a revision instruction. A revision request leaves
        the row open on purpose (see ``_apply_change``), so this is the moment it
        becomes REVISED: when the replacement exists, never before.
        """
        since = self._now() - REVISE_LINK_WINDOW_S
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, payload_json FROM send_as_acts WHERE status = ? AND approver = ?"
                " AND replaced_by IS NULL AND instruction IS NOT NULL AND instruction != ''"
                " AND created_at >= ? ORDER BY created_at DESC",
                (STATUS_OPEN, approver, since),
            ).fetchall()
            for row in rows:
                if sorted(json.loads(row["payload_json"]).get("to") or []) == sorted(to):
                    db.execute(
                        "UPDATE send_as_acts SET replaced_by = ?, status = ? WHERE id = ? AND status = ?",
                        (new_id, STATUS_REVISED, row["id"], STATUS_OPEN),
                    )
                    return

    def dispatched_on(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            found = db.execute(
                "SELECT * FROM send_as_acts WHERE status = ? AND conversation_id = ? ORDER BY created_at DESC",
                (STATUS_DISPATCHED, conversation_id),
            ).fetchone()
        return dict(found) if found else None


__all__ = [
    "REVISE_LINK_WINDOW_S",
    "STATUS_CANCELLED",
    "STATUS_DISPATCHED",
    "STATUS_FAILED",
    "STATUS_OPEN",
    "STATUS_REVISED",
    "STATUS_SENDING",
    "SendAsStore",
]
