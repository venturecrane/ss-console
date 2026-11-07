"""Broker-owned ledger for "set up our ..." jobs: the drafting lane's second kind
(``runners/jobs/jobs/library_setup_lane.py``), on its own table in the drafting
lane's database file.

WHY ITS OWN TABLE. A drafting row is a document on a client matter, metered by
the cycle's allowance, one unfinished per class per matter. A setup job reads
across the firm's matters, files nothing, costs nothing and names no client
matter: its filing target is the firm's own library matter. Stretching
``drafting_jobs`` would have to bend all three rules.

SERVED THROUGH THE DRAFTING VERBS. The lane reads and records a setup job with
``drafting_job_status`` / ``drafting_job_record`` (``drafting_verbs.
drafting_dispatch`` hands a setup id here), and the reply binding reads it
through ``DraftingOrSetup``, so a setup job's reply rides the drafting job's
code-sent reply (``reply_text.CODE_SENT_KINDS``) in the words of
``library_setup_wording``. Audit rows reuse the drafting job's types, with
``job_kind: library_setup`` in their metadata.

STATES:

    submitted -> running | held | failed
    running   -> observed | delivered | held | failed
    held      -> failed

``observed`` is a run on a seat whose ``logic_in_code.library_setup`` is off: it
is never replied on (not a replyable state). ``delivered`` is a reply owed.
``held`` and ``failed`` are SMD's; the firm is told nothing.

THE ROW holds ids, codes, counts, dates and the report the job recorded: no
document text and no file name.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid

JOB_KIND = "library_setup"
KINDS = ("mediation_brief", "memo")
STATES = ("submitted", "running", "observed", "delivered", "held", "failed")
TERMINAL = frozenset({"observed", "delivered", "failed"})
_ALLOWED_NEXT = {
    "submitted": {"running", "held", "failed"},
    "running": {"observed", "delivered", "held", "failed"},
    "held": {"failed"},
    "observed": set(),
    "delivered": set(),
    "failed": set(),
}
MAX_ATTORNEY = 80
MAX_REQUEST_TEXT = 20_000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
_ATTORNEY = re.compile(r"^[^\W\d_][\w .'-]{0,79}$")
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS library_setup_jobs ("
    "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL, "
    "document_class TEXT NOT NULL, matter_number TEXT NOT NULL, requester TEXT NOT NULL, "
    "request_ref TEXT NOT NULL, envelope_digest TEXT NOT NULL, live INTEGER NOT NULL, "
    "attorney_named TEXT, report_json TEXT, reason TEXT, cents INTEGER NOT NULL DEFAULT 0, "
    "attempt INTEGER NOT NULL DEFAULT 1, reply_sent_at TEXT, reply_key TEXT)"
)
#: The columns a row is read with, plus what the reply chain expects of a
#: drafting row (no matter, no files, no folder).
_SELECT = (
    "SELECT *, 'library_setup' AS job_kind, '' AS matter_id, '' AS file_to_matter_id, "
    "NULL AS delivery_json, NULL AS folder_id, NULL AS revise_of FROM library_setup_jobs WHERE id=?"
)
PROJECTION = (
    "id",
    "job_kind",
    "created_at",
    "updated_at",
    "state",
    "document_class",
    "matter_number",
    "requester",
    "live",
    "attorney_named",
    "reason",
    "cents",
    "reply_sent_at",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue; the message names the field."""


class Underway(ValueError):
    """A setup of this kind, for this attorney or the firm, has not finished."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"library setup job {job_id} is underway")
        self.job_id = job_id


def validate_envelope(req: Any) -> dict[str, Any]:
    keys = {"kind", "attorney", "requested_by", "request_ref", "request_text"}
    if not isinstance(req, dict) or set(req) != keys:
        raise EnvelopeError(f"a library setup envelope carries exactly {sorted(keys)}")
    if req["kind"] not in KINDS:
        raise EnvelopeError(f"kind must be one of {list(KINDS)}")
    attorney = req["attorney"]
    if attorney is not None:
        attorney = " ".join(str(attorney).split()) if isinstance(attorney, str) else ""
        if not _ATTORNEY.match(attorney) or len(attorney) > MAX_ATTORNEY:
            raise EnvelopeError("attorney must be the attorney's name as the request gives it, or null")
    who, ref, text = req["requested_by"], req["request_ref"], req["request_text"]
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise EnvelopeError("requested_by must be the requester's email address")
    if not (isinstance(ref, str) and _MESSAGE_ID.match(ref)):
        raise EnvelopeError("request_ref must be the request email's internetMessageId")
    if not (isinstance(text, str) and text.strip() and len(text) <= MAX_REQUEST_TEXT):
        raise EnvelopeError("request_text must be the request verbatim")
    return {"kind": req["kind"], "attorney": attorney or None, "requested_by": who.strip().lower(), "request_ref": ref}


def queue_record(env: dict[str, Any], job_id: str, library_number: str, live: bool) -> dict[str, Any]:
    """The runner's job file. ``document_class`` is the kind, so the drafting
    lane's own reads of a class (its wake, its card) name the right one."""
    return {
        "kind": JOB_KIND,
        "job_id": job_id,
        "setup_kind": env["kind"],
        "document_class": env["kind"],
        "attorney_named": env["attorney"],
        "requester": env["requested_by"],
        "message_ref": env["request_ref"],
        "library_matter_number": library_number,
        "live": live,
    }


class SetupLedger:
    """Per-operation connections, ``journal_mode=DELETE``, as the drafting ledger."""

    def __init__(self, db_path: str, queue_dir: str | Path) -> None:
        self._db_path = db_path
        self.queue_dir = Path(queue_dir)
        conn = self._connect()
        try:
            conn.execute(CREATE_SQL)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def owns(self, job_id: str) -> bool:
        return bool(_ULID_RE.match(job_id or "")) and self.read(job_id) is not None

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(_SELECT, (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        out = {k: row.get(k) for k in PROJECTION}
        out["live"] = bool(out["live"])
        try:
            out["report"] = json.loads(row["report_json"]) if row.get("report_json") else None
        except ValueError:
            out["report"] = None
        return out

    def submit(self, envelope: Any, *, library_number: str, live: bool) -> str:
        """Row, then queue file. Raises ``EnvelopeError`` or ``Underway``."""
        env = validate_envelope(envelope)
        job_id, now = _ulid(), _iso_utc()
        digest = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            twin = conn.execute(
                "SELECT id FROM library_setup_jobs WHERE document_class=? AND COALESCE(lower(attorney_named),'')=? "
                "AND state IN ('submitted','running') LIMIT 1",
                (env["kind"], (env["attorney"] or "").lower()),
            ).fetchone()
            if twin is not None:
                conn.rollback()
                raise Underway(twin["id"])
            conn.execute(
                "INSERT INTO library_setup_jobs (id, created_at, updated_at, state, document_class, matter_number, "
                "requester, request_ref, envelope_digest, live, attorney_named) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, now, now, "submitted", env["kind"], library_number, env["requested_by"],
                 env["request_ref"], digest, int(live), env["attorney"]),
            )  # fmt: skip
            conn.commit()
        finally:
            conn.close()
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        record = queue_record(env, job_id, library_number, live)
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")
        return job_id

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """One transition (a same-state record is a note); the row after."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        report = fields.get("report")
        if report is not None and not isinstance(report, dict):
            raise ValueError("report must be an object")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state FROM library_setup_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            if state != cur["state"] and state not in _ALLOWED_NEXT[cur["state"]]:
                conn.rollback()
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            conn.execute(
                "UPDATE library_setup_jobs SET state=?, updated_at=?, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "report_json=CASE WHEN ? THEN ? ELSE report_json END WHERE id=?",
                (state, _iso_utc(), "reason" in fields, str(fields.get("reason") or "")[:500] or None,
                 report is not None, json.dumps(report, sort_keys=True) if report is not None else None, job_id),
            )  # fmt: skip
            conn.commit()
        finally:
            conn.close()
        row = self.read(job_id) or {}
        row["prev_state"] = cur["state"]
        return row

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE library_setup_jobs SET reply_sent_at=?, reply_key=? WHERE id=? "
                "AND (reply_key IS NULL OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)",
                (_iso_utc(), key, job_id, key),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def unmark_replied(self, job_id: str, key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE library_setup_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
                (job_id, key),
            )
            conn.commit()
        finally:
            conn.close()


class DraftingOrSetup:
    """The drafting ledger as the reply binding reads it: a setup job's id reads
    its setup row; every other id, and everything else, is the drafting ledger."""

    def __init__(self, drafting: Any, setup: SetupLedger) -> None:
        self._drafting = drafting
        self._setup = setup

    def _for(self, job_id: str) -> Any:
        return self._setup if self._setup.owns(job_id) else self._drafting

    def read(self, job_id: str) -> dict[str, Any] | None:
        return self._for(job_id).read(job_id)

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        return self._for(job_id).mark_replied(job_id, key)

    def unmark_replied(self, job_id: str, key: str) -> None:
        self._for(job_id).unmark_replied(job_id, key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._drafting, name)


def of_drafting(drafting: Any) -> SetupLedger:
    """The setup ledger beside a drafting ledger: the same file, the same queue."""
    return SetupLedger(drafting._db_path, drafting.queue_dir)


__all__ = [
    "JOB_KIND",
    "KINDS",
    "STATES",
    "TERMINAL",
    "DraftingOrSetup",
    "EnvelopeError",
    "SetupLedger",
    "Underway",
    "of_drafting",
    "queue_record",
    "validate_envelope",
]
