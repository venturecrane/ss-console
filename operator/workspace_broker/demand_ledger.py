"""Broker-owned ledger for demand jobs: the gap audit and draft demand a firm
administrator asks the Operator for (docs/specs/operator/demand-drafting-routine.md).

THE CONTRACT the request edge (broker verbs, overlay tool, skill) and the runner
(daemon dispatch, demand stages) both build on. It lives in its own table, not a
``kind`` column on ``medchron_jobs``: every chronology query (the page allowance,
the work-twin debit, the coverage union an UPDATE reads, the console projection)
is written for chronology rows, and a demand row in them would charge demand
work to the page allowance and record a demand's reads as chronology coverage
(review of the plan, 2026-10-06). Same audit db file, same broker writer, same
monotonic states, for the reasons ``medchron_ledger`` gives.

THE ENVELOPE (``validate_envelope``), written to ``<queue>/<job_id>.json``:

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
    request_text      str (<= 20000)                    the request verbatim: the
                                                        sender's brief is the
                                                        drafting instruction
    deliverables      ["gap_audit", "demand"]           what was asked for

THE WAKE (the runner's completion, for the DELIVER turn): ``job_id``, ``kind:
"demand"``, ``outcome`` (delivered | held | failed), ``folder_id``, ``files``
([{name, size}] as read back), ``requested_by``, ``request_ref``, ``reason``.

THE ALLOWANCE is a COUNT of demands per billing cycle (the firm's authored
``demand_allowance_per_cycle``), on the chronology cycle's anchor. A job counts
once it recorded cents, whatever it ended as, and a job that ended before any
paid stage (a premise failure, a cap estimate over the limit) does not.

THE REPLY is once per job, durably: ``mark_replied`` is a compare-and-set on
``reply_sent_at``, so a restart or a second wake can never send a second reply.
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
AUDIT_TYPE = {s: f"DEMAND_JOB_{s.upper()}" for s in STATES}
_ALLOWED_NEXT = {
    "submitted": {"running", "held", "failed"},
    "running": {"held", "delivered", "failed"},
    "held": {"failed"},
    "delivered": set(),
    # A resume: a person asked for it through the root-only verb, with a reason.
    "failed": {"running"},
}
DELIVERABLES = ("gap_audit", "demand")
ALLOWANCE_KEY = "demand_allowance_per_cycle"
SKILL_NAME = "demand-letter-drafter"
MAX_REQUEST_TEXT = 20_000

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_MATTER_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS demand_jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "state TEXT NOT NULL, "
    "matter_id TEXT NOT NULL, "
    "matter_number TEXT NOT NULL, "
    "file_to_matter_id TEXT NOT NULL, "
    "requester TEXT NOT NULL, "
    "request_ref TEXT NOT NULL, "
    "envelope_digest TEXT NOT NULL, "
    "cents INTEGER NOT NULL DEFAULT 0, "
    "reason TEXT, "
    "folder_id TEXT, "
    "delivery_json TEXT, "
    "reply_sent_at TEXT"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_demand_jobs_created ON demand_jobs(created_at)"
PROJECTION = (
    "id",
    "created_at",
    "updated_at",
    "state",
    "matter_id",
    "matter_number",
    "file_to_matter_id",
    "requester",
    "cents",
    "reason",
    "folder_id",
    "reply_sent_at",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


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
    """The envelope exactly as the runner will read it, or ``EnvelopeError``."""
    keys = {"matter", "file_to", "requested_by", "request_ref", "request_text", "deliverables"}
    if not isinstance(req, dict) or set(req) != keys:
        raise EnvelopeError(f"a demand envelope carries exactly {sorted(keys)}")
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
    wanted = req["deliverables"]
    if not isinstance(wanted, list) or not wanted or any(d not in DELIVERABLES for d in wanted):
        raise EnvelopeError(f"deliverables must be drawn from {list(DELIVERABLES)}")
    return {
        "matter": matter,
        "file_to": file_to,
        "requested_by": who.strip().lower(),
        "request_ref": ref,
        "request_text": text,
        "deliverables": sorted(set(wanted), key=DELIVERABLES.index),
    }


class DemandLedger:
    """Per-operation sqlite connections, ``journal_mode=DELETE``, as
    ``MedchronLedger``: the broker and the runner reach this file as different
    uids, and the WAL's shared-memory file would need both to write it."""

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
    ) -> dict[str, Any]:
        """Demands used and remaining this cycle. Unauthored is ``authored:
        False`` with nothing remaining: the fail-closed answer."""
        window = cycle_window(now or _iso_utc(), anchor_day, effective_from)
        conn = self._connect()
        try:
            used = conn.execute(
                "SELECT COUNT(*) AS n FROM demand_jobs WHERE created_at >= ? AND created_at < ? AND cents > 0",
                (window.start, window.end),
            ).fetchone()["n"]
        finally:
            conn.close()
        out = {"unit": "demands", "used": used, "cycle": window.label, "cycle_start": window.start}
        if allowance is None:
            return {**out, "allowance": None, "remaining": 0, "authored": False}
        return {**out, "allowance": allowance, "remaining": max(0, allowance - used), "authored": True}

    # -- intake ------------------------------------------------------------
    def active_on_matter(self, matter_id: str) -> str | None:
        """An unfinished demand job on this matter, which a second submit would duplicate."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM demand_jobs WHERE matter_id=? AND state NOT IN ('delivered','failed') "
                "ORDER BY created_at DESC LIMIT 1",
                (matter_id,),
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def submit(self, envelope: dict[str, Any]) -> str:
        """Row, then queue file, so an envelope on disk always has its row."""
        env = validate_envelope(envelope)
        job_id = _ulid()
        now = _iso_utc()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO demand_jobs (id, created_at, updated_at, state, matter_id, matter_number, "
                "file_to_matter_id, requester, request_ref, envelope_digest) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    now,
                    now,
                    "submitted",
                    env["matter"]["id"],
                    env["matter"]["number"],
                    (env["file_to"] or env["matter"])["id"],
                    env["requested_by"],
                    env["request_ref"],
                    digest(env),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        queued = {**env, "job_id": job_id, "kind": "demand", "submitted_at": now}
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps(queued, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")
        return job_id

    # -- reads -------------------------------------------------------------
    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM demand_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """The row as the agent and console see it: no request text, no ref."""
        out = {k: row.get(k) for k in PROJECTION}
        delivery = json.loads(row["delivery_json"]) if row.get("delivery_json") else None
        out["files"] = (delivery or {}).get("files")
        return out

    # -- the runner's report and the reply ---------------------------------
    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition; returns the row after. A same-state record is a
        note (merges fields). ValueError on an unknown job or an illegal move."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        conn = self._connect()
        try:
            cur = conn.execute("SELECT state FROM demand_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                raise ValueError(f"no such job {job_id}")
            if state != cur["state"] and state not in _ALLOWED_NEXT[cur["state"]]:
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            cents = fields.get("cents")
            if "cents" in fields and (not isinstance(cents, int) or isinstance(cents, bool) or cents < 0):
                raise ValueError("cents must be a non-negative int")
            delivery = fields.get("delivery")
            # One fixed statement: each optional column is written only when
            # its flag is set, so nothing is composed into the SQL text.
            conn.execute(
                "UPDATE demand_jobs SET state=?, updated_at=?, "
                "cents=CASE WHEN ? THEN ? ELSE cents END, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "folder_id=CASE WHEN ? THEN ? ELSE folder_id END, "
                "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END "
                "WHERE id=?",
                (
                    state,
                    _iso_utc(),
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
            return dict(conn.execute("SELECT * FROM demand_jobs WHERE id=?", (job_id,)).fetchone())
        finally:
            conn.close()

    def mark_replied(self, job_id: str) -> bool:
        """True exactly once per job: the compare-and-set the completion reply
        takes before it sends. False when a reply was already sent (or no job)."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE demand_jobs SET reply_sent_at=? WHERE id=? AND reply_sent_at IS NULL", (_iso_utc(), job_id)
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()


__all__ = [
    "ALLOWANCE_KEY",
    "AUDIT_TYPE",
    "DELIVERABLES",
    "PROJECTION",
    "SKILL_NAME",
    "STATES",
    "TERMINAL",
    "DemandLedger",
    "EnvelopeError",
    "validate_envelope",
]
