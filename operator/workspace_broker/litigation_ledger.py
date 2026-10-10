"""Broker-owned ledger for litigation status jobs: one fresh status workbook of
the firm's open litigation matters (complaint filed, per-defendant service and
answer, next court date, case status, discovery both ways, each value citing
its document), filed to the firm's own library matter.

A sibling of ``drafting_ledger`` (it imports nothing from the drafting or
demand lanes on purpose, so a change there cannot move this lane's contract).
Its own tables (``litigation_jobs``, ``litigation_spend``) on the same audit db
file, the same broker writer, monotonic states:

    queued    -> running | held | failed
    running   -> held | delivered | failed
    held      -> failed
    failed    -> running        (a resume, root only, with a reason)

WHAT EACH ENDING MEANS (the drafting lane's rule, unchanged): ``failed`` is OUR
machinery; it is resumable and the requester is told NOTHING (the reply
binding refuses it; SMD's shortfall alert carries it). ``held`` is a cause in
the firm's files or the request (an attorney with no open matters, a gate that
will not pass on what the files say); it is final and the requester is told.
``delivered`` is the filed workbook.

TWO TRIGGERS. ``request``: an administrator's email (the overlay sets the
requester and message ref from the verified inbound). ``scheduled``: the
weekday cron's pre_run (or root), with NO requester in the envelope; the
broker fills the requester from the skill's authored ``scheduled_recipients``
and the message ref as ``scheduled:<Pacific date>``, so the unique index on
``request_ref`` makes one scheduled job per Pacific date.

THE QUEUE FILE (``<queue>/<job_id>.json``) is the runner's input, exactly::

    {"kind": "litigation", "job_id", "trigger", "requester", "message_ref",
     "request_text", "scope": {"attorney_staff_ids": [...]} | {"all": true},
     "file_to_matter_id", "file_to_matter_number", "folder_name"}

THE RUNNER'S REPORT is counts and file metadata only (``REPORT_FIELDS``):
nothing in this ledger names a matter, a party or a date from a file.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid

STATES = ("queued", "running", "held", "delivered", "failed")
TERMINAL = frozenset({"delivered", "failed"})
#: The audit type each state writes. ``queued`` is the submit, so it writes
#: SUBMITTED (the vocabulary's word for the request edge in every lane).
AUDIT_TYPE = {s: f"LITIGATION_JOB_{'SUBMITTED' if s == 'queued' else s.upper()}" for s in STATES}
_ALLOWED_NEXT = {
    "queued": {"running", "held", "failed"},
    "running": {"held", "delivered", "failed"},
    "held": {"failed"},
    "delivered": set(),
    "failed": {"running"},
}
TRIGGERS = ("request", "scheduled")
SKILL_NAME = "litigation-status"
KIND = "litigation"
MAX_REQUEST_TEXT = 20_000
#: The integer counts the runner reports. Counts only: never a matter fact.
COUNT_FIELDS = ("matters_total", "matters_reread", "flags_new")
#: Everything a record may carry besides the state.
REPORT_FIELDS = ("cents", "reason", "stage", "folder_id", "files", *COUNT_FIELDS)

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_MATTER_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
_SCHEDULED_REF = re.compile(r"^scheduled:\d{4}-\d{2}-\d{2}$")
_FOLDER = re.compile(r"^[A-Za-z0-9 ._()-]{1,80}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS litigation_jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "state TEXT NOT NULL, "
    "trigger TEXT NOT NULL, "
    "requester TEXT NOT NULL, "
    "request_ref TEXT NOT NULL, "
    "scope_key TEXT NOT NULL, "
    "scope_json TEXT NOT NULL, "
    "file_to_matter_id TEXT NOT NULL, "
    "envelope_digest TEXT NOT NULL, "
    "stage TEXT, "
    "cents INTEGER NOT NULL DEFAULT 0, "
    "matters_total INTEGER, "
    "matters_reread INTEGER, "
    "flags_new INTEGER, "
    "reason TEXT, "
    "folder_id TEXT, "
    "delivery_json TEXT, "
    "reply_sent_at TEXT, "
    "attempt INTEGER NOT NULL DEFAULT 1, "
    "reply_key TEXT"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_litigation_jobs_created ON litigation_jobs(created_at)"
#: One job per request email, and (``scheduled:<date>``) one scheduled job per Pacific date.
CREATE_UNIQUE_REF_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_litigation_jobs_request_ref ON litigation_jobs(request_ref)"
)
CREATE_SPEND_SQL = (
    "CREATE TABLE IF NOT EXISTS litigation_spend (job_id TEXT NOT NULL, at TEXT NOT NULL, cents INTEGER NOT NULL)"
)
CREATE_SPEND_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_litigation_spend_at ON litigation_spend(at)"
UNFINISHED = ("queued", "running")
PROJECTION = (
    "created_at",
    "updated_at",
    "state",
    "trigger",
    "requester",
    "stage",
    "cents",
    "matters_total",
    "matters_reread",
    "flags_new",
    "reason",
    "folder_id",
    "reply_sent_at",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


class SubmitRefused(ValueError):
    """A valid request the ledger will not queue NOW."""

    def __init__(self, message: str, job_id: str | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scope(raw: Any) -> dict[str, Any]:
    if raw == {"all": True}:
        return {"all": True}
    if isinstance(raw, dict) and set(raw) == {"attorney_staff_ids"}:
        ids = raw["attorney_staff_ids"]
        if isinstance(ids, list) and 0 < len(ids) <= 50 and all(isinstance(i, str) and _UUID.match(i) for i in ids):
            return {"attorney_staff_ids": sorted({i.strip().lower() for i in ids})}
    raise EnvelopeError('scope must be {"all": true} or {"attorney_staff_ids": [staff id, ...]}')


def scope_key(scope: dict[str, Any]) -> str:
    return "all" if scope.get("all") is True else "staff:" + ",".join(scope["attorney_staff_ids"])


def validate_envelope(req: Any) -> dict[str, Any]:
    """The RESOLVED envelope (the broker's, after requester fill-in and
    attorney resolution), normalized, or ``EnvelopeError``."""
    keys = {
        "trigger",
        "requester",
        "message_ref",
        "request_text",
        "scope",
        "file_to_matter_id",
        "file_to_matter_number",
        "folder_name",
    }
    if not isinstance(req, dict) or set(req) != keys:
        raise EnvelopeError(f"a litigation envelope carries exactly {sorted(keys)}")
    trigger = req["trigger"]
    if trigger not in TRIGGERS:
        raise EnvelopeError(f"trigger must be one of {list(TRIGGERS)}")
    who = req["requester"]
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise EnvelopeError("requester must be the requester's email address")
    ref = req["message_ref"]
    pattern = _SCHEDULED_REF if trigger == "scheduled" else _MESSAGE_ID
    if not (isinstance(ref, str) and pattern.match(ref)):
        raise EnvelopeError(
            "message_ref must be scheduled:<YYYY-MM-DD>"
            if trigger == "scheduled"
            else "message_ref must be the request email's internetMessageId"
        )
    text = req["request_text"]
    if not (isinstance(text, str) and text.strip() and len(text) <= MAX_REQUEST_TEXT):
        raise EnvelopeError(f"request_text must be the request verbatim, 1 to {MAX_REQUEST_TEXT} characters")
    mid, number = req["file_to_matter_id"], req["file_to_matter_number"]
    if not (isinstance(mid, str) and _UUID.match(mid)):
        raise EnvelopeError("file_to_matter_id must be the library matter's id")
    if not (isinstance(number, str) and _MATTER_NUMBER.match(number)):
        raise EnvelopeError("file_to_matter_number must be the library matter number")
    folder = req["folder_name"]
    if not (isinstance(folder, str) and _FOLDER.match(folder.strip())):
        raise EnvelopeError("folder_name must be the authored folder name")
    return {
        "trigger": trigger,
        "requester": who.strip().lower(),
        "message_ref": ref,
        "request_text": text,
        "scope": _scope(req["scope"]),
        "file_to_matter_id": mid.strip().lower(),
        "file_to_matter_number": number,
        "folder_name": folder.strip(),
    }


def queue_record(env: dict[str, Any], job_id: str) -> dict[str, Any]:
    """The runner's job file, from a validated envelope. Exactly these keys."""
    return {"kind": KIND, "job_id": job_id, **env}


def _files(raw: Any) -> list[dict[str, Any]]:
    """The runner's files list, reduced to the metadata the ledger keeps."""
    if not isinstance(raw, list):
        raise ValueError("files must be a list")
    out = []
    for f in raw[:20]:
        if not isinstance(f, dict):
            raise ValueError("each file must be an object")
        size = f.get("size")
        sha = str(f.get("sha256") or "")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or not _SHA256.match(sha):
            raise ValueError("each file carries an integer size and a sha256")
        out.append(
            {
                "role": str(f.get("role") or "")[:40],
                "name": str(f.get("name") or "")[:200],
                "size": size,
                "sha256": sha,
                "file_id": str(f.get("file_id") or "")[:80],
            }
        )
    return out


class LitigationLedger:
    """Per-operation sqlite connections, ``journal_mode=DELETE``: the broker
    and the runner reach this file as different uids."""

    def __init__(self, db_path: str, queue_dir: str | Path) -> None:
        self._db_path = db_path
        self.queue_dir = Path(queue_dir)
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
            for sql in (CREATE_SQL, CREATE_INDEX_SQL, CREATE_UNIQUE_REF_SQL, CREATE_SPEND_SQL, CREATE_SPEND_INDEX_SQL):
                conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def spend_between(self, start: str, end: str, exclude: str = "") -> int:
        """Cents WRITTEN in [start, end), without ``exclude``'s."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cents), 0) AS c FROM litigation_spend WHERE at >= ? AND at < ? AND job_id != ?",
                (start, end, exclude),
            ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def unfinished_for_scope(self, key: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM litigation_jobs WHERE scope_key=? AND state IN ('queued','running') "
                "ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def submit(self, envelope: dict[str, Any]) -> str:
        """Row, then queue file, so a job file on disk always has its row. The
        duplicate-ref and same-scope checks run inside one write transaction."""
        env = validate_envelope(envelope)
        key = scope_key(env["scope"])
        job_id = _ulid()
        now = _iso_utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute("SELECT id FROM litigation_jobs WHERE request_ref=?", (env["message_ref"],)).fetchone()
            if dup is not None:
                conn.rollback()
                what = "today's scheduled run" if env["trigger"] == "scheduled" else "that request email"
                raise SubmitRefused(f"{what} already queued litigation job {dup['id']}", dup["id"])
            twin = conn.execute(
                "SELECT id FROM litigation_jobs WHERE scope_key=? AND state IN ('queued','running') "
                "ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            if twin is not None:
                conn.rollback()
                raise SubmitRefused(
                    f"a status list for the same scope is already underway as job {twin['id']}", twin["id"]
                )
            conn.execute(
                "INSERT INTO litigation_jobs (id, created_at, updated_at, state, trigger, requester, request_ref, "
                "scope_key, scope_json, file_to_matter_id, envelope_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    now,
                    now,
                    "queued",
                    env["trigger"],
                    env["requester"],
                    env["message_ref"],
                    key,
                    json.dumps(env["scope"], sort_keys=True),
                    env["file_to_matter_id"],
                    digest(env),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps(queue_record(env, job_id), indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")
        return job_id

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM litigation_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM litigation_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
            ]
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """The row as the agent sees it: counts and the workbook's metadata, no
        request text, no message ref, no scope."""
        out: dict[str, Any] = {"job_id": row.get("id"), **{k: row.get(k) for k in PROJECTION}}
        delivery = json.loads(row["delivery_json"]) if row.get("delivery_json") else {}
        files = delivery.get("files") if isinstance(delivery, dict) else None
        workbook = next((f for f in files or [] if isinstance(f, dict) and f.get("role") == "workbook"), None)
        out["file"] = (
            {"name": workbook.get("name"), "size": workbook.get("size"), "sha256": workbook.get("sha256")}
            if workbook
            else None
        )
        return out

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition; returns the row after (with ``prev_state``). A
        same-state record is a note (merges fields)."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        unknown = set(fields) - set(REPORT_FIELDS)
        if unknown:
            raise ValueError(f"litigation_job_record carries only {list(REPORT_FIELDS)}; got {sorted(unknown)}")
        for key in ("cents", *COUNT_FIELDS):
            value = fields.get(key)
            if key in fields and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{key} must be a non-negative int")
        files = _files(fields["files"]) if "files" in fields else None
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state, cents FROM litigation_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            if state != cur["state"] and state not in _ALLOWED_NEXT[cur["state"]]:
                conn.rollback()
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            now = _iso_utc()
            cents = fields.get("cents")
            if isinstance(cents, int) and cents > int(cur["cents"] or 0):
                conn.execute(
                    "INSERT INTO litigation_spend (job_id, at, cents) VALUES (?,?,?)",
                    (job_id, now, cents - int(cur["cents"] or 0)),
                )

            def text(key: str, limit: int) -> str | None:
                return str(fields.get(key) or "")[:limit] or None

            conn.execute(
                "UPDATE litigation_jobs SET state=?, updated_at=?, "
                "attempt=attempt + CASE WHEN ? THEN 1 ELSE 0 END, "
                "cents=CASE WHEN ? THEN ? ELSE cents END, "
                "matters_total=CASE WHEN ? THEN ? ELSE matters_total END, "
                "matters_reread=CASE WHEN ? THEN ? ELSE matters_reread END, "
                "flags_new=CASE WHEN ? THEN ? ELSE flags_new END, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "stage=CASE WHEN ? THEN ? ELSE stage END, "
                "folder_id=CASE WHEN ? THEN ? ELSE folder_id END, "
                "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END "
                "WHERE id=?",
                (
                    state,
                    now,
                    cur["state"] == "failed" and state == "running",
                    "cents" in fields,
                    fields.get("cents"),
                    "matters_total" in fields,
                    fields.get("matters_total"),
                    "matters_reread" in fields,
                    fields.get("matters_reread"),
                    "flags_new" in fields,
                    fields.get("flags_new"),
                    "reason" in fields,
                    text("reason", 500),
                    "stage" in fields,
                    text("stage", 40),
                    "folder_id" in fields,
                    text("folder_id", 120),
                    files is not None,
                    json.dumps({"files": files}, sort_keys=True) if files is not None else None,
                    job_id,
                ),
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM litigation_jobs WHERE id=?", (job_id,)).fetchone())
            row["prev_state"] = cur["state"]
            return row
        finally:
            conn.close()

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        """True exactly once per (job, key); the reply binding's key is
        ``<attempt>:<state>``."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE litigation_jobs SET reply_sent_at=?, reply_key=? WHERE id=? "
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
                "UPDATE litigation_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
                (job_id, key),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "AUDIT_TYPE",
    "COUNT_FIELDS",
    "KIND",
    "PROJECTION",
    "REPORT_FIELDS",
    "SKILL_NAME",
    "STATES",
    "TERMINAL",
    "TRIGGERS",
    "EnvelopeError",
    "LitigationLedger",
    "SubmitRefused",
    "queue_record",
    "scope_key",
    "validate_envelope",
]
