"""One litigation job as the runner reads it: the broker's envelope plus what
the lane stamps before every run (the seat's slug, the month's litigation
spend, read fresh, and the two caps from the firm config).

The envelope (the broker's ``litigation_job_submit`` writes exactly this)::

    {"kind": "litigation", "job_id": ULID, "trigger": "request"|"scheduled",
     "requester": email, "message_ref": str, "request_text": str,
     "scope": {"all": true}
            | {"attorney_staff_ids": [uuid, ...]}
            | {"paralegal_staff_ids": [uuid, ...]}
            | {"attorney_staff_ids": [...], "paralegal_staff_ids": [...]},
     "file_to_matter_id": uuid, "file_to_matter_number": str, "folder_name": str}

plus, for the deadlines report only (a $0 kind with its own runner,
``deadlines/``), ``"report": "deadlines"`` and ``"workbooks"``: a non-empty
subset of ``["deadlines", "statute"]``. An envelope without ``report`` is the
status list, exactly as before.

Re-validated here (the broker's package is not on the runner's venv): the
runner refuses an envelope it cannot read the same way the broker refused to
queue one. A scheduled job carries the requester the broker filled in and a
``scheduled:<YYYY-MM-DD>`` message ref; its request text may be empty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JOB_FILE = "job.json"
TRIGGERS = ("request", "scheduled")
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")
_SCHEDULED_REF = re.compile(r"^scheduled:\d{4}-\d{2}-\d{2}$")
_FOLDER = re.compile(r"^[^/\\\x00-\x1f]{1,120}$")
REPORTS = ("status", "deadlines")
WORKBOOKS = ("deadlines", "statute")


class LitigationJobError(ValueError):
    pass


@dataclass(frozen=True)
class LitigationJob:
    job_dir: Path
    job_id: str
    slug: str
    trigger: str
    requester: str
    message_ref: str
    request_text: str
    #: Staff ids whose matters are in scope; empty with ``scope_all``.
    attorney_staff_ids: tuple[str, ...]
    scope_all: bool
    file_to_id: str
    file_to_number: str
    folder_name: str
    month_cents_used: int
    per_job_cap_usd: float | None = None
    monthly_budget_usd: float | None = None
    #: Person Assisting staff ids (a paralegal's files). With attorney ids
    #: too, a matter must match both; empty when no paralegal was named.
    paralegal_staff_ids: tuple[str, ...] = ()
    #: ``status`` (the list) or ``deadlines`` (the $0 deadlines report).
    report: str = "status"
    #: The deadlines report's workbooks, in ``WORKBOOKS`` order; empty for status.
    workbooks: tuple[str, ...] = ()

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str, *, empty_ok: bool = False) -> str:
    v = data.get(key)
    if not isinstance(v, str) or (not v.strip() and not empty_ok):
        raise LitigationJobError(f"{key} is required")
    return v.strip()


SCOPE_ID_KEYS = ("attorney_staff_ids", "paralegal_staff_ids")


def _ids(raw: Any) -> tuple[str, ...] | None:
    if isinstance(raw, list) and raw and all(isinstance(i, str) and _UUID.match(i) for i in raw):
        return tuple(dict.fromkeys(i.lower() for i in raw))
    return None


def _scope(raw: Any) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
    """(attorney ids, every matter, paralegal ids)."""
    if raw == {"all": True}:
        return (), True, ()
    if isinstance(raw, dict) and raw and set(raw) <= set(SCOPE_ID_KEYS):
        got = {k: _ids(raw[k]) for k in raw}
        if all(v is not None for v in got.values()):
            return got.get("attorney_staff_ids") or (), False, got.get("paralegal_staff_ids") or ()
    raise LitigationJobError(
        'scope must be {"all": true} or non-empty staff id lists under "attorney_staff_ids", '
        '"paralegal_staff_ids" or both'
    )


def _cap(data: dict[str, Any], key: str) -> float | None:
    v = data.get(key)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        raise LitigationJobError(f"{key} must be a number > 0 when stamped")
    return float(v)


def _report(data: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    report = data.get("report", "status")
    if report not in REPORTS:
        raise LitigationJobError(f"report must be one of {list(REPORTS)}")
    raw = data.get("workbooks")
    if report == "status":
        if raw is not None:
            raise LitigationJobError("workbooks belongs to the deadlines report only")
        return "status", ()
    if not (isinstance(raw, list) and raw and set(raw) <= set(WORKBOOKS)):
        raise LitigationJobError(f"workbooks must be a non-empty subset of {list(WORKBOOKS)}")
    return "deadlines", tuple(w for w in WORKBOOKS if w in raw)


def parse(data: Any, job_dir: Path) -> LitigationJob:
    if not isinstance(data, dict) or data.get("kind") != "litigation":
        raise LitigationJobError("not a litigation job (kind must be 'litigation')")
    job_id = _str(data, "job_id")
    if not _ULID.match(job_id):
        raise LitigationJobError("job_id must be a ULID")
    trigger = data.get("trigger")
    if trigger not in TRIGGERS:
        raise LitigationJobError(f"trigger must be one of {list(TRIGGERS)}")
    requester = _str(data, "requester")
    if not _EMAIL.match(requester):
        raise LitigationJobError("requester must be an email address")
    ref = _str(data, "message_ref")
    if trigger == "scheduled" and not _SCHEDULED_REF.match(ref):
        raise LitigationJobError("a scheduled job's message_ref is scheduled:<YYYY-MM-DD>")
    text = _str(data, "request_text", empty_ok=(trigger == "scheduled"))
    ids, everything, paralegals = _scope(data.get("scope"))
    fid, fnum = data.get("file_to_matter_id"), data.get("file_to_matter_number")
    if not (isinstance(fid, str) and _UUID.match(fid) and isinstance(fnum, str) and _NUMBER.match(fnum)):
        raise LitigationJobError("file_to_matter_id and file_to_matter_number must name the filing matter")
    folder = _str(data, "folder_name")
    if not _FOLDER.match(folder):
        raise LitigationJobError("folder_name must be one folder name (no slashes)")
    report, workbooks = _report(data)
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise LitigationJobError(
            "month_cents_used must be stamped (a non-negative int) before a run; unmetered is refused"
        )
    return LitigationJob(
        job_dir=job_dir,
        job_id=job_id,
        slug=_str(data, "slug"),
        trigger=str(trigger),
        requester=requester,
        message_ref=ref,
        request_text=text,
        attorney_staff_ids=ids,
        scope_all=everything,
        file_to_id=fid,
        file_to_number=fnum,
        folder_name=folder,
        month_cents_used=cents,
        per_job_cap_usd=_cap(data, "per_job_cap_usd"),
        monthly_budget_usd=_cap(data, "monthly_budget_usd"),
        paralegal_staff_ids=paralegals,
        report=report,
        workbooks=workbooks,
    )


def load(job_dir: Path) -> LitigationJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LitigationJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))
