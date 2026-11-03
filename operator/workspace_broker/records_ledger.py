"""Broker-owned ledger for matter records jobs (``matter-records``): a staff
member asked the Operator by email for named records on a matter, and the
records lane finds the pages, cuts the files and writes one SMD review row.

A sibling of ``casefile_ledger`` (it imports nothing from that lane, so a
change there cannot move this lane's contract). Its own tables on the same
audit db file, the same broker writer, the same monotonic states plus
``review``::

    queued    -> running | held | failed
    running   -> held | review | delivered | failed
    review    -> delivered | failed     (the row was sent, or SMD rejected it / it expired)
    held      -> failed                 (also SMD's close)
    failed    -> running                (a resume, root only)

``review`` is a job whose reply sits in the SMD review directory as one held
row (``records_contract.review_row``). It leaves ``review`` only on the
runner's report of that row's outcome: ``sent`` -> ``delivered``, ``rejected``
or ``expired`` -> ``failed``.

THE ONE RULE THIS LANE ADDS. ``request_ref`` (the request email's RFC id, else
its message id) is unique across every row ever written, terminal ones included: one email is
answered by at most one job. A twin submit returns ``already_queued`` (the
first job is still live) or ``already_done`` (it ended) with that job's id,
and queues nothing. Same-matter jobs are NOT refused; the daemon runs them
one at a time.

``records_lines`` holds one row per parsed item of a job the runner reported:
its number and an outcome token, never the item's words.

THE RUNNER'S REPORT is ids, counts, tokens and hashes only: nothing in this
ledger names a client, a party, a provider or a document's words.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

STATES = ("queued", "running", "held", "review", "delivered", "failed")
LIVE_STATES = ("queued", "running", "held", "review")
TERMINAL_STATES = ("delivered", "failed")
ALLOWED_NEXT = {
    "queued": {"running", "held", "failed"},
    "running": {"held", "review", "delivered", "failed"},
    "review": {"delivered", "failed"},
    "held": {"failed"},
    "delivered": set(),
    "failed": {"running"},
}
SKILL_NAME = "matter-records"
KIND = "records"
ACKS = ("turn", "none")
VERDICTS = ("delivered", "held", "failed")
#: The review row's outcome as the runner reads it back from the review dir.
DELIVERY_STATUSES = ("review", "sent", "rejected", "expired", "failed")
#: The row's reason when the verdict was delivered (``records_contract.review_row.HOLD_REASON``).
REVIEW_REASON = "matter_files_review"
MAX_REQUEST_TEXT = 20_000
MAX_ITEMS = 50
CLOSED_REASON = "closed"
CLOSE_REF = re.compile(r"^(vfy_)?[0-9A-HJKMNP-TV-Z]{26}$")
REQUEST_KEYS_REQUIRED = frozenset({"trigger", "requester", "message_ref", "request_text"})
REQUEST_KEYS_OPTIONAL = frozenset({"matter_id", "ack", "internet_message_id"})
RECORD_FIELDS = ("state", "verdict", "reason", "stage", "cents", "delivery", "items")
DELIVERY_FIELDS = ("review_id", "digest", "decision", "status")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,63}$")
#: An internetMessageId (``<x@y>``) or a Graph message id (base64url-ish).
_MESSAGE_ID = re.compile(r"^(<?[^\s<>]{3,500}@[^\s<>]{1,250}>?|[A-Za-z0-9+/=_.-]{16,512})$")
_RFC_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")
_REVIEW_ID = re.compile(r"^[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z_]{1,40}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS records_jobs ("
    "job_id TEXT PRIMARY KEY, request_ref TEXT NOT NULL, message_ref TEXT NOT NULL, matter_id TEXT, requester TEXT NOT NULL, "
    "state TEXT NOT NULL, cents INTEGER NOT NULL DEFAULT 0, review_id TEXT, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, trigger TEXT NOT NULL, ack TEXT NOT NULL, envelope_digest TEXT NOT NULL, "
    "stage TEXT, reason TEXT, delivery_json TEXT, attempt INTEGER NOT NULL DEFAULT 1, "
    "close_reason TEXT, close_ref TEXT)"
)
CREATE_LINES_SQL = (
    "CREATE TABLE IF NOT EXISTS records_lines ("
    "job_id TEXT NOT NULL, n INTEGER NOT NULL, outcome TEXT NOT NULL, files INTEGER NOT NULL DEFAULT 0, "
    "updated_at TEXT NOT NULL, PRIMARY KEY (job_id, n))"
)
CREATE_INDEXES = (
    # The lane's one rule: one email, one job, ever. Not partial, on purpose.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_records_jobs_request_ref ON records_jobs(request_ref)",
    # The reply anchor too: the same email submitted once with its RFC id and
    # once without (a root submit naming only the Graph id) is still one job.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_records_jobs_message_ref ON records_jobs(message_ref)",
    "CREATE INDEX IF NOT EXISTS idx_records_jobs_created ON records_jobs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_records_jobs_state ON records_jobs(state)",
    "CREATE TABLE IF NOT EXISTS records_spend (job_id TEXT NOT NULL, at TEXT NOT NULL, cents INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_records_spend_at ON records_spend(at)",
)
PROJECTION = tuple("created_at updated_at state trigger requester matter_id stage cents review_id reason".split())


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


class SubmitRefused(ValueError):
    """A valid request the ledger will not queue: this email already has a job.
    ``duplicate`` is ``already_queued`` (that job is live) or ``already_done``."""

    def __init__(self, message: str, job_id: str, duplicate: str) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.duplicate = duplicate


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def matter_ok(value: Any) -> bool:
    """A matter's Smokeball id (uuid) or its number as staff wrote it."""
    return isinstance(value, str) and bool(_UUID.match(value) or _NUMBER.match(value.strip()))


def validate_envelope(req: Any) -> dict[str, Any]:
    """The envelope, normalized, or ``EnvelopeError``. Exactly the request keys
    (``matter_id`` and ``ack`` optional; ``ack`` defaults to ``turn``)."""
    if not isinstance(req, dict):
        raise EnvelopeError("a records envelope is an object")
    keys = set(req)
    if not (REQUEST_KEYS_REQUIRED <= keys <= REQUEST_KEYS_REQUIRED | REQUEST_KEYS_OPTIONAL):
        raise EnvelopeError(
            f"a records envelope carries exactly {sorted(REQUEST_KEYS_REQUIRED)}, "
            f"and optionally {sorted(REQUEST_KEYS_OPTIONAL)}"
        )
    if req["trigger"] != "request":
        raise EnvelopeError("a records job's trigger is request")
    who = req["requester"]
    if not (isinstance(who, str) and _EMAIL.match(who.strip())):
        raise EnvelopeError("requester must be the requester's email address")
    ref = req["message_ref"]
    if not (isinstance(ref, str) and _MESSAGE_ID.match(ref)):
        raise EnvelopeError("message_ref must be the request email's message id")
    rfc = req.get("internet_message_id") or ""
    if rfc and not (isinstance(rfc, str) and _RFC_ID.match(rfc)):
        raise EnvelopeError("internet_message_id, when given, is the request email's internetMessageId")
    text = req["request_text"]
    if not (isinstance(text, str) and text.strip() and len(text) <= MAX_REQUEST_TEXT):
        raise EnvelopeError(f"request_text must be the sender's own words, 1 to {MAX_REQUEST_TEXT} characters")
    matter = req.get("matter_id")
    if matter in (None, ""):
        matter = None
    elif not matter_ok(matter):
        raise EnvelopeError("matter_id, when given, is the matter's id or number")
    else:
        matter = matter.strip().lower() if _UUID.match(matter) else " ".join(matter.split())
    ack = req.get("ack", "turn")
    if ack not in ACKS:
        raise EnvelopeError(f"ack must be one of {list(ACKS)}")
    return {
        "trigger": "request",
        "requester": who.strip().lower(),
        "message_ref": ref,
        "internet_message_id": rfc,
        # The card and dedupe key: the RFC id when the seat has one (the
        # request index joins on it), else the message id.
        "request_ref": rfc or ref,
        "request_text": text,
        "matter_id": matter,
        "ack": ack,
    }


def _delivery(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not set(raw) <= set(DELIVERY_FIELDS):
        raise ValueError(f"delivery carries only {list(DELIVERY_FIELDS)}")
    out: dict[str, Any] = {}
    if raw.get("review_id") is not None:
        if not (isinstance(raw["review_id"], str) and _REVIEW_ID.match(raw["review_id"])):
            raise ValueError("delivery.review_id is the review row's 24-hex id")
        out["review_id"] = raw["review_id"]
    if raw.get("digest") is not None:
        if not (isinstance(raw["digest"], str) and _SHA256.match(raw["digest"])):
            raise ValueError("delivery.digest is the review row's sha256 digest")
        out["digest"] = raw["digest"]
    if raw.get("decision") is not None:
        if not (isinstance(raw["decision"], str) and _TOKEN.match(raw["decision"])):
            raise ValueError("delivery.decision is a token")
        out["decision"] = raw["decision"]
    if raw.get("status") is not None:
        if raw["status"] not in DELIVERY_STATUSES:
            raise ValueError(f"delivery.status is one of {list(DELIVERY_STATUSES)}")
        out["status"] = raw["status"]
    return out


def _items(raw: Any) -> list[dict[str, Any]]:
    if not (isinstance(raw, list) and len(raw) <= MAX_ITEMS):
        raise ValueError(f"items is a list of at most {MAX_ITEMS}")
    out = []
    for it in raw:
        ok = isinstance(it, dict) and set(it) <= {"n", "outcome", "files"}
        n, files = (it or {}).get("n") if ok else None, (it or {}).get("files", 0) if ok else None
        if not (
            ok
            and isinstance(n, int)
            and not isinstance(n, bool)
            and 1 <= n <= MAX_ITEMS
            and _TOKEN.match(str(it.get("outcome") or ""))
            and isinstance(files, int)
            and not isinstance(files, bool)
            and files >= 0
        ):
            raise ValueError("each item is {n, outcome token, files count}")
        out.append({"n": n, "outcome": it["outcome"], "files": files})
    return out


def _fields(report: dict[str, Any]) -> dict[str, Any]:
    """The report's fields, checked and reduced (no state decided here)."""
    unknown = set(report) - set(RECORD_FIELDS)
    if unknown:
        raise ValueError(f"records_job_record carries only {['job_id', *RECORD_FIELDS]}; got {sorted(unknown)}")
    f: dict[str, Any] = {}
    if "cents" in report:
        cents = report["cents"]
        if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
            raise ValueError("cents must be a non-negative int")
        f["cents"] = cents
    for key, limit in (("reason", 500), ("stage", 40)):
        if report.get(key) is not None:
            if not isinstance(report[key], str):
                raise ValueError(f"{key} must be a string")
            f[key] = report[key][:limit]
    if "delivery" in report:
        f["delivery"] = _delivery(report["delivery"])
    if "items" in report:
        f["items"] = _items(report["items"])
    if report.get("verdict") is not None and report["verdict"] not in VERDICTS:
        raise ValueError(f"verdict is one of {list(VERDICTS)}")
    return f


def _from_verdict(verdict: Any, review_id: Any, f: dict[str, Any], current: str) -> str:
    """The state a verdict (and whether a review row was written) names."""
    if verdict is not None and review_id:
        if verdict == "delivered":
            f["reason"] = REVIEW_REASON
        elif not f.get("reason"):
            raise ValueError("a non-delivered verdict carries its hold reason")
        return "review"
    if verdict in ("held", "failed"):
        if not f.get("reason"):
            raise ValueError("a held or failed verdict carries its reason")
        return verdict
    if verdict == "delivered":
        raise ValueError("a delivered verdict names the review row it wrote (delivery.review_id)")
    return current  # a same-state note (cents, stage)


def resolve(report: dict[str, Any], current: str) -> tuple[str, dict[str, Any]]:
    """The runner's report as (next state, checked fields).

    An explicit ``state`` wins. Otherwise: ``delivery.status`` ``sent`` ->
    ``delivered``, ``rejected`` / ``expired`` / ``failed`` -> ``failed``; a
    verdict with a review row written -> ``review`` (its reason
    ``matter_files_review`` for a delivered verdict, else the hold reason); a
    ``held`` / ``failed`` verdict with no row -> that state."""
    f = _fields(report)
    delivery = f.get("delivery") or {}
    status, review_id = delivery.get("status"), delivery.get("review_id")
    state = report.get("state")
    if state is not None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
    elif status == "sent":
        state = "delivered"
    elif status in ("rejected", "expired", "failed"):
        state = "failed"
        f.setdefault("reason", f"review_{status}")
    else:
        state = _from_verdict(report.get("verdict"), review_id, f, current)
    if state == "review" and not review_id:
        raise ValueError("a job in review names its review row (delivery.review_id)")
    return state, f


__all__ = [
    "ACKS",
    "ALLOWED_NEXT",
    "CLOSE_REF",
    "CLOSED_REASON",
    "CREATE_INDEXES",
    "CREATE_LINES_SQL",
    "CREATE_SQL",
    "EnvelopeError",
    "KIND",
    "LIVE_STATES",
    "MAX_REQUEST_TEXT",
    "PROJECTION",
    "REVIEW_REASON",
    "SKILL_NAME",
    "STATES",
    "SubmitRefused",
    "TERMINAL_STATES",
    "digest",
    "resolve",
    "validate_envelope",
]
