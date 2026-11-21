"""A chronology unit's WHOLE price, asked once at its first paid check.

WHY (2026-11-20). The one check every unit asks before its first paid stage
(``driver._check_limits`` -> ``limits.check_before_first_paid``) compared the
firm's ``per_job_cap_usd`` and ``monthly_budget_usd`` with ONE stage's
projection. A stopped update of 2,918 new pages passed it and then spent past
what either figure allows. At that check the projection is now the unit's
whole price: the pages the unit is asked about (``update/charged.question_pages``)
times the worst rate measured for its kind, less what the unit's own ledger has
already spent (a resumed unit is priced on what remains; a joint matter's
second unit is priced for its own chronology, once). The stage's own
projection still stands when it is larger, so the check is never looser than
it was.

SMD's dollar approval (``jobs.medchron.allowance approve --usd``) replaces the
job's cap with the approved figure and widens the cycle line by it for this job
alone: the approval is the job's hard ceiling, enforced by the existing
per-call and per-batch checks, and the whole-job price is not asked again.

The price is never part of a hold reason (``limits`` module docstring); it rides
beside it as integer cents (``Outcome.price_cents``) to run state and SMD's
alert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import budget as budget_mod, limits as limits_mod
from . import job as job_mod, projection
from .update import charged as update_charged

#: Dollars a page, the worst measured bill for each kind of job:
#: a full chronology, the 2026-11-19 full job ($247.29 over 3,821 pages, 0.0647);
#: an update, the 2026-11-17 update ($70.96 over 509 pages read, 0.1394).
FULL_USD_PER_PAGE = 0.065
UPDATE_USD_PER_PAGE = 0.139


def whole_price_usd(pages: int, *, update: bool) -> float:
    """The unit's whole price: its pages at its kind's measured rate."""
    return max(0, int(pages)) * (UPDATE_USD_PER_PAGE if update else FULL_USD_PER_PAGE)


def unit_spent_usd(pricing: budget_mod.Pricing, ledger: Path) -> float:
    """What this unit's own usage ledger has spent, priced by the job's card."""
    return budget_mod.Budget(pricing, 0.0, [ledger], 1.0).refresh()


def remaining_usd(price_usd: float, unit_spent: float) -> float:
    """What is left of the unit's price after its own spend, never below 0."""
    return max(0.0, price_usd - unit_spent)


def cents(usd: float) -> int:
    return int(round(usd * 100))


def approved_limits(cap_usd: float, monthly_usd: float, approved_usd: float | None) -> tuple[float, float]:
    """(job cap, cycle line) for this job. With SMD's approval the cap IS the
    approval and the cycle line is widened by it, so the cap binds first."""
    if approved_usd is None:
        return cap_usd, monthly_usd
    return approved_usd, monthly_usd + approved_usd


def _priced(d: Any, unit: str, pages: int) -> tuple[float, float] | None:
    """(the unit's whole price, what remains of it after the unit's own
    spend), or None once SMD approved a dollar ceiling for this job."""
    if d._approved_usd is not None:
        return None
    price = whole_price_usd(pages, update=d.job.update is not None)
    ledger = d.slug_dir / "runs" / unit / job_mod.USAGE_LEDGER
    return price, remaining_usd(price, unit_spent_usd(d.pricing, ledger))


def check_first_paid(d: Any, unit: str, stage: str, extracted: Path, *, projected: float, spent: float) -> None:
    """The driver's check before a unit's first paid stage: the page question,
    then the two cost limits against the unit's remaining whole price (or the
    stage's projection, if larger). A hold carries its figures beside the
    reason: an allowance hold its page counts, a cost hold the unit's price."""
    pages = update_charged.question_pages(d.slug_dir, unit, extracted, d.job.update)
    price = _priced(d, unit, pages)
    try:
        d.limits.check_before_first_paid(
            pages=pages,
            projected_usd=projected if price is None else max(projected, price[1]),
            spent_usd=spent,
            stage=stage,
            approved_pages=d._approved_pages,
        )
    except limits_mod.LimitHold as hold:
        if hold.allowance is not None:
            usd = projection.read_usd(extracted, d.limits.usd_per_scanned_page, d.budget)
            hold.allowance["projected_cents"] = cents(usd)
        elif price is not None:
            hold.price_cents = cents(price[0])
        raise
