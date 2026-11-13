"""The paid session kit (see the package docstring)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod
from ..ledger import Ledger
from ..llm import Doorway

#: The file a lane's doorway writes in the job's data folder, and the one its
#: budget prices.
LEDGER_NAME = "usage-ledger.jsonl"


def pricing_path(explicit: str | Path | None = None) -> Path:
    """The rate card: the path a caller names, else ``$JOBS_PRICING_JSON``,
    else the seat's card. Read at call time, so a test that points either
    elsewhere is honoured."""
    return Path(explicit or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)


def load_pricing(explicit: str | Path | None = None) -> budget_mod.Pricing:
    return budget_mod.Pricing.load(pricing_path(explicit))


def budget(
    pricing: budget_mod.Pricing, cap_usd: float, ledgers: list[Path], usd_per_million_chars: float = 1.0
) -> budget_mod.Budget:
    """The job's live spend over ``ledgers`` against ``cap_usd``. The
    characters rate prices a projection from text; a lane that never projects
    from text keeps 1.0."""
    return budget_mod.Budget(pricing, cap_usd, ledgers, usd_per_million_chars)


@dataclass(frozen=True)
class Caps:
    """The figures a job's limits hold it to, as the lane computed them. The
    values are passed through unchanged (an int cap stays an int), so the
    limits a lane builds here are the limits it built by hand."""

    cap_usd: float
    monthly_budget_usd: float
    usd_per_scanned_page: float = 0.0
    usd_per_audit_claim: float = 0.0
    month_cents_used: int | None = None
    allowance_remaining_pages: int | None = None
    allowance_month: str | None = None
    allowance_cycle_label: str | None = None

    def limits(self) -> limits_mod.Limits:
        return limits_mod.Limits(
            cap_usd=self.cap_usd,
            monthly_budget_usd=self.monthly_budget_usd,
            usd_per_scanned_page=self.usd_per_scanned_page,
            usd_per_audit_claim=self.usd_per_audit_claim,
            month_cents_used=self.month_cents_used,
            allowance_remaining_pages=self.allowance_remaining_pages,
            allowance_month=self.allowance_month,
            allowance_cycle_label=self.allowance_cycle_label,
        )


@dataclass
class PaidSession:
    """What a lane's constructor keeps: it assigns each part to the attribute
    it always used (``self.pricing``, ``self.budget``, ``self.limits``,
    ``self.doorway``, and records' ``self.ledger``)."""

    pricing: budget_mod.Pricing
    ledger: Ledger
    budget: budget_mod.Budget
    limits: limits_mod.Limits
    doorway: Doorway


def open_session(
    data: Path,
    caps: Caps,
    *,
    pricing: str | Path | None = None,
    usd_per_million_chars: float = 1.0,
    client: Any = None,
    log: Callable[[str], None] = print,
    before_request: Callable[[str], None] | None = None,
    before_batch: Callable[[str, int, int], None] | None = None,
) -> PaidSession:
    """The rate card, the ledger in ``data``, the budget over that ledger, the
    limits and the doorway, in that order (the card is read first, so a
    missing card refuses the job before anything else is built).

    ``before_request`` and ``before_batch`` are the lane's own checks: the
    doorway calls the first before every interactive call and the second
    before a batch is submitted, before any money moves."""
    card = load_pricing(pricing)
    ledger = Ledger(data / LEDGER_NAME)
    spend = budget(card, caps.cap_usd, [ledger.path], usd_per_million_chars)
    limits = caps.limits()
    doorway = Doorway(ledger=ledger, client=client, log=log, before_request=before_request, before_batch=before_batch)
    return PaidSession(pricing=card, ledger=ledger, budget=spend, limits=limits, doorway=doorway)


__all__ = ["LEDGER_NAME", "Caps", "PaidSession", "budget", "load_pricing", "open_session", "pricing_path"]
