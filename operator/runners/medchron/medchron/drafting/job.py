"""One drafting job as the runner reads it: the broker's envelope plus what the
lane stamps before every run (the seat's slug and the month's drafting spend,
read fresh, never taken from the envelope).

The envelope (the broker's ``drafting_job_submit`` writes exactly this)::

    {"kind": "drafting", "job_id": <ULID>, "matter_id": str, "matter_number": str,
     "document_class": one of CLASSES, "requester": email, "message_ref": str,
     "request_text": str, "file_to_matter_id": str|null, "file_to_matter_number": str|null}

Re-validated here rather than imported from the broker, whose package is not on
the runner's venv: the runner refuses an envelope it cannot read the same way
the broker refused to queue one. Not demand's ``job.py``, which refuses any
kind but ``demand`` and carries a different envelope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .firm import CLASSES

JOB_FILE = "job.json"
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")


class DraftingJobError(ValueError):
    pass


@dataclass(frozen=True)
class DraftingJob:
    job_dir: Path
    job_id: str
    slug: str
    matter_id: str
    matter_number: str
    document_class: str
    requester: str
    message_ref: str
    request_text: str
    file_to_id: str
    file_to_number: str
    month_cents_used: int
    #: Drafting jobs left this cycle with this one excluded; None on a laptop run.
    allowance_remaining: int | None = None

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str) -> str:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        raise DraftingJobError(f"{key} is required")
    return v.strip()


def parse(data: Any, job_dir: Path) -> DraftingJob:
    if not isinstance(data, dict) or data.get("kind") != "drafting":
        raise DraftingJobError("not a drafting job (kind must be 'drafting')")
    job_id, mid, number = _str(data, "job_id"), _str(data, "matter_id"), _str(data, "matter_number")
    if not _ULID.match(job_id):
        raise DraftingJobError("job_id must be a ULID")
    if not _UUID.match(mid) or not _NUMBER.match(number):
        raise DraftingJobError("matter_id and matter_number must name the matter")
    cls = data.get("document_class")
    if cls not in CLASSES:
        raise DraftingJobError(f"document_class must be one of {list(CLASSES)}")
    requester = _str(data, "requester")
    if not _EMAIL.match(requester):
        raise DraftingJobError("requester must be an email address")
    fid, fnum = data.get("file_to_matter_id"), data.get("file_to_matter_number")
    if (fid is None) != (fnum is None):
        raise DraftingJobError("file_to_matter_id and file_to_matter_number come together or not at all")
    if fid is not None:
        if not (isinstance(fid, str) and _UUID.match(fid) and isinstance(fnum, str) and _NUMBER.match(fnum)):
            raise DraftingJobError("file_to_matter_id and file_to_matter_number must name a matter")
    else:
        fid, fnum = mid, number
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise DraftingJobError(
            "month_cents_used must be stamped (a non-negative int) before a run; unmetered is refused"
        )
    remaining = data.get("allowance_remaining")
    return DraftingJob(
        job_dir=job_dir,
        job_id=job_id,
        slug=_str(data, "slug"),
        matter_id=mid,
        matter_number=number,
        document_class=str(cls),
        requester=requester,
        message_ref=_str(data, "message_ref"),
        request_text=_str(data, "request_text"),
        file_to_id=str(fid),
        file_to_number=str(fnum),
        month_cents_used=cents,
        allowance_remaining=remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else None,
    )


def load(job_dir: Path) -> DraftingJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DraftingJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))
