"""SMD's approvals on a held chronology's resume, and the cents they commit.

``overage`` parses the approval ``medchron_job_resume`` takes (pages past the
cycle allowance, or a dollar ceiling past a whole-job price hold), for
``medchron_verbs``.

WHY (2026-11-20). The runner stamps the cycle's spend into a job's
``month_cents_used`` at every claim and resume (``jobs.medchron.job.stamp_period``),
and the cycle's ``cents_used`` counts only cents a job has recorded. A job SMD
approved past its price (``jobs.medchron.allowance approve --usd``) sits as a
resume marker until the daemon claims it, and in that gap another job's cycle
check would not see the money already committed. ``cents_committed`` is that
money: each pending resume marker's approved dollars, in cents. Billing's
``cents_used`` is unchanged.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

#: The resume marker the broker writes (``medchron_verbs._resume``).
MARKER_PREFIX = ".resume-"


def approved_usd(value: object) -> float | None:
    """A dollar approval as the broker accepts it: a finite number above 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    usd = float(value)
    return usd if math.isfinite(usd) and usd > 0 else None


def committed_cents(queue_dir: Path | None, exclude_job_id: str | None = None) -> int:
    """The approved cents of every pending resume marker but ``exclude_job_id``'s
    (a job's own resume is not metered against its own approval). An unreadable
    marker commits nothing: the runner's per-call ceiling still binds it."""
    if queue_dir is None or not queue_dir.is_dir():
        return 0
    total = 0
    for marker in queue_dir.glob(f"{MARKER_PREFIX}*.json"):
        try:
            doc = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("job_id") == exclude_job_id:
            continue
        usd = approved_usd(doc.get("allowance_approved_usd"))
        total += 0 if usd is None else int(round(usd * 100))
    return total


#: The settings a whole-job price hold names (``jobs.medchron.job_price``), and
#: the words its sentence carries (``limits.check_before_paid``); a cap stop
#: during a stage says "the run's spend reached" instead and is not approvable.
PRICE_HOLD_KEYS = ("per_job_cap_usd", "monthly_budget_usd")
PRICE_HOLD_WORDS = "this run's projected cost"


def _approver(request: dict[str, Any]) -> str:
    by = request.get("allowance_approved_by")
    if not isinstance(by, str) or not by.strip() or len(by) > 120:
        raise ValueError("allowance_approved_by must name who approved (at most 120 characters)")
    return by.strip()


def overage(request: dict[str, Any], row: dict[str, Any], allowance_key: str) -> dict[str, Any] | None:
    """SMD's approval past a hold (``jobs.medchron.allowance approve``, root
    only, no agent tool): pages past the cycle allowance, or a dollar ceiling
    past a whole-job price hold. None when the request carries no approval;
    refused when it carries a malformed one or the job is not held on what it
    approves. ``allowance_key`` is the page allowance's setting (the ledger's)."""
    pages, usd = request.get("allowance_approved_pages"), request.get("allowance_approved_usd")
    if pages is None and usd is None:
        return None
    if pages is not None and usd is not None:
        raise ValueError("an approval names pages or dollars, never both")
    reason = str(row.get("reason") or "")
    if usd is not None:
        ceiling = approved_usd(usd)
        if ceiling is None:
            raise ValueError("allowance_approved_usd must be a positive number")
        by = _approver(request)
        if row.get("state") != "held" or not (reason.startswith(PRICE_HOLD_KEYS) and PRICE_HOLD_WORDS in reason):
            raise ValueError(f"job {row.get('id')} is not held on its price; a dollar approval does not apply")
        return {"allowance_approved_usd": ceiling, "allowance_approved_by": by}
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError("allowance_approved_pages must be a positive integer")
    by = _approver(request)
    if row.get("state") != "held" or not reason.startswith(allowance_key):
        raise ValueError(f"job {row.get('id')} is not held on the page allowance; an approved overage does not apply")
    return {"allowance_approved_pages": pages, "allowance_approved_by": by}
