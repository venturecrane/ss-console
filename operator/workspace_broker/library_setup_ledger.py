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
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .lane_ledger import ReplyOnce, StateRecord

JOB_KIND = "library_setup"
#: Every kind the drafting job makes, in the document-kind registry's order
#: (``runners/jobs/jobs/library_setup.KINDS``, pinned equal by test).
KINDS = ("discovery_set", "discovery_response", "mediation_brief", "memo", "depo_outline")
#: "Set up the document library": one job, one list per kind in ``KINDS``.
LIBRARY = "library"
MAX_POINT = 200
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
#: Added for the administrator's choice (3b): which list a choice is from, the
#: chosen document's ids, the attorney it was for, and the intake run.
ADDED_COLUMNS = {
    "mode": "TEXT NOT NULL DEFAULT 'list'",
    "parent_id": "TEXT",
    "choice_json": "TEXT",
    "attorney_name": "TEXT",
    "attorney_key": "TEXT",
    "run_id": "TEXT",
    # What a list job was pointed at: "matter:<number>" or "name" (the words are
    # in its queue file only: a file's name can carry a client's).
    "pointed": "TEXT",
}
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
    "mode",
    "parent_id",
    "run_id",
    "pointed",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue; the message names the field."""


class Underway(ValueError):
    """A setup of this kind, for this attorney or the firm, has not finished."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"library setup job {job_id} is underway")
        self.job_id = job_id


REQUIRED = frozenset({"kind", "attorney", "requested_by", "request_ref", "request_text"})
#: What the administrator pointed at, as the request gives it: a matter's number,
#: or words of a document's name. Each must appear in the request's own words.
POINTERS = frozenset({"matter", "document"})


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text or "").split()).casefold()


def _pointer(req: dict[str, Any], key: str, text: str) -> str | None:
    value = req.get(key)
    if value is None:
        return None
    value = " ".join(str(value).split()) if isinstance(value, str) else ""
    if not value or len(value) > MAX_POINT or _fold(value) not in _fold(text):
        raise EnvelopeError(f"{key} must be words the request itself gives, or null")
    return value


def validate_envelope(req: Any) -> dict[str, Any]:
    if not isinstance(req, dict) or not REQUIRED <= set(req) <= REQUIRED | POINTERS:
        raise EnvelopeError(
            f"a library setup envelope carries exactly {sorted(REQUIRED)}, and may add {sorted(POINTERS)}"
        )
    if req["kind"] not in KINDS and req["kind"] != LIBRARY:
        raise EnvelopeError(f"kind must be one of {[*KINDS, LIBRARY]}")
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
    matter, document = _pointer(req, "matter", text), _pointer(req, "document", text)
    if (matter or document) and req["kind"] == LIBRARY:
        raise EnvelopeError("a document is pointed at for one kind, never for the whole library")
    if matter and document:
        raise EnvelopeError("point at a matter or at a document's name, not both")
    out = {"kind": req["kind"], "attorney": attorney or None, "requested_by": who.strip().lower(), "request_ref": ref}
    if matter:
        out["matter"] = matter
    if document:
        out["document"] = document
    return out


def pointed_of(env: dict[str, Any]) -> str | None:
    """The row's ``pointed``: ``matter:<number>``, ``name``, or None."""
    if env.get("matter"):
        return f"matter:{env['matter']}"
    return "name" if env.get("document") else None


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
        **({"point_matter": env["matter"]} if env.get("matter") else {}),
        **({"point_name": env["document"]} if env.get("document") else {}),
    }


class SetupLedger(StateRecord, ReplyOnce):
    """The library setup lane on the shared ledger (``lane_ledger``), on the
    drafting ledger's file: its table, its DDL, its row as the reply chain
    reads it, and its UPDATE."""

    JOBS = "library_setup_jobs"

    def _create(self, conn: sqlite3.Connection) -> None:
        conn.execute(CREATE_SQL)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(library_setup_jobs)")}
        for name, decl in ADDED_COLUMNS.items():
            if name not in have:
                conn.execute(f"ALTER TABLE library_setup_jobs ADD COLUMN {name} {decl}")

    def _row_sql(self) -> str:
        return _SELECT

    def owns(self, job_id: str) -> bool:
        return bool(_ULID_RE.match(job_id or "")) and self.read(job_id) is not None

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
                "SELECT id FROM library_setup_jobs WHERE mode='list' AND document_class=? "
                "AND COALESCE(lower(attorney_named),'')=? AND COALESCE(pointed,'')=? "
                "AND state IN ('submitted','running') LIMIT 1",
                (env["kind"], (env["attorney"] or "").lower(), pointed_of(env) or ""),
            ).fetchone()
            if twin is not None:
                conn.rollback()
                raise Underway(twin["id"])
            conn.execute(
                "INSERT INTO library_setup_jobs (id, created_at, updated_at, state, document_class, matter_number, "
                "requester, request_ref, envelope_digest, live, attorney_named, pointed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, now, now, "submitted", env["kind"], library_number, env["requested_by"],
                 env["request_ref"], digest, int(live), env["attorney"], pointed_of(env)),
            )  # fmt: skip
            conn.commit()
        finally:
            conn.close()
        self._write_queue(job_id, queue_record(env, job_id, library_number, live))
        return job_id

    def _check(self, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """A ``report``, when present, is an object."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        report = fields.get("report")
        if report is not None and not isinstance(report, dict):
            raise ValueError("report must be an object")
        return fields

    def _apply(
        self, conn: sqlite3.Connection, job_id: str, cur: sqlite3.Row, state: str, fields: dict[str, Any], now: str
    ) -> None:
        report = fields.get("report")
        conn.execute(
            "UPDATE library_setup_jobs SET state=?, updated_at=?, "
            "reason=CASE WHEN ? THEN ? ELSE reason END, "
            "report_json=CASE WHEN ? THEN ? ELSE report_json END WHERE id=?",
            (state, now, "reason" in fields, str(fields.get("reason") or "")[:500] or None,
             report is not None, json.dumps(report, sort_keys=True) if report is not None else None, job_id),
        )  # fmt: skip


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
    "LIBRARY",
    "STATES",
    "TERMINAL",
    "DraftingOrSetup",
    "EnvelopeError",
    "SetupLedger",
    "Underway",
    "of_drafting",
    "pointed_of",
    "queue_record",
    "validate_envelope",
]
