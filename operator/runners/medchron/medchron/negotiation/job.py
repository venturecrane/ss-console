"""One negotiation-watch job as the runner reads it: the broker's envelope plus
what the lane stamps before every run (the seat's slug and the month's
negotiation spend, read fresh).

The envelope (the broker's ``negotiation_job_submit`` writes exactly this)::

    {"kind": "negotiation", "job_id": ULID, "trigger": "scheduled",
     "requester": email, "message_ref": "scheduled:<YYYY-MM-DDTHH>",
     "matter_statuses": [str], "negotiation_design": guid | "",
     "firm_words": [str], "seed_saved_before": "YYYY-MM-DDTHH:MM:SSZ" | "",
     "per_job_cap_usd": n, "monthly_budget_usd": n}

Re-validated here: the runner refuses an envelope it cannot read the same way
the broker refused to queue one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_FILE = "job.json"
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WORD = re.compile(r"^[a-z]{3,40}$")


class NegotiationJobError(ValueError):
    pass


@dataclass(frozen=True)
class NegotiationJob:
    job_dir: Path
    job_id: str
    slug: str
    matter_statuses: tuple[str, ...]
    negotiation_design: str | None
    firm_words: tuple[str, ...]
    #: First-run seed cutoff (UTC): a never-seen matter's files saved before it
    #: are taken as already handled; None seeds every file present.
    seed_saved_before: datetime | None
    per_job_cap_usd: float
    monthly_budget_usd: float
    month_cents_used: int

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _usd(data: dict[str, Any], key: str) -> float:
    v = data.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise NegotiationJobError(f"{key} must be a positive number")
    return float(v)


def _seed(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise NegotiationJobError("seed_saved_before must be an ISO UTC timestamp") from None
    if when.tzinfo is None:
        raise NegotiationJobError("seed_saved_before must carry its timezone")
    return when.astimezone(timezone.utc)


def parse(data: Any, job_dir: Path) -> NegotiationJob:
    if not isinstance(data, dict) or data.get("kind") != "negotiation":
        raise NegotiationJobError("job.json is not a negotiation job")
    job_id = str(data.get("job_id") or "")
    if not _ULID.match(job_id):
        raise NegotiationJobError("job_id must be a ULID")
    slug = str(data.get("slug") or "").strip()
    if not slug:
        raise NegotiationJobError("slug is required")
    statuses = data.get("matter_statuses") or ["Open"]
    if not isinstance(statuses, list) or not all(isinstance(s, str) and s.strip() for s in statuses):
        raise NegotiationJobError("matter_statuses must be a list of statuses")
    design = str(data.get("negotiation_design") or "").strip().lower() or None
    if design is not None and not _GUID.match(design):
        raise NegotiationJobError("negotiation_design must be a layout design guid")
    words = data.get("firm_words") or []
    if not isinstance(words, list) or not all(isinstance(w, str) and _WORD.match(w) for w in words):
        raise NegotiationJobError("firm_words must be lowercase words")
    seed = _seed(data.get("seed_saved_before"))
    month = data.get("month_cents_used")
    if isinstance(month, bool) or not isinstance(month, int) or month < 0:
        raise NegotiationJobError("month_cents_used must be the month's spend in cents")
    return NegotiationJob(
        job_dir=job_dir,
        job_id=job_id,
        slug=slug,
        matter_statuses=tuple(s.strip() for s in statuses),
        negotiation_design=design,
        firm_words=tuple(words),
        seed_saved_before=seed,
        per_job_cap_usd=_usd(data, "per_job_cap_usd"),
        monthly_budget_usd=_usd(data, "monthly_budget_usd"),
        month_cents_used=month,
    )


def load(job_dir: Path) -> NegotiationJob:
    try:
        data = json.loads((Path(job_dir) / JOB_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NegotiationJobError(f"job.json could not be read ({type(exc).__name__})") from exc
    return parse(data, Path(job_dir))


__all__ = ["JOB_FILE", "NegotiationJob", "NegotiationJobError", "load", "parse"]
