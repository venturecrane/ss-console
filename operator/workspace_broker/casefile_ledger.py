"""Broker-owned ledger for Case File Manager jobs: a caseload read, its File
Status filed in each matter, the ranked list filed to the firm's library, and
the numbered lines a person may approve; and the apply of approved lines.

A sibling of ``litigation_ledger`` (it imports nothing from that lane, so a
change there cannot move this lane's contract). Its own tables on the same
audit db file, the same broker writer, the same monotonic states::

    queued    -> running | held | failed
    running   -> held | delivered | failed
    held      -> failed         (also SMD's close)
    failed    -> running        (a resume, root only)

``casefile_lines`` holds every numbered line a delivered ``full`` job issued:
its kind (``task`` / ``medicals``), matter, source id and the hash of the facts
the runner proposed (the facts themselves stay in the runner's state on the
volume). A line moves ``proposed`` (recorded) -> ``raised`` (the broker sent it
to the requester, ``reply_binding``) -> ``applying`` (an apply job holds it)
-> ``applied`` | ``refused`` | ``failed`` (that job's outcome). A line is
applied at most once: ``applying`` is taken in the same write transaction that
queues the apply job.

THE RUNNER'S REPORT is ids, counts, hashes and file metadata only: nothing in
this ledger names a client, a party or a document's words.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

STATES = ("queued", "running", "held", "delivered", "failed")
AUDIT_TYPE = {s: f"CASEFILE_JOB_{'SUBMITTED' if s == 'queued' else s.upper()}" for s in STATES}
ALLOWED_NEXT = {
    "queued": {"running", "held", "failed"},
    "running": {"held", "delivered", "failed"},
    "held": {"failed"},
    "delivered": set(),
    "failed": {"running"},
}
TRIGGERS = ("request", "scheduled")
MODES = ("full", "recheck", "apply")
LINE_KINDS = ("task", "medicals")
LINE_STATES = ("proposed", "raised", "applying", "applied", "refused", "failed")
SKILL_NAME = "case-file-manager"
KIND = "casefile"
MAX_REQUEST_TEXT = 20_000
MAX_LINES = 999
COUNT_FIELDS = ("matters_total", "matters_read", "matters_current", "matters_held", "lines_proposed")
REPORT_FIELDS = (
    "cents",
    "reason",
    "stage",
    "folder_id",
    "files",
    "lines",
    "line_outcomes",
    "writes",
    "metrics",
    *COUNT_FIELDS,
)
CLOSED_REASON = "closed"
CLOSE_REF = re.compile(r"^(vfy_)?[0-9A-HJKMNP-TV-Z]{26}$")
#: The outcomes that are a write the runner made and read back.
APPLIED_OUTCOMES = ("completed", "written", "invoice_added")
#: The outcomes where nothing needed writing (the record already shows it).
NOTHING_TO_DO = ("already_complete", "already_present")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
_FOLDER = re.compile(r"^[A-Za-z0-9 ._()-]{1,80}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")
_OUTCOME = re.compile(r"^[a-z_]{1,40}$")
_WRITE_PATH = re.compile(r"^/(tasks|matters)/[0-9a-fA-F-]{32,40}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS casefile_jobs ("
    "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL, "
    "trigger TEXT NOT NULL, mode TEXT NOT NULL, requester TEXT NOT NULL, request_ref TEXT NOT NULL, "
    "scope_key TEXT NOT NULL, scope_json TEXT NOT NULL, file_to_matter_id TEXT, envelope_digest TEXT NOT NULL, "
    "source_job_id TEXT, stage TEXT, cents INTEGER NOT NULL DEFAULT 0, matters_total INTEGER, "
    "matters_read INTEGER, matters_current INTEGER, matters_held INTEGER, lines_proposed INTEGER, "
    "reason TEXT, folder_id TEXT, delivery_json TEXT, metrics_json TEXT, outcomes_json TEXT, "
    "reply_sent_at TEXT, attempt INTEGER NOT NULL DEFAULT 1, reply_key TEXT, close_reason TEXT, close_ref TEXT)"
)
CREATE_LINES_SQL = (
    "CREATE TABLE IF NOT EXISTS casefile_lines ("
    "job_id TEXT NOT NULL, n INTEGER NOT NULL, kind TEXT NOT NULL, matter_id TEXT NOT NULL, "
    "source_id TEXT NOT NULL, sha256 TEXT NOT NULL, state TEXT NOT NULL, thread_ref TEXT, dispatch_ref TEXT, "
    "apply_job_id TEXT, outcome TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (job_id, n))"
)
CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_casefile_jobs_created ON casefile_jobs(created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_casefile_jobs_request_ref_live ON casefile_jobs(request_ref) "
    "WHERE NOT (state = 'failed' AND trigger = 'request')",
    "CREATE INDEX IF NOT EXISTS idx_casefile_lines_thread ON casefile_lines(thread_ref)",
    "CREATE TABLE IF NOT EXISTS casefile_spend (job_id TEXT NOT NULL, at TEXT NOT NULL, cents INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_casefile_spend_at ON casefile_spend(at)",
)
PROJECTION = tuple(
    "created_at updated_at state trigger mode requester stage cents matters_total matters_read matters_current "
    "matters_held lines_proposed reason folder_id reply_sent_at source_job_id".split()
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


def _names(raw: Any, cap: int, rx: re.Pattern[str] | None = None) -> list[str] | None:
    if not (isinstance(raw, list) and 0 < len(raw) <= cap and all(isinstance(x, str) for x in raw)):
        return None
    out = list(dict.fromkeys(" ".join(x.split()) for x in raw))
    if any(not x or len(x) > 100 or (rx is not None and not rx.match(x)) for x in out):
        return None
    return out


def normalize_scope(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and set(raw) == {"assisting"} and isinstance(raw["assisting"], dict):
        names = _names(raw["assisting"].get("names"), 10)
        if names is not None and set(raw["assisting"]) == {"names"}:
            return {"assisting": {"names": names}}
    if isinstance(raw, dict) and set(raw) == {"matters"} and isinstance(raw["matters"], dict):
        nums = _names(raw["matters"].get("numbers"), 200, _NUMBER)
        if nums is not None and set(raw["matters"]) == {"numbers"}:
            return {"matters": {"numbers": nums}}
    if isinstance(raw, dict) and set(raw) == {"assisting_staff_ids"}:
        ids = raw["assisting_staff_ids"]
        if isinstance(ids, list) and 0 < len(ids) <= 20 and all(isinstance(i, str) and _UUID.match(i) for i in ids):
            return {"assisting_staff_ids": sorted({i.lower() for i in ids})}
    raise EnvelopeError(
        'scope must be {"assisting": {"names": [...]}}, {"matters": {"numbers": [...]}} '
        'or {"assisting_staff_ids": [uuid, ...]}'
    )


def scope_key(scope: dict[str, Any]) -> str:
    return "casefile:" + digest(scope)[:32]


def validate_line(raw: Any) -> dict[str, Any]:
    if not (isinstance(raw, dict) and set(raw) == {"n", "kind", "matter_id", "source_id", "sha256"}):
        raise ValueError("each line carries exactly n, kind, matter_id, source_id and sha256")
    n = raw["n"]
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_LINES):
        raise ValueError("a line's n is 1..999")
    if raw["kind"] not in LINE_KINDS or not (isinstance(raw["matter_id"], str) and _UUID.match(raw["matter_id"])):
        raise ValueError("a line's kind is task or medicals, on a matter uuid")
    if not (isinstance(raw["source_id"], str) and _SOURCE.match(raw["source_id"])):
        raise ValueError("a line's source_id is a plain identifier")
    if not (isinstance(raw["sha256"], str) and _SHA256.match(raw["sha256"])):
        raise ValueError("a line's sha256 is 64 hex characters")
    return {**raw, "matter_id": raw["matter_id"].lower()}


def _common(req: dict[str, Any]) -> dict[str, Any]:
    if req.get("trigger") not in TRIGGERS or req.get("mode") not in MODES:
        raise EnvelopeError(f"trigger must be one of {list(TRIGGERS)} and mode one of {list(MODES)}")
    who = req.get("requester")
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise EnvelopeError("requester must be the requester's email address")
    ref = req.get("message_ref")
    if not (isinstance(ref, str) and _MESSAGE_ID.match(ref)):
        raise EnvelopeError("message_ref must be the request email's internetMessageId")
    text = req.get("request_text")
    if not (isinstance(text, str) and text.strip() and len(text) <= MAX_REQUEST_TEXT):
        raise EnvelopeError(f"request_text must be the request verbatim, 1 to {MAX_REQUEST_TEXT} characters")
    return {
        "trigger": req["trigger"],
        "mode": req["mode"],
        "requester": who.strip().lower(),
        "message_ref": ref,
        "request_text": text,
    }


def validate_envelope(req: Any) -> dict[str, Any]:
    """The RESOLVED envelope (the broker's), normalized, or ``EnvelopeError``."""
    if not isinstance(req, dict):
        raise EnvelopeError("a case file envelope is an object")
    common = _common(req)
    if common["mode"] == "apply":
        if set(req) != {*common, "apply"}:
            raise EnvelopeError("an apply envelope carries the common keys and apply")
        a = req["apply"]
        if not (isinstance(a, dict) and set(a) == {"source_job_id", "approver", "thread_ref", "lines"}):
            raise EnvelopeError("apply carries exactly source_job_id, approver, thread_ref and lines")
        if not (_ULID.match(str(a["source_job_id"])) and _EMAIL.match(str(a["approver"]))):
            raise EnvelopeError("apply names a job id and the approver's address")
        try:
            lines = [validate_line(x) for x in a["lines"]] if isinstance(a["lines"], list) and a["lines"] else None
        except ValueError as exc:
            raise EnvelopeError(str(exc)) from exc
        if not lines or not (isinstance(a["thread_ref"], str) and 0 < len(a["thread_ref"]) <= 512):
            raise EnvelopeError("apply carries a thread and at least one line")
        return {**common, "apply": {**a, "approver": str(a["approver"]).strip().lower(), "lines": lines}}
    if set(req) != {*common, "scope", "file_to_matter_id", "file_to_matter_number", "folder_name"}:
        raise EnvelopeError("a full envelope carries the common keys, scope and the filing target")
    mid, number, folder = req["file_to_matter_id"], req["file_to_matter_number"], req["folder_name"]
    if not (isinstance(mid, str) and _UUID.match(mid) and isinstance(number, str) and _NUMBER.match(number)):
        raise EnvelopeError("file_to_matter_id and file_to_matter_number must name the library matter")
    if not (isinstance(folder, str) and _FOLDER.match(folder.strip())):
        raise EnvelopeError("folder_name must be the authored folder name")
    return {
        **common,
        "scope": normalize_scope(req["scope"]),
        "file_to_matter_id": mid.lower(),
        "file_to_matter_number": number,
        "folder_name": folder.strip(),
    }


def checked_writes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 2000:
        raise ValueError("writes must be a list of at most 2000 entries")
    out = []
    for w in raw:
        ok = isinstance(w, dict) and set(w) <= {"method", "path", "status", "ok"}
        if not ok or w.get("method") not in ("PUT", "PATCH", "POST") or not _WRITE_PATH.match(str(w.get("path") or "")):
            raise ValueError("each write is {method: PUT|PATCH|POST, path: /tasks/<id>|/matters/<id>, status, ok}")
        out.append({"method": w["method"], "path": w["path"], "ok": w.get("ok") is True})
    return out


def checked_outcomes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > MAX_LINES:
        raise ValueError("line_outcomes must be a list")
    out = []
    for o in raw:
        if not (isinstance(o, dict) and isinstance(o.get("n"), int) and _OUTCOME.match(str(o.get("outcome") or ""))):
            raise ValueError("each line outcome is {n, kind, outcome}")
        out.append({"n": int(o["n"]), "kind": str(o.get("kind") or ""), "outcome": str(o["outcome"])})
    return out


def _files(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("files must be a list")
    out = []
    for f in raw[:400]:
        size, sha = (f or {}).get("size"), str((f or {}).get("sha256") or "")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or not _SHA256.match(sha):
            raise ValueError("each file carries an integer size and a sha256")
        out.append({k: str(f.get(k) or "")[:200] for k in ("role", "name", "file_id")} | {"size": size, "sha256": sha})
    return out


def checked(state: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A record's fields, checked and reduced."""
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    unknown = set(fields) - set(REPORT_FIELDS)
    if unknown:
        raise ValueError(f"casefile_job_record carries only {list(REPORT_FIELDS)}; got {sorted(unknown)}")
    for key in ("cents", *COUNT_FIELDS):
        value = fields.get(key)
        if key in fields and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValueError(f"{key} must be a non-negative int")
    out = dict(fields)
    if "files" in fields:
        out["files"] = _files(fields["files"])
    if "lines" in fields:
        out["lines"] = [validate_line(x) for x in fields["lines"]]
    if "line_outcomes" in fields:
        out["line_outcomes"] = checked_outcomes(fields["line_outcomes"])
    if "writes" in fields:
        out["writes"] = checked_writes(fields["writes"])
    if "metrics" in fields and not isinstance(fields["metrics"], dict):
        raise ValueError("metrics must be an object")
    return out


__all__ = [
    "APPLIED_OUTCOMES",
    "ALLOWED_NEXT",
    "AUDIT_TYPE",
    "CLOSE_REF",
    "CLOSED_REASON",
    "COUNT_FIELDS",
    "CREATE_INDEXES",
    "CREATE_LINES_SQL",
    "CREATE_SQL",
    "EnvelopeError",
    "KIND",
    "LINE_STATES",
    "NOTHING_TO_DO",
    "PROJECTION",
    "SKILL_NAME",
    "STATES",
    "SubmitRefused",
    "checked",
    "digest",
    "scope_key",
    "validate_envelope",
]
