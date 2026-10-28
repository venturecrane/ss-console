"""One case file job as the runner reads it: the broker's envelope plus what
the lane stamps before every run (the seat's slug, the month's case file spend,
read fresh, and the two caps from the firm config).

The envelope (the broker's ``casefile_job_submit`` / ``casefile_apply_submit``
writes exactly this)::

    {"kind": "casefile", "job_id": ULID, "trigger": "request"|"scheduled",
     "mode": "full"|"recheck"|"apply",
     "requester": email, "message_ref": str, "request_text": str,
     # full / recheck:
     "scope": {"assisting": {"names": [str, ...]}}       a caseload: the matters
                                                         these people assist on
            | {"matters": {"numbers": [str, ...]}}       named matters
            | {"assisting_staff_ids": [uuid, ...]},      (root only)
     "file_to_matter_id": uuid, "file_to_matter_number": str, "folder_name": str,
     # apply:
     "apply": {"source_job_id": ULID, "approver": email, "thread_ref": str,
               "lines": [{"n", "kind": "task"|"medicals", "matter_id",
                          "source_id", "sha256"}, ...]}}

Re-validated here (the broker's package is not on the runner's venv). The
kinds are ``full``, ``recheck`` (the weekday schedule: only what changed) and
``apply``. A ``scheduled`` job's ``message_ref`` is
``scheduled:<firm date>:<caseload key>`` (``casefile_schedule``), never an email.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JOB_FILE = "job.json"
TRIGGERS = ("request", "scheduled")
MODES = ("full", "recheck", "apply")
LINE_KINDS = ("task", "medicals")
MAX_NAMES = 10
MAX_NUMBERS = 200
MAX_LINES = 999
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")
_FOLDER = re.compile(r"^[^/\\\x00-\x1f]{1,120}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SOURCE = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")


class CasefileJobError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    #: Person Assisting names (resolved by the runner against ``/staff``).
    names: tuple[str, ...] = ()
    #: Person Assisting staff ids (root's scope; already resolved).
    staff_ids: tuple[str, ...] = ()
    #: Matter numbers named.
    numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Line:
    n: int
    kind: str
    matter_id: str
    source_id: str
    sha256: str


@dataclass(frozen=True)
class Apply:
    source_job_id: str
    approver: str
    thread_ref: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class CasefileJob:
    job_dir: Path
    job_id: str
    slug: str
    trigger: str
    mode: str
    requester: str
    message_ref: str
    request_text: str
    month_cents_used: int
    scope: Scope | None = None
    file_to_id: str = ""
    file_to_number: str = ""
    folder_name: str = ""
    apply: Apply | None = None
    per_job_cap_usd: float | None = None
    monthly_budget_usd: float | None = None

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str, *, empty_ok: bool = False) -> str:
    v = data.get(key)
    if not isinstance(v, str) or (not v.strip() and not empty_ok):
        raise CasefileJobError(f"{key} is required")
    return v.strip()


def _names(raw: Any, cap: int, rx: re.Pattern[str] | None = None) -> tuple[str, ...] | None:
    if not (isinstance(raw, list) and raw and len(raw) <= cap and all(isinstance(x, str) for x in raw)):
        return None
    out = tuple(dict.fromkeys(" ".join(x.split()) for x in raw))
    if any(not x or len(x) > 100 or (rx is not None and not rx.match(x)) for x in out):
        return None
    return out


def _scope(raw: Any) -> Scope:
    if isinstance(raw, dict) and set(raw) == {"assisting"} and isinstance(raw["assisting"], dict):
        names = _names(raw["assisting"].get("names"), MAX_NAMES)
        if names is not None and set(raw["assisting"]) == {"names"}:
            return Scope(names=names)
    if isinstance(raw, dict) and set(raw) == {"matters"} and isinstance(raw["matters"], dict):
        nums = _names(raw["matters"].get("numbers"), MAX_NUMBERS, _NUMBER)
        if nums is not None and set(raw["matters"]) == {"numbers"}:
            return Scope(numbers=nums)
    if isinstance(raw, dict) and set(raw) == {"assisting_staff_ids"}:
        ids = raw["assisting_staff_ids"]
        if isinstance(ids, list) and ids and all(isinstance(i, str) and _UUID.match(i) for i in ids):
            return Scope(staff_ids=tuple(dict.fromkeys(i.lower() for i in ids)))
    raise CasefileJobError(
        'scope must be {"assisting": {"names": [...]}}, {"matters": {"numbers": [...]}} '
        'or {"assisting_staff_ids": [uuid, ...]}'
    )


def _line(raw: Any) -> Line:
    if not (isinstance(raw, dict) and set(raw) == {"n", "kind", "matter_id", "source_id", "sha256"}):
        raise CasefileJobError("each apply line carries exactly n, kind, matter_id, source_id and sha256")
    n = raw["n"]
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_LINES):
        raise CasefileJobError("an apply line's n is 1..999")
    if raw["kind"] not in LINE_KINDS:
        raise CasefileJobError(f"an apply line's kind is one of {list(LINE_KINDS)}")
    mid, src, sha = raw["matter_id"], raw["source_id"], raw["sha256"]
    if not (isinstance(mid, str) and _UUID.match(mid)):
        raise CasefileJobError("an apply line's matter_id is the matter's uuid")
    if not (isinstance(src, str) and _SOURCE.match(src)):
        raise CasefileJobError("an apply line's source_id is a plain identifier")
    if not (isinstance(sha, str) and _SHA.match(sha)):
        raise CasefileJobError("an apply line's sha256 is the proposal's hash")
    return Line(n, raw["kind"], mid.lower(), src, sha)


def _apply(raw: Any) -> Apply:
    if not (isinstance(raw, dict) and set(raw) == {"source_job_id", "approver", "thread_ref", "lines"}):
        raise CasefileJobError("apply carries exactly source_job_id, approver, thread_ref and lines")
    src, who, thread = raw["source_job_id"], raw["approver"], raw["thread_ref"]
    if not (isinstance(src, str) and _ULID.match(src)):
        raise CasefileJobError("apply.source_job_id must be a ULID")
    if not (isinstance(who, str) and _EMAIL.match(who)):
        raise CasefileJobError("apply.approver must be an email address")
    if not (isinstance(thread, str) and 0 < len(thread) <= 512):
        raise CasefileJobError("apply.thread_ref is the thread the lines were raised on")
    lines = raw["lines"]
    # Empty only when the lane scoped a resumed apply to the lines it still holds.
    if not (isinstance(lines, list) and len(lines) <= MAX_LINES):
        raise CasefileJobError("apply.lines is a list")
    parsed = tuple(_line(x) for x in lines)
    if len({ln.n for ln in parsed}) != len(parsed):
        raise CasefileJobError("apply.lines names a line number twice")
    return Apply(src, who.strip().lower(), thread, parsed)


def _cap(data: dict[str, Any], key: str) -> float | None:
    v = data.get(key)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        raise CasefileJobError(f"{key} must be a number > 0 when stamped")
    return float(v)


def _filing(data: dict[str, Any]) -> tuple[str, str, str]:
    """The library matter: its number always, its id when the seat authored
    one. With no id, the runner resolves the number to exactly one Open matter
    before anything is paid (``run._library``), or holds ``library_unresolved``."""
    fid, fnum = data.get("file_to_matter_id") or "", data.get("file_to_matter_number")
    if not (isinstance(fid, str) and (not fid or _UUID.match(fid)) and isinstance(fnum, str) and _NUMBER.match(fnum)):
        raise CasefileJobError("file_to_matter_number (and file_to_matter_id when given) must name the filing matter")
    folder = _str(data, "folder_name")
    if not _FOLDER.match(folder):
        raise CasefileJobError("folder_name must be one folder name (no slashes)")
    return fid.lower(), fnum, folder


def parse(data: Any, job_dir: Path) -> CasefileJob:
    if not isinstance(data, dict) or data.get("kind") != "casefile":
        raise CasefileJobError("not a case file job (kind must be 'casefile')")
    job_id = _str(data, "job_id")
    if not _ULID.match(job_id):
        raise CasefileJobError("job_id must be a ULID")
    trigger, mode = data.get("trigger"), data.get("mode")
    if trigger not in TRIGGERS:
        raise CasefileJobError(f"trigger must be one of {list(TRIGGERS)}")
    if mode not in MODES:
        raise CasefileJobError(f"mode must be one of {list(MODES)}")
    requester = _str(data, "requester")
    if not _EMAIL.match(requester):
        raise CasefileJobError("requester must be an email address")
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise CasefileJobError("month_cents_used must be stamped (a non-negative int) before a run")
    common: dict[str, Any] = {
        "job_dir": job_dir,
        "job_id": job_id,
        "slug": _str(data, "slug"),
        "trigger": str(trigger),
        "mode": str(mode),
        "requester": requester.lower(),
        "message_ref": _str(data, "message_ref"),
        "request_text": _str(data, "request_text", empty_ok=trigger == "scheduled"),
        "month_cents_used": cents,
        "per_job_cap_usd": _cap(data, "per_job_cap_usd"),
        "monthly_budget_usd": _cap(data, "monthly_budget_usd"),
    }
    if mode == "apply":
        return CasefileJob(**common, apply=_apply(data.get("apply")))
    fid, fnum, folder = _filing(data)
    return CasefileJob(
        **common, scope=_scope(data.get("scope")), file_to_id=fid, file_to_number=fnum, folder_name=folder
    )


def load(job_dir: Path) -> CasefileJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CasefileJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))


__all__ = ["Apply", "CasefileJob", "CasefileJobError", "Line", "Scope", "load", "parse"]
