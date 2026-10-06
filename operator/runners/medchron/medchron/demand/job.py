"""One demand job as the runner reads it: the broker's envelope (the contract in
``workspace_broker/demand_ledger.py``) plus what the daemon stamps before every
run and every resume (the month's demand spend, read fresh, never taken from
the envelope, which goes stale the moment another job records cents).

Re-validated here rather than imported: the broker module is not on the
runner's venv, and the runner must refuse an envelope it cannot read the same
way the broker refused to queue one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DELIVERABLES = ("gap_audit", "demand")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
JOB_FILE = "job.json"


class DemandJobError(ValueError):
    pass


@dataclass(frozen=True)
class DemandJob:
    job_dir: Path
    job_id: str
    slug: str
    matter_id: str
    matter_number: str
    file_to_id: str
    file_to_number: str
    requested_by: str
    request_ref: str
    request_text: str
    deliverables: tuple[str, ...]
    month_cents_used: int
    #: Demands left this cycle with this job excluded (the broker reserves
    #: submitted, running and held jobs). None on a laptop run.
    allowance_remaining: int | None = None

    @property
    def data(self) -> Path:
        return self.job_dir / "data"

    def wants(self, deliverable: str) -> bool:
        return deliverable in self.deliverables


def _matter(raw: Any, field: str) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise DemandJobError(f"{field} must be {{id, number}}")
    mid, number = str(raw.get("id") or ""), str(raw.get("number") or "")
    if not _UUID.match(mid) or not _NUMBER.match(number):
        raise DemandJobError(f"{field} must carry the matter's id and number")
    return mid, number


def parse(data: Any, job_dir: Path) -> DemandJob:
    if not isinstance(data, dict) or data.get("kind") != "demand":
        raise DemandJobError("not a demand job (kind must be 'demand')")
    mid, number = _matter(data.get("matter"), "matter")
    fid, fnumber = _matter(data["file_to"], "file_to") if data.get("file_to") else (mid, number)
    text = data.get("request_text")
    if not isinstance(text, str) or not text.strip():
        raise DemandJobError("request_text is required: the requester's brief is the drafting instruction")
    wanted = data.get("deliverables")
    if not isinstance(wanted, list) or not wanted or any(d not in DELIVERABLES for d in wanted):
        raise DemandJobError(f"deliverables must be drawn from {list(DELIVERABLES)}")
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise DemandJobError("month_cents_used must be stamped (a non-negative int) before a run; unmetered is refused")
    for key in ("job_id", "slug", "requested_by", "request_ref"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise DemandJobError(f"{key} is required")
    return DemandJob(
        job_dir=job_dir,
        job_id=data["job_id"],
        slug=data["slug"],
        matter_id=mid,
        matter_number=number,
        file_to_id=fid,
        file_to_number=fnumber,
        requested_by=data["requested_by"],
        request_ref=data["request_ref"],
        request_text=text,
        deliverables=tuple(d for d in DELIVERABLES if d in wanted),
        month_cents_used=cents,
        allowance_remaining=data.get("allowance_remaining")
        if isinstance(data.get("allowance_remaining"), int)
        else None,
    )


def load(job_dir: Path) -> DemandJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemandJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))
