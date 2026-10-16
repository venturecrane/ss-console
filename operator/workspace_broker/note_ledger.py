"""Broker-owned ledger for SMD notes: words SMD has the Operator say to a firm
colleague, in her own thread on a request she sent, or (2026-10-16, a
new-message note) in one new email to an authored admin.

WHY IT EXISTS (2026-10-15). The Operator filed a document on SMD's request and
could not tell the person who asked: a turn opened by an inbound email may
answer only its sender, and the only SMD-initiated way for the Operator to
speak to the firm was a job's completion wake (``medchron/deliver_by_smd.py``).
A note is that wake with no job behind it: SMD records the exact words against
the colleague's request email (``note_verbs.note_record``, root only), and the
smd-note skill's DELIVER mode sends them through the verified reply binding,
which is job-generic (``reply_binding.py``, kind ``note_job``).

ONE ROW PER (request email, words), or for a new-message note per (recipient,
subject and words): its ``request_ref`` is ``new:<key>`` and its digest covers
the subject. There is no queue and no runner: a note is
born ``delivered`` (its content is final the moment SMD approved it), attempt 1,
so its reply key is ``note_job:<id>:1:delivered`` and it is answered once.
Recording the same words on the same email again is refused by the UNIQUE
index, so a re-run of the root command can never become a second send.

The text is kept here (the DELIVER turn reads it word for word through
``note_job_status``) and NEVER in an audit row: ``NOTE_JOB_RECORDED`` carries
the id, the text's digest, the requester's key, who approved it, the mode and
(a new message) the matter numbers, never a matter's name or the subject.

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
    "trigger TEXT NOT NULL DEFAULT 'request', "
    "subject TEXT, "
    "UNIQUE(request_ref, text_digest)"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_note_jobs_created ON note_jobs(created_at)"
#: Added 2026-10-16 (the new-message note). A v1 table on a live seat gains
#: them in place; its rows are reply notes, which the defaults describe.
ADDED_COLUMNS = (("trigger", "TEXT NOT NULL DEFAULT 'request'"), ("subject", "TEXT"))
#: A note with no request email takes ``trigger='scheduled'``, the reply
#: binding's word for "no email asked for this": one new email to an authored
#: admin (``reply_binding._verify_scheduled``).
TRIGGER_REQUEST = "request"
TRIGGER_NEW_MESSAGE = "scheduled"
#: A new-message note's ``request_ref``: ``new:`` plus the recipient's key. It
#: carries no ``@`` on purpose: ``participant_lookup._job`` reads a ref with an
#: ``@`` as an RFC Message-ID.
NEW_MESSAGE_REF_PREFIX = "new:"


def text_digest(text: str, subject: str | None = None) -> str:
    """The words' digest; a new-message note's covers its subject too, so the
    same subject and words to the same person are recorded once."""
    data = text if subject is None else f"{subject}\n\n{text}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


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
            have = {r["name"] for r in conn.execute("PRAGMA table_info(note_jobs)")}
            for name, kind in ADDED_COLUMNS:
                if name in have:
                    continue
                try:
                    conn.execute(f"ALTER TABLE note_jobs ADD COLUMN {name} {kind}")
                except sqlite3.OperationalError as exc:
                    # The broker and the runner both construct the ledger: the
                    # other may have added it between our read and the ALTER.
                    if "duplicate column" not in str(exc).lower():
                        raise
            conn.commit()
        finally:
            conn.close()

    def create(
        self,
        *,
        requester: str,
        request_ref: str,
        text: str,
        approved_by: str,
        recorded_by: str,
        subject: str | None = None,
    ) -> str:
        """One note row, born ``delivered``. ``NoteExists`` when these exact
        words were already recorded on this email (or, for a new-message note,
        sent to this person under this subject). A ``subject`` makes it a
        new-message note (``trigger='scheduled'``)."""
        job_id = _ulid()
        digest = text_digest(text, subject)
        trigger = TRIGGER_REQUEST if subject is None else TRIGGER_NEW_MESSAGE
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
                "recorded_by, trigger, subject) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, _iso_utc(), requester, request_ref, text, digest, approved_by, recorded_by, trigger, subject),
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

    def find(self, request_ref: str, text: str, subject: str | None = None) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM note_jobs WHERE request_ref=? AND text_digest=?",
                (request_ref, text_digest(text, subject)),
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


__all__ = [
    "AUDIT_TYPE",
    "NEW_MESSAGE_REF_PREFIX",
    "SKILL_NAME",
    "STATES",
    "TRIGGER_NEW_MESSAGE",
    "TRIGGER_REQUEST",
    "NoteExists",
    "NoteLedger",
    "text_digest",
]
