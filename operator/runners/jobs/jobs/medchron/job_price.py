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

Every cost hold of a chronology job (the job cap or the cycle line, at the
first paid check, at a later stage's projection, at a batch, or when spend
reached a line mid-call) can be approved by SMD with a dollar ceiling
(``jobs.medchron.allowance approve --usd N``). For that job the NEWEST approval
is the cap (its total spend ceiling) and the cycle line is not applied: the
approval is the cycle exception. A later, higher approval replaces it, so an
approved job stops only on its own ceiling, and that stop is approvable too.
The whole-job price is not asked again once a ceiling is approved.

The figure a hold tested (the projection it added: at the first paid check the
larger of the stage's projection and the unit's remaining price) rides beside
the reason as integer cents (``Outcome.price_cents``); SMD's alert prints the
ceiling that clears it. It is never part of a hold reason (``limits`` module
docstring).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .. import budget as budget_mod, limits as limits_mod
from . import job as job_mod, projection
from .update import charged as update_charged

#: Dollars a page: each kind's worst measured bill divided by the pages the price
#: multiplies, as ``update/charged.question_pages`` counts them for that job,
#: rounded up (measured 2026-11-20 on the saved real jobs, at $0):
#: full runs 247.29 / 3,963 = 0.0624, 46.90 / 986 = 0.0476, 12.07 / 494 = 0.0244;
#: updates 70.96 / 805 = 0.0881, 9.33 / 109 = 0.0856, 6.65 / 78 = 0.0853.
FULL_USD_PER_PAGE = 0.065
UPDATE_USD_PER_PAGE = 0.09


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
    approval and the cycle line does not apply to it."""
    if approved_usd is None:
        return cap_usd, monthly_usd
    return approved_usd, math.inf


def check_projected(
    limits: limits_mod.Limits, *, projected: float, spent: float, stage: str, batch: bool = False
) -> None:
    """``limits.check_before_paid``; a hold carries the projection it tested."""
    try:
        if batch:
            limits.check_before_paid(projected_usd=projected, spent_usd=spent, stage=stage, batch=True)
        else:  # the call each stage always made, unchanged
            limits.check_before_paid(projected_usd=projected, spent_usd=spent, stage=stage)
    except limits_mod.LimitHold as hold:
        hold.price_cents = cents(projected)
        raise


#: The first paid check when the extract's page record cannot be read: the
#: price cannot be evaluated, and "cannot evaluate" is not "permitted".
UNPRICED_REASON = (
    f"{limits_mod.CAP_SETTING}: the matter's page record could not be read before {{stage}}, so the "
    "job's cost could not be checked; the package was not started"
)


def extract_readable(extracted: Path) -> bool:
    """The extract stage's record holds at least one row with a page count."""
    try:
        lines = extracted.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("pages"), int) and not isinstance(row["pages"], bool):
            return True
    return False


def unit_remaining_usd(d: Any, unit: str, extracted: Path) -> float:
    """What remains of the unit's whole price after its own spend."""
    pages = update_charged.question_pages(d.slug_dir, unit, extracted, d.job.update)
    price = whole_price_usd(pages, update=d.job.update is not None)
    ledger = d.slug_dir / "runs" / unit / job_mod.USAGE_LEDGER
    return remaining_usd(price, unit_spent_usd(d.pricing, ledger))


def hold_cents(d: Any, hold: limits_mod.LimitHold, unit: str, extracted: Path) -> int | None:
    """The figure a cost hold carries: the one its check tested, or for a stop
    where spend reached a line, what remains of the unit's price."""
    if hold.price_cents is not None or hold.allowance is not None:
        return hold.price_cents
    if hold.setting not in (limits_mod.CAP_SETTING, limits_mod.BUDGET_SETTING):
        return None
    return cents(unit_remaining_usd(d, unit, extracted))


def check_first_paid(d: Any, unit: str, stage: str, extracted: Path, *, projected: float, spent: float) -> None:
    """The driver's check before a unit's first paid stage: the page question,
    then the two cost limits against the unit's remaining whole price (or the
    stage's projection, if larger). A hold carries its figures beside the
    reason: an allowance hold its page counts, a cost hold the figure it tested."""
    pages = update_charged.question_pages(d.slug_dir, unit, extracted, d.job.update)
    if d._approved_usd is None and not extract_readable(extracted):
        raise limits_mod.LimitHold(limits_mod.CAP_SETTING, UNPRICED_REASON.format(stage=stage))
    remaining = None if d._approved_usd is not None else unit_remaining_usd(d, unit, extracted)
    tested = projected if remaining is None else max(projected, remaining)
    try:
        d.limits.check_before_first_paid(
            pages=pages,
            projected_usd=tested,
            spent_usd=spent,
            stage=stage,
            approved_pages=d._approved_pages,
        )
    except limits_mod.LimitHold as hold:
        if hold.allowance is not None:
            usd = projection.read_usd(extracted, d.limits.usd_per_scanned_page, d.budget)
            hold.allowance["projected_cents"] = cents(usd)
        else:
            hold.price_cents = cents(tested)
        raise
