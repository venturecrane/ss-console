"""One records job as the runner reads it: the broker's envelope plus what the
lane stamps before every run (the seat's slug, the month's records spend read
fresh, the two caps from ``records-firm.yaml``).

The envelope the broker's ``records_job_submit`` queues::

    {"kind": "records", "job_id": ULID, "trigger": "request",
     "requester": email, "message_ref": Graph message id,
     "internet_message_id": str, "request_ref": str,
     "request_text": the sender's OWN words, "matter_id": uuid | number | null,
     "ack": "turn" | "none"}

Re-validated here: the broker's package is not on the runner's venv.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JOB_FILE = "job.json"
MAX_REQUEST_TEXT = 20_000
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,63}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")


class RecordsJobError(ValueError):
    pass


@dataclass(frozen=True)
class RecordsJob:
    job_dir: Path
    job_id: str
    slug: str
    requester: str
    message_ref: str
    request_text: str
    month_cents_used: int
    #: The matter's Smokeball id; empty when the envelope named it by number.
    matter_id: str = ""
    #: The matter number as staff wrote it, when the envelope named no id.
    matter_number: str = ""
    ack: str = "turn"
    per_job_cap_usd: float | None = None
    monthly_budget_usd: float | None = None

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str) -> str:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        raise RecordsJobError(f"{key} is required")
    return v


def _cap(data: dict[str, Any], key: str) -> float | None:
    v = data.get(key)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        raise RecordsJobError(f"{key} must be a number > 0 when stamped")
    return float(v)


def parse(data: Any, job_dir: Path) -> RecordsJob:
    if not isinstance(data, dict) or data.get("kind") != "records":
        raise RecordsJobError("not a records job (kind must be 'records')")
    job_id = _str(data, "job_id").strip()
    if not _ULID.match(job_id):
        raise RecordsJobError("job_id must be a ULID")
    if data.get("trigger") != "request":
        raise RecordsJobError("a records job's trigger is request")
    requester = _str(data, "requester").strip().lower()
    if not _EMAIL.match(requester):
        raise RecordsJobError("requester must be an email address")
    text = _str(data, "request_text")
    if len(text) > MAX_REQUEST_TEXT:
        raise RecordsJobError(f"request_text is over {MAX_REQUEST_TEXT} characters")
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise RecordsJobError("month_cents_used must be stamped (a non-negative int) before a run")
    raw = data.get("matter_id")
    mid = num = ""
    if isinstance(raw, str) and raw.strip():
        if _UUID.match(raw.strip()):
            mid = raw.strip().lower()
        elif _NUMBER.match(raw.strip()):
            num = " ".join(raw.split())
        else:
            raise RecordsJobError("matter_id, when given, is the matter's id or number")
    elif raw not in (None, ""):
        raise RecordsJobError("matter_id, when given, is a string")
    ack = data.get("ack", "turn")
    if ack not in ("turn", "none"):
        raise RecordsJobError("ack is turn or none")
    return RecordsJob(
        job_dir=job_dir,
        job_id=job_id,
        slug=_str(data, "slug").strip(),
        requester=requester,
        message_ref=_str(data, "message_ref").strip(),
        request_text=text,
        month_cents_used=cents,
        matter_id=mid,
        matter_number=num,
        ack=str(ack),
        per_job_cap_usd=_cap(data, "per_job_cap_usd"),
        monthly_budget_usd=_cap(data, "monthly_budget_usd"),
    )


def load(job_dir: Path) -> RecordsJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordsJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))


__all__ = ["RecordsJob", "RecordsJobError", "load", "parse"]
