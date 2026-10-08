"""One litigation job as the runner reads it: the broker's envelope plus what
the lane stamps before every run (the seat's slug, the month's litigation
spend, read fresh, and the two caps from the firm config).

The envelope (the broker's ``litigation_job_submit`` writes exactly this)::

    {"kind": "litigation", "job_id": ULID, "trigger": "request"|"scheduled",
     "requester": email, "message_ref": str, "request_text": str,
     "scope": {"attorney_staff_ids": [uuid, ...]} | {"all": true},
     "file_to_matter_id": uuid, "file_to_matter_number": str, "folder_name": str}

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

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str, *, empty_ok: bool = False) -> str:
    v = data.get(key)
    if not isinstance(v, str) or (not v.strip() and not empty_ok):
        raise LitigationJobError(f"{key} is required")
    return v.strip()


def _scope(raw: Any) -> tuple[tuple[str, ...], bool]:
    if raw == {"all": True}:
        return (), True
    if isinstance(raw, dict) and set(raw) == {"attorney_staff_ids"}:
        ids = raw["attorney_staff_ids"]
        if isinstance(ids, list) and ids and all(isinstance(i, str) and _UUID.match(i) for i in ids):
            return tuple(dict.fromkeys(i.lower() for i in ids)), False
    raise LitigationJobError('scope must be {"attorney_staff_ids": [uuid, ...]} (non-empty) or {"all": true}')


def _cap(data: dict[str, Any], key: str) -> float | None:
    v = data.get(key)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        raise LitigationJobError(f"{key} must be a number > 0 when stamped")
    return float(v)


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
    ids, everything = _scope(data.get("scope"))
    fid, fnum = data.get("file_to_matter_id"), data.get("file_to_matter_number")
    if not (isinstance(fid, str) and _UUID.match(fid) and isinstance(fnum, str) and _NUMBER.match(fnum)):
        raise LitigationJobError("file_to_matter_id and file_to_matter_number must name the filing matter")
    folder = _str(data, "folder_name")
    if not _FOLDER.match(folder):
        raise LitigationJobError("folder_name must be one folder name (no slashes)")
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
    )


def load(job_dir: Path) -> LitigationJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LitigationJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))
