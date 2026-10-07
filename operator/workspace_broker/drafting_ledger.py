"""Broker-owned ledger for drafting jobs: a litigation document (mediation brief,
propounded discovery, discovery responses, memo, deposition outline) a firm
administrator asks the Operator to draft, in the firm's authored house style.

A parallel copy of ``demand_ledger`` (the demand lane is live and owned
separately; this file imports nothing from it on purpose, so a change there
cannot move this lane's contract). Its own tables (``drafting_jobs``,
``drafting_spend``) on the same audit db file, the same broker writer, the
same monotonic states:

    submitted -> running | held | failed
    running   -> held | delivered | failed
    held      -> failed
    failed    -> running        (a resume, root only, with a reason)

WHAT EACH ENDING MEANS (carried from the demand lane, ss#3085 / ss#3087):
``failed`` is OUR machinery (truncation, an incomplete audit, a gate on our
own output, a render fault). It is resumable through ``drafting_job_resume``
and the requester is told NOTHING (the reply binding refuses it; SMD's
shortfall alert carries it). ``held`` is a cause in the FILE or the REQUEST
(the record lacks what the document needs, the request names no matter). It
is final and the requester is told why. ``delivered`` is the filed document.

THE SUBMIT ENVELOPE (``validate_envelope``), the verb payload:

    matter            {"id": uuid, "number": str}      the matter the job READS
    file_to           {"id": uuid, "number": str}|null  where it FILES (null: the
                                                        same matter; a rehearsal
                                                        names the library matter)
    requested_by      email                             set by the overlay from the
                                                        turn's verified inbound
                                                        sender, never the model
    request_ref       str                               that inbound message's
                                                        internetMessageId, which the
                                                        completion reply threads to
    request_text      str (<= 20000)                    the request verbatim
    document_class    one of DOCUMENT_CLASSES

THE QUEUE FILE (``<queue>/<job_id>.json``), the runner's input, is the FLAT
shape the runner is built to (``queue_record``)::

    {"kind": "drafting", "job_id", "matter_id", "matter_number",
     "document_class", "requester", "message_ref", "request_text",
     "file_to_matter_id" | null, "file_to_matter_number" | null}

THE ALLOWANCE is a COUNT of drafts per billing cycle (the firm's authored
``drafting_allowance_per_cycle``), on the chronology cycle's anchor, counted
exactly as demand counts: in flight (submitted, running) reserves a slot; a
finished job counts once it recorded cents.

SUBMIT IS ATOMIC: inside one ``BEGIN IMMEDIATE`` it re-checks that this
request email has not already queued this class, that no draft of the same
class is unfinished on the matter, and that the cycle has a draft left.

THE REPLY is once per (attempt, outcome), a compare-and-set on ``reply_key``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .cycle_window import cycle_window

STATES = ("submitted", "running", "held", "delivered", "failed")
TERMINAL = frozenset({"delivered", "failed"})
AUDIT_TYPE = {s: f"DRAFTING_JOB_{s.upper()}" for s in STATES}
_ALLOWED_NEXT = {
    "submitted": {"running", "held", "failed"},
    "running": {"held", "delivered", "failed"},
    "held": {"failed"},
    "delivered": set(),
    "failed": {"running"},
}
#: The document classes the lane can draft. A seat switches each on in the
#: skill's ``enabled_classes`` setting; one not listed there is refused.
DOCUMENT_CLASSES = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")
#: How a refusal names a class to the requester.
CLASS_LABEL = {
    "mediation_brief": "a mediation brief",
    "discovery_set": "propounded discovery",
    "discovery_response": "discovery responses",
    "memo": "a memo",
    "depo_outline": "a deposition outline",
}
ALLOWANCE_KEY = "drafting_allowance_per_cycle"
CLASSES_KEY = "enabled_classes"
SKILL_NAME = "document-drafter"
MAX_REQUEST_TEXT = 20_000

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_MATTER_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS drafting_jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "state TEXT NOT NULL, "
    "matter_id TEXT NOT NULL, "
    "matter_number TEXT NOT NULL, "
    "file_to_matter_id TEXT NOT NULL, "
    "document_class TEXT NOT NULL, "
    "requester TEXT NOT NULL, "
    "request_ref TEXT NOT NULL, "
    "envelope_digest TEXT NOT NULL, "
    "cents INTEGER NOT NULL DEFAULT 0, "
    "reason TEXT, "
    "folder_id TEXT, "
    "delivery_json TEXT, "
    "reply_sent_at TEXT, "
    "attempt INTEGER NOT NULL DEFAULT 1, "
    "reply_key TEXT"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_drafting_jobs_created ON drafting_jobs(created_at)"
#: One job per (request email, class): one email may ask for two documents.
CREATE_UNIQUE_REF_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_drafting_jobs_request_ref_class ON drafting_jobs(request_ref, document_class)"
)
CREATE_SPEND_SQL = (
    "CREATE TABLE IF NOT EXISTS drafting_spend (job_id TEXT NOT NULL, at TEXT NOT NULL, cents INTEGER NOT NULL)"
)
CREATE_SPEND_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_drafting_spend_at ON drafting_spend(at)"
IN_FLIGHT = ("submitted", "running")
PROJECTION = (
    "id",
    "created_at",
    "updated_at",
    "state",
    "matter_id",
    "matter_number",
    "file_to_matter_id",
    "document_class",
    "requester",
    "cents",
    "reason",
    "folder_id",
    "reply_sent_at",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


class SubmitRefused(ValueError):
    """A valid request the ledger will not queue NOW. ``job_id`` names the job
    it collided with, when there is one."""

    def __init__(self, message: str, job_id: str | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _matter(raw: Any, field: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"id", "number"}:
        raise EnvelopeError(f"{field} must be {{id, number}}")
    mid, number = raw["id"], raw["number"]
    if not (isinstance(mid, str) and _UUID.match(mid)):
        raise EnvelopeError(f"{field}.id must be the matter's id")
    if not (isinstance(number, str) and _MATTER_NUMBER.match(number)):
        raise EnvelopeError(f"{field}.number must be the matter number")
    return {"id": mid, "number": number}


def validate_envelope(req: Any) -> dict[str, Any]:
    """The submit envelope, normalized, or ``EnvelopeError``."""
    keys = {"matter", "file_to", "requested_by", "request_ref", "request_text", "document_class"}
    if not isinstance(req, dict) or set(req) != keys:
        raise EnvelopeError(f"a drafting envelope carries exactly {sorted(keys)}")
    matter = _matter(req["matter"], "matter")
    file_to = _matter(req["file_to"], "file_to") if req["file_to"] is not None else None
    who = req["requested_by"]
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise EnvelopeError("requested_by must be the requester's email address")
    ref = req["request_ref"]
    if not (isinstance(ref, str) and _MESSAGE_ID.match(ref)):
        raise EnvelopeError("request_ref must be the request email's internetMessageId")
    text = req["request_text"]
    if not (isinstance(text, str) and text.strip() and len(text) <= MAX_REQUEST_TEXT):
        raise EnvelopeError(f"request_text must be the request verbatim, 1 to {MAX_REQUEST_TEXT} characters")
    klass = req["document_class"]
    if klass not in DOCUMENT_CLASSES:
        raise EnvelopeError(f"document_class must be one of {list(DOCUMENT_CLASSES)}")
    return {
        "matter": matter,
        "file_to": file_to,
        "requested_by": who.strip().lower(),
        "request_ref": ref,
        "request_text": text,
        "document_class": klass,
    }


def queue_record(env: dict[str, Any], job_id: str) -> dict[str, Any]:
    """The runner's job file, from a validated envelope. Exactly these keys."""
    file_to = env["file_to"]
    return {
        "kind": "drafting",
        "job_id": job_id,
        "matter_id": env["matter"]["id"],
        "matter_number": env["matter"]["number"],
        "document_class": env["document_class"],
        "requester": env["requested_by"],
        "message_ref": env["request_ref"],
        "request_text": env["request_text"],
        "file_to_matter_id": file_to["id"] if file_to else None,
        "file_to_matter_number": file_to["number"] if file_to else None,
    }


#: The delivery report's lists the DELIVER turn names. The runner may send each
#: inside ``delivery`` or as a top-level field of the record; either lands in
#: the stored delivery, so the projection has one place to read them from.
REPORT_LISTS = ("markers", "caption_discrepancies")


def merged_delivery(fields: dict[str, Any]) -> dict[str, Any] | None:
    """``fields["delivery"]`` with any top-level report list folded in, or None
    when the record carries neither (a note must not erase a stored report)."""
    raw = fields.get("delivery")
    delivery = dict(raw) if isinstance(raw, dict) else {}
    for key in REPORT_LISTS:
        if isinstance(fields.get(key), list):
            delivery[key] = fields[key]
    return delivery if (isinstance(raw, dict) or delivery) else None


class DraftingLedger:
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
            conn.execute(CREATE_SQL)
            conn.execute(CREATE_INDEX_SQL)
            conn.execute(CREATE_UNIQUE_REF_SQL)
            conn.execute(CREATE_SPEND_SQL)
            conn.execute(CREATE_SPEND_INDEX_SQL)
            conn.commit()
        finally:
            conn.close()

    # -- the cycle's count -------------------------------------------------
    def allowance(
        self,
        allowance: int | None,
        now: str | None = None,
        anchor_day: int | None = None,
        effective_from: str | None = None,
        exclude: str = "",
    ) -> dict[str, Any]:
        """Drafts used and remaining this cycle, without ``exclude``.
        Unauthored is ``authored: False`` with nothing remaining."""
        window = cycle_window(now or _iso_utc(), anchor_day, effective_from)
        conn = self._connect()
        try:
            used = self._used(conn, window.start, window.end, exclude)
        finally:
            conn.close()
        out = {"unit": "drafts", "used": used, "cycle": window.label, "cycle_start": window.start}
        if allowance is None:
            return {**out, "allowance": None, "remaining": 0, "authored": False}
        return {**out, "allowance": allowance, "remaining": max(0, allowance - used), "authored": True}

    @staticmethod
    def _used(conn: sqlite3.Connection, start: str, end: str, exclude: str = "") -> int:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM drafting_jobs WHERE created_at >= ? AND created_at < ? AND id != ? "
            "AND (state IN ('submitted','running') OR cents > 0)",
            (start, end, exclude),
        ).fetchone()["n"]

    def spend_between(self, start: str, end: str, exclude: str = "") -> int:
        """Cents WRITTEN in [start, end), without ``exclude``'s."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cents), 0) AS c FROM drafting_spend WHERE at >= ? AND at < ? AND job_id != ?",
                (start, end, exclude),
            ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    # -- intake ------------------------------------------------------------
    def active_on_matter(self, matter_id: str, document_class: str) -> str | None:
        """An unfinished draft of this class on this matter."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM drafting_jobs WHERE matter_id=? AND document_class=? "
                "AND state NOT IN ('delivered','failed','held') ORDER BY created_at DESC LIMIT 1",
                (matter_id, document_class),
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def submit(
        self,
        envelope: dict[str, Any],
        *,
        allowance: int | None = None,
        anchor_day: int | None = None,
        effective_from: str | None = None,
    ) -> str:
        """Row, then queue file, so a job file on disk always has its row."""
        env = validate_envelope(envelope)
        klass = env["document_class"]
        job_id = _ulid()
        now = _iso_utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute(
                "SELECT id FROM drafting_jobs WHERE request_ref=? AND document_class=?",
                (env["request_ref"], klass),
            ).fetchone()
            if dup is not None:
                conn.rollback()
                raise SubmitRefused(f"that request email already queued drafting job {dup['id']}", dup["id"])
            twin = conn.execute(
                "SELECT id FROM drafting_jobs WHERE matter_id=? AND document_class=? "
                "AND state NOT IN ('delivered','failed','held') ORDER BY created_at DESC LIMIT 1",
                (env["matter"]["id"], klass),
            ).fetchone()
            if twin is not None:
                conn.rollback()
                raise SubmitRefused(
                    f"{CLASS_LABEL[klass]} on this matter is already underway as job {twin['id']}", twin["id"]
                )
            if allowance is not None:
                window = cycle_window(now, anchor_day, effective_from)
                used = self._used(conn, window.start, window.end)
                if used >= allowance:
                    conn.rollback()
                    raise SubmitRefused(
                        f"this cycle's drafting allowance is spent ({used} of {allowance} in {window.label})"
                    )
            conn.execute(
                "INSERT INTO drafting_jobs (id, created_at, updated_at, state, matter_id, matter_number, "
                "file_to_matter_id, document_class, requester, request_ref, envelope_digest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    now,
                    now,
                    "submitted",
                    env["matter"]["id"],
                    env["matter"]["number"],
                    (env["file_to"] or env["matter"])["id"],
                    klass,
                    env["requested_by"],
                    env["request_ref"],
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

    # -- reads -------------------------------------------------------------
    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM drafting_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM drafting_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
            ]
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """The row as the agent sees it: no request text, no message ref. The
        delivery report's files, markers and caption discrepancies ride along,
        so the DELIVER turn can name them without reading the job dir."""
        out = {k: row.get(k) for k in PROJECTION}
        delivery = json.loads(row["delivery_json"]) if row.get("delivery_json") else None
        delivery = delivery or {}
        out["files"] = delivery.get("files")
        for key in REPORT_LISTS:
            if key in delivery:
                out[key] = delivery[key]
        return out

    # -- the runner's report and the reply ---------------------------------
    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition; returns the row after. A same-state record is a
        note (merges fields). ValueError on an unknown job or an illegal move."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state, cents FROM drafting_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            if state != cur["state"] and state not in _ALLOWED_NEXT[cur["state"]]:
                conn.rollback()
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            cents = fields.get("cents")
            if "cents" in fields and (not isinstance(cents, int) or isinstance(cents, bool) or cents < 0):
                conn.rollback()
                raise ValueError("cents must be a non-negative int")
            now = _iso_utc()
            if isinstance(cents, int) and cents > int(cur["cents"] or 0):
                conn.execute(
                    "INSERT INTO drafting_spend (job_id, at, cents) VALUES (?,?,?)",
                    (job_id, now, cents - int(cur["cents"] or 0)),
                )
            resumed = cur["state"] == "failed" and state == "running"
            delivery = merged_delivery(fields)
            conn.execute(
                "UPDATE drafting_jobs SET state=?, updated_at=?, "
                "attempt=attempt + CASE WHEN ? THEN 1 ELSE 0 END, "
                "cents=CASE WHEN ? THEN ? ELSE cents END, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "folder_id=CASE WHEN ? THEN ? ELSE folder_id END, "
                "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END "
                "WHERE id=?",
                (
                    state,
                    now,
                    resumed,
                    "cents" in fields,
                    cents,
                    "reason" in fields,
                    str(fields.get("reason") or "")[:500] or None,
                    "folder_id" in fields,
                    str(fields.get("folder_id") or "") or None,
                    isinstance(delivery, dict),
                    json.dumps(delivery, sort_keys=True) if isinstance(delivery, dict) else None,
                    job_id,
                ),
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM drafting_jobs WHERE id=?", (job_id,)).fetchone())
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
                "UPDATE drafting_jobs SET reply_sent_at=?, reply_key=? WHERE id=? "
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
                "UPDATE drafting_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?",
                (job_id, key),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "ALLOWANCE_KEY",
    "AUDIT_TYPE",
    "CLASSES_KEY",
    "CLASS_LABEL",
    "DOCUMENT_CLASSES",
    "PROJECTION",
    "SKILL_NAME",
    "STATES",
    "TERMINAL",
    "DraftingLedger",
    "EnvelopeError",
    "SubmitRefused",
    "merged_delivery",
    "queue_record",
    "validate_envelope",
]
