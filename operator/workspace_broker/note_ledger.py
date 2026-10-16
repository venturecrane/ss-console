"""Broker-owned ledger for SMD notes: words SMD has the Operator say to a firm
colleague, in her own thread, on a request she sent.

WHY IT EXISTS (2026-10-15). The Operator filed a document on SMD's request and
could not tell the person who asked: a turn opened by an inbound email may
answer only its sender, and the only SMD-initiated way for the Operator to
speak to the firm was a job's completion wake (``medchron/deliver_by_smd.py``).
A note is that wake with no job behind it: SMD records the exact words against
the colleague's request email (``note_verbs.note_record``, root only), and the
smd-note skill's DELIVER mode sends them through the verified reply binding,
which is job-generic (``reply_binding.py``, kind ``note_job``).

ONE ROW PER (request email, words). There is no queue and no runner: a note is
born ``delivered`` (its content is final the moment SMD approved it), attempt 1,
so its reply key is ``note_job:<id>:1:delivered`` and it is answered once.
Recording the same words on the same email again is refused by the UNIQUE
index, so a re-run of the root command can never become a second send.

The text is kept here (the DELIVER turn reads it word for word through
``note_job_status``) and NEVER in an audit row: ``NOTE_JOB_RECORDED`` carries
the id, the text's digest, the requester's key and who approved it.

The connection discipline is ``drafting_ledger``'s: per-operation sqlite
connections, ``journal_mode=DELETE``, because the broker and the runner reach
the audit db as different uids.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .audit_ledger import _iso_utc, _ulid

STATES = ("delivered",)
SKILL_NAME = "smd-note"
AUDIT_TYPE = "NOTE_JOB_RECORDED"

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS note_jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "requester TEXT NOT NULL, "
    "request_ref TEXT NOT NULL, "
    "text TEXT NOT NULL, "
    "text_digest TEXT NOT NULL, "
    "approved_by TEXT NOT NULL, "
    "recorded_by TEXT NOT NULL, "
    "state TEXT NOT NULL DEFAULT 'delivered', "
    "attempt INTEGER NOT NULL DEFAULT 1, "
    "reply_sent_at TEXT, "
    "reply_key TEXT, "
    "UNIQUE(request_ref, text_digest)"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_note_jobs_created ON note_jobs(created_at)"


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NoteExists(ValueError):
    """These words were already recorded on this request email."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"these words were already recorded on that email as note job {job_id}")
        self.job_id = job_id


class NoteLedger:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(CREATE_SQL)
            conn.execute(CREATE_INDEX_SQL)
            conn.commit()
        finally:
            conn.close()

    def create(self, *, requester: str, request_ref: str, text: str, approved_by: str, recorded_by: str) -> str:
        """One note row, born ``delivered``. ``NoteExists`` when these exact
        words were already recorded on this email."""
        job_id = _ulid()
        digest = text_digest(text)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute(
                "SELECT id FROM note_jobs WHERE request_ref=? AND text_digest=?", (request_ref, digest)
            ).fetchone()
            if dup is not None:
                conn.rollback()
                raise NoteExists(dup["id"])
            conn.execute(
                "INSERT INTO note_jobs (id, created_at, requester, request_ref, text, text_digest, approved_by, "
                "recorded_by) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, _iso_utc(), requester, request_ref, text, digest, approved_by, recorded_by),
            )
            conn.commit()
        finally:
            conn.close()
        return job_id

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM note_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def find(self, request_ref: str, text: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM note_jobs WHERE request_ref=? AND text_digest=?", (request_ref, text_digest(text))
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        """True exactly once per (job, key); the reply binding's key is
        ``<attempt>:<state>`` (``drafting_ledger.mark_replied``)."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE note_jobs SET reply_sent_at=?, reply_key=? WHERE id=? "
                "AND (reply_key IS NULL OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)",
                (_iso_utc(), key, job_id, key),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def unmark_replied(self, job_id: str, key: str) -> None:
        """Undo a mark whose send Graph refused outright (nothing was sent)."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE note_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
                (job_id, key),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["AUDIT_TYPE", "SKILL_NAME", "STATES", "NoteExists", "NoteLedger", "text_digest"]
