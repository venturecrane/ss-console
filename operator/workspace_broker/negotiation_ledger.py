"""Broker-owned ledger for the negotiation watch: one scheduled job per weekday
slot that enters new offers on the firm's Negotiation Details tabs, and one
NOTICE per new offer, which is what the firm is emailed about.

A sibling of ``drafting_ledger`` (it imports nothing from that lane, so a
change there cannot move this one's contract). Its own tables on the same audit
db file (``negotiation_jobs``, ``negotiation_notices``, ``negotiation_spend``),
monotonic job states:

    queued    -> running | failed
    running   -> delivered | failed
    failed    -> running        (a resume, root only, with a reason)

``failed`` is OUR machinery; the firm is told nothing (the reply binding
refuses it; SMD's shortfall alert carries it). ``delivered`` means the run
finished: every offer it entered or could not enter is a notice row.

ONE TRIGGER. ``scheduled``: the weekday cron's pre_run (or root). The broker
fills the requester from the skill's authored ``scheduled_recipients`` and the
message ref as ``scheduled:<Pacific date>T<hour>``, so the unique index on
``request_ref`` makes one job per cron slot.

NOTICES. ``record(delivered, {"notices": [...]})`` writes one row per notice,
each with its own ULID, and answers with the ids; the runner's lane gives each
its own completion wake. A notice holds the code-composed message (matter
number, the party, the amount and date, and whether it was entered); the reply
binding sends it, once, as one new email to the notice's requester. Re-recording
``delivered`` writes no second set.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid

STATES = ("queued", "running", "delivered", "failed")
TERMINAL = frozenset({"delivered", "failed"})
AUDIT_TYPE = {s: f"NEGOTIATION_JOB_{'SUBMITTED' if s == 'queued' else s.upper()}" for s in STATES}
_ALLOWED_NEXT = {
    "queued": {"running", "failed"},
    "running": {"delivered", "failed"},
    "delivered": set(),
    "failed": {"running"},
}
SKILL_NAME = "negotiation-watch"
KIND = "negotiation"
COUNT_FIELDS = ("matters_total", "matters_seeded", "docs_read", "docs_failed")
REPORT_FIELDS = ("cents", "reason", "stage", "notices", "wake", *COUNT_FIELDS)
NOTICE_STATUSES = ("entered", "not_entered")
MAX_NOTICES = 200
MAX_NOTICE_TEXT = 1000

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_SLOT_REF = re.compile(r"^scheduled:\d{4}-\d{2}-\d{2}T\d{2}$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WORD = re.compile(r"^[a-z]{3,40}$")
_SEED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MATTER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

CREATE_JOBS_SQL = (
    "CREATE TABLE IF NOT EXISTS negotiation_jobs ("
    "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL, "
    "trigger TEXT NOT NULL, requester TEXT NOT NULL, request_ref TEXT NOT NULL, envelope_digest TEXT NOT NULL, "
    "stage TEXT, cents INTEGER NOT NULL DEFAULT 0, matters_total INTEGER, matters_seeded INTEGER, "
    "docs_read INTEGER, docs_failed INTEGER, notices_count INTEGER, reason TEXT, "
    "attempt INTEGER NOT NULL DEFAULT 1, reply_sent_at TEXT, reply_key TEXT)"
)
CREATE_NOTICES_SQL = (
    "CREATE TABLE IF NOT EXISTS negotiation_notices ("
    "id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created_at TEXT NOT NULL, requester TEXT NOT NULL, "
    "matter_id TEXT NOT NULL, matter_number TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL, "
    "reply_sent_at TEXT, reply_key TEXT, wake_failed INTEGER NOT NULL DEFAULT 0)"
)
CREATE_SQLS = (
    CREATE_JOBS_SQL,
    "CREATE INDEX IF NOT EXISTS idx_negotiation_jobs_created ON negotiation_jobs(created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_negotiation_jobs_request_ref ON negotiation_jobs(request_ref)",
    CREATE_NOTICES_SQL,
    "CREATE INDEX IF NOT EXISTS idx_negotiation_notices_job ON negotiation_notices(job_id)",
    "CREATE TABLE IF NOT EXISTS negotiation_spend (job_id TEXT NOT NULL, at TEXT NOT NULL, cents INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_negotiation_spend_at ON negotiation_spend(at)",
)
PROJECTION = (
    "created_at",
    "updated_at",
    "state",
    "trigger",
    "stage",
    "cents",
    *COUNT_FIELDS,
    "notices_count",
    "reason",
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


def _usd(req: dict[str, Any], key: str) -> float:
    v = req[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise EnvelopeError(f"{key} must be a positive number")
    return float(v)


def validate_envelope(req: Any) -> dict[str, Any]:
    """The RESOLVED envelope (the broker's, after the requester and the
    authored settings are filled in), normalized, or ``EnvelopeError``."""
    keys = {
        "trigger",
        "requester",
        "message_ref",
        "request_text",
        "matter_statuses",
        "negotiation_design",
        "firm_words",
        "seed_saved_before",
        "per_job_cap_usd",
        "monthly_budget_usd",
    }
    if not isinstance(req, dict) or set(req) != keys:
        raise EnvelopeError(f"a negotiation envelope carries exactly {sorted(keys)}")
    if req["trigger"] != "scheduled":
        raise EnvelopeError('trigger must be "scheduled"')
    who = req["requester"]
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise EnvelopeError("requester must be the scheduled recipient's email address")
    if not (isinstance(req["message_ref"], str) and _SLOT_REF.match(req["message_ref"])):
        raise EnvelopeError("message_ref must be scheduled:<YYYY-MM-DD>T<HH>")
    text = req["request_text"]
    if not (isinstance(text, str) and 0 < len(text.strip()) <= 200):
        raise EnvelopeError("request_text must be the scheduled run's label")
    statuses = req["matter_statuses"]
    if not (isinstance(statuses, list) and statuses and all(isinstance(s, str) and s.strip() for s in statuses)):
        raise EnvelopeError("matter_statuses must be a list of Smokeball statuses")
    design = str(req["negotiation_design"] or "").strip().lower()
    if design and not _GUID.match(design):
        raise EnvelopeError("negotiation_design must be a layout design guid, or empty")
    seed = req["seed_saved_before"]
    if not (seed == "" or (isinstance(seed, str) and _SEED.match(seed))):
        raise EnvelopeError("seed_saved_before must be YYYY-MM-DDTHH:MM:SSZ, or empty")
    words = req["firm_words"]
    if not (isinstance(words, list) and all(isinstance(w, str) and _WORD.match(w) for w in words)):
        raise EnvelopeError("firm_words must be lowercase words")
    return {
        "trigger": "scheduled",
        "requester": who.strip().lower(),
        "message_ref": req["message_ref"],
        "request_text": text.strip(),
        "matter_statuses": [s.strip() for s in statuses],
        "negotiation_design": design,
        "firm_words": list(words),
        "seed_saved_before": seed,
        "per_job_cap_usd": _usd(req, "per_job_cap_usd"),
        "monthly_budget_usd": _usd(req, "monthly_budget_usd"),
    }


def queue_record(env: dict[str, Any], job_id: str) -> dict[str, Any]:
    return {"kind": KIND, "job_id": job_id, **env}


def _notices(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) > MAX_NOTICES:
        raise ValueError(f"notices must be a list of at most {MAX_NOTICES}")
    out = []
    for n in raw:
        if not isinstance(n, dict):
            raise ValueError("each notice must be an object")
        mid, number = str(n.get("matter_id") or ""), str(n.get("matter_number") or "")
        status, text = n.get("status"), n.get("text")
        if not _MATTER_ID.match(mid) or len(number) > 64 or status not in NOTICE_STATUSES:
            raise ValueError("each notice names its matter id, matter number and status")
        if not (isinstance(text, str) and text.strip() and len(text) <= MAX_NOTICE_TEXT):
            raise ValueError(f"each notice carries its message, 1 to {MAX_NOTICE_TEXT} characters")
        if "—" in text:
            raise ValueError("a notice's message carries no em dash")
        out.append({"matter_id": mid, "matter_number": number, "status": status, "text": text.strip()})
    return out


def _check_fields(state: str, fields: dict[str, Any]) -> None:
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    unknown = set(fields) - set(REPORT_FIELDS)
    if unknown:
        raise ValueError(f"negotiation_job_record carries only {list(REPORT_FIELDS)}; got {sorted(unknown)}")
    for key in ("cents", *COUNT_FIELDS):
        value = fields.get(key)
        if key in fields and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValueError(f"{key} must be a non-negative int")


def _insert_notices(
    conn: sqlite3.Connection, job_id: str, now: str, requester: str, notices: list[dict[str, str]]
) -> list[str]:
    """One row per notice, inside the caller's write transaction; their ids."""
    ids = []
    for n in notices:
        nid = _ulid()
        conn.execute(
            "INSERT INTO negotiation_notices (id, job_id, created_at, requester, matter_id, matter_number, "
            "status, text) VALUES (?,?,?,?,?,?,?,?)",
            (nid, job_id, now, requester, n["matter_id"], n["matter_number"], n["status"], n["text"]),
        )
        ids.append(nid)
    return ids


def _pair(fields: dict[str, Any], key: str) -> tuple[bool, Any]:
    return key in fields, fields.get(key)


def subject(row: dict[str, Any]) -> str:
    """The fixed subject of a notice's one email (the broker's, never the model's)."""
    number = re.sub(r"[^A-Za-z0-9 ._-]", "", str(row.get("matter_number") or ""))[:64] or "a matter"
    return f"New offer, matter {number}"


class NegotiationLedger:
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
            for sql in CREATE_SQLS:
                conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def spend_between(self, start: str, end: str, exclude: str = "") -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cents), 0) AS c FROM negotiation_spend WHERE at >= ? AND at < ? AND job_id != ?",
                (start, end, exclude),
            ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def unfinished(self) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM negotiation_jobs WHERE state IN ('queued','running') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def submit(self, envelope: dict[str, Any]) -> str:
        """Row, then queue file. The duplicate-slot and one-at-a-time checks run
        inside one write transaction."""
        env = validate_envelope(envelope)
        job_id = _ulid()
        now = _iso_utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute("SELECT id FROM negotiation_jobs WHERE request_ref=?", (env["message_ref"],)).fetchone()
            if dup is not None:
                conn.rollback()
                raise SubmitRefused(f"this slot's scheduled run already queued negotiation job {dup['id']}", dup["id"])
            twin = conn.execute(
                "SELECT id FROM negotiation_jobs WHERE state IN ('queued','running') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if twin is not None:
                conn.rollback()
                raise SubmitRefused(f"negotiation job {twin['id']} is already underway", twin["id"])
            conn.execute(
                "INSERT INTO negotiation_jobs (id, created_at, updated_at, state, trigger, requester, request_ref, "
                "envelope_digest) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, now, now, "queued", env["trigger"], env["requester"], env["message_ref"], digest(env)),
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

    def read_job(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM negotiation_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def read_notice(self, notice_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM negotiation_notices WHERE id=?", (notice_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def read(self, ident: str) -> dict[str, Any] | None:
        """A job row, or a notice in the job-row shape the reply binding reads
        (``notice`` true, ``state`` delivered, ``trigger`` scheduled)."""
        job = self.read_job(ident)
        if job is not None:
            return {**job, "notice": False}
        n = self.read_notice(ident)
        if n is None:
            return None
        return {
            **n,
            "notice": True,
            "state": "delivered",
            "trigger": "scheduled",
            "request_ref": f"notice:{n['id']}",
            "attempt": 1,
        }

    def notices_of(self, job_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM negotiation_notices WHERE job_id=? ORDER BY rowid", (job_id,))
            ]
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM negotiation_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
            ]
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """A job's counts, or a notice's message: what the completion turn sends."""
        if row.get("notice"):
            return {
                "job_id": row.get("id"),
                "kind": "notice",
                "state": "delivered",
                "matter_number": row.get("matter_number"),
                "status": row.get("status"),
                "message": row.get("text"),
                "subject": subject(row),
                "reply_sent_at": row.get("reply_sent_at"),
            }
        return {"job_id": row.get("id"), "kind": "job", **{k: row.get(k) for k in PROJECTION}}

    def _record_wake(self, ident: str, wake: Any) -> dict[str, Any]:
        """A lost wake (the daemon's five failures), noted on a notice or a job."""
        conn = self._connect()
        try:
            conn.execute("UPDATE negotiation_notices SET wake_failed=1 WHERE id=?", (ident,))
            conn.commit()
        finally:
            conn.close()
        row = self.read(ident)
        if row is None:
            raise ValueError(f"no such job or notice {ident}")
        return {**row, "prev_state": row["state"], "notice_ids": [], "wake": wake}

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition; returns the row after (``prev_state``, and
        ``notice_ids`` for a delivery). A same-state record is a note."""
        _check_fields(state, fields)
        if set(fields) == {"wake"} and self.read_job(job_id) is None:
            return self._record_wake(job_id, fields["wake"])
        notices = _notices(fields["notices"]) if "notices" in fields else []
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT * FROM negotiation_jobs WHERE id=?", (job_id,)).fetchone()
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
                    "INSERT INTO negotiation_spend (job_id, at, cents) VALUES (?,?,?)",
                    (job_id, now, cents - int(cur["cents"] or 0)),
                )
            first_delivery = state == "delivered" and cur["state"] != "delivered"
            ids = _insert_notices(conn, job_id, now, cur["requester"], notices) if first_delivery else []
            conn.execute(
                "UPDATE negotiation_jobs SET state=?, updated_at=?, "
                "attempt=attempt + CASE WHEN ? THEN 1 ELSE 0 END, "
                "cents=CASE WHEN ? THEN ? ELSE cents END, "
                "matters_total=CASE WHEN ? THEN ? ELSE matters_total END, "
                "matters_seeded=CASE WHEN ? THEN ? ELSE matters_seeded END, "
                "docs_read=CASE WHEN ? THEN ? ELSE docs_read END, "
                "docs_failed=CASE WHEN ? THEN ? ELSE docs_failed END, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "stage=CASE WHEN ? THEN ? ELSE stage END, "
                "notices_count=CASE WHEN ? THEN ? ELSE notices_count END "
                "WHERE id=?",
                (
                    state,
                    now,
                    cur["state"] == "failed" and state == "running",
                    *_pair(fields, "cents"),
                    *_pair(fields, "matters_total"),
                    *_pair(fields, "matters_seeded"),
                    *_pair(fields, "docs_read"),
                    *_pair(fields, "docs_failed"),
                    "reason" in fields,
                    str(fields.get("reason") or "")[:500] or None,
                    "stage" in fields,
                    str(fields.get("stage") or "")[:40] or None,
                    first_delivery,
                    len(ids),
                    job_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        row = {**(self.read_job(job_id) or {}), "notice": False, "prev_state": cur["state"]}
        row["notice_ids"] = ids if ids else [n["id"] for n in self.notices_of(job_id)] if state == "delivered" else []
        return row

    def mark_replied(self, ident: str, key: str = "reply") -> bool:
        """True exactly once per (notice or job, key)."""
        args = (_iso_utc(), key, ident, key)
        conn = self._connect()
        try:
            if self.read_job(ident) is not None:
                cur = conn.execute(
                    "UPDATE negotiation_jobs SET reply_sent_at=?, reply_key=? WHERE id=? AND (reply_key IS NULL "
                    "OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)",
                    args,
                )
            else:
                cur = conn.execute(
                    "UPDATE negotiation_notices SET reply_sent_at=?, reply_key=? WHERE id=? AND (reply_key IS NULL "
                    "OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)",
                    args,
                )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def unmark_replied(self, ident: str, key: str) -> None:
        conn = self._connect()
        try:
            for sql in (
                "UPDATE negotiation_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
                "UPDATE negotiation_notices SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
            ):
                conn.execute(sql, (ident, key))
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "AUDIT_TYPE",
    "COUNT_FIELDS",
    "KIND",
    "SKILL_NAME",
    "STATES",
    "EnvelopeError",
    "NegotiationLedger",
    "SubmitRefused",
    "queue_record",
    "subject",
    "validate_envelope",
]
