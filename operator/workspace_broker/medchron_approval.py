"""SMD's approval on a held chronology's resume (``medchron_job_resume``).

``overage`` parses it for ``medchron_verbs``: pages past the cycle allowance
(``jobs.medchron.allowance approve --pages``), or a dollar ceiling for a job
held on a cost line (``approve --usd``): the job cap or the cycle budget, at a
projection check or when spend reached a line mid-run (2026-11-20). Root only,
no agent tool. Its own module because ``medchron_verbs`` sits at the size
ceiling.
"""

from __future__ import annotations

import math
from typing import Any

#: The settings a cost hold names (``jobs.limits``); every one is approvable.
COST_HOLD_KEYS = ("per_job_cap_usd", "monthly_budget_usd")


def approved_usd(value: object) -> float | None:
    """A dollar approval as the broker accepts it: a finite number above 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    usd = float(value)
    return usd if math.isfinite(usd) and usd > 0 else None


def _approver(request: dict[str, Any]) -> str:
    by = request.get("allowance_approved_by")
    if not isinstance(by, str) or not by.strip() or len(by) > 120:
        raise ValueError("allowance_approved_by must name who approved (at most 120 characters)")
    return by.strip()


def overage(request: dict[str, Any], row: dict[str, Any], allowance_key: str) -> dict[str, Any] | None:
    """SMD's approval past a hold (``jobs.medchron.allowance approve``, root
    only, no agent tool): pages past the cycle allowance, or a dollar ceiling
    for a job held on a cost line. None when the request carries no approval;
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
            raise ValueError("allowance_approved_usd must be a positive, finite number")
        by = _approver(request)
        if row.get("state") != "held" or not reason.startswith(tuple(f"{k}:" for k in COST_HOLD_KEYS)):
            raise ValueError(f"job {row.get('id')} is not held on a cost line; a dollar approval does not apply")
        return {"allowance_approved_usd": ceiling, "allowance_approved_by": by}
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError("allowance_approved_pages must be a positive integer")
    by = _approver(request)
    if row.get("state") != "held" or not reason.startswith(allowance_key):
        raise ValueError(f"job {row.get('id')} is not held on the page allowance; an approved overage does not apply")
    return {"allowance_approved_pages": pages, "allowance_approved_by": by}
