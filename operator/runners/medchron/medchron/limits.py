"""The four routine-11 limits, and the one grammar their holds speak.

Four settings, checked at two moments:

* ``single_matter_page_threshold`` and the seat's monthly page allowance are
  read ONCE, before the first paid stage, from the pages the extract stage
  actually found. Nothing is spent to learn them, so a matter that is too big
  costs nothing to refuse.
* ``monthly_budget_usd`` and ``per_job_cap_usd`` are re-checked before EVERY
  paid model call, through the doorway's ``before_request`` hook. Between-stage
  checking was the old shape and it let a job run far past its cap inside one
  long stage; per-call bounds the overshoot to one call.

The grammar (2026-09-09, ADR 0087 amendment): every hold reason starts with the
NAME OF THE SETTING that held it and carries no dollar figure -- not the cap's,
not the budget's, not the projection's. Two reasons:

1. The seat's content gates refuse an agent-drafted dollar amount on sight, so
   a reason carrying one cannot be relayed to the requester (proven live
   2026-08-31: a held-job report was refused four times and never landed).
2. ss-console is a PUBLIC repo and these strings are fixtures in it.

Page counts are fine: they are the metered unit and the firm authored the
allowance. The figures themselves live where a person can read them: the run's
state file, ``log-<stage>.txt``, and the job's console row.
"""
from __future__ import annotations

from dataclasses import dataclass

THRESHOLD_SETTING = "single_matter_page_threshold"
ALLOWANCE_SETTING = "chronology_package_page_allowance_per_month"
BUDGET_SETTING = "monthly_budget_usd"
CAP_SETTING = "per_job_cap_usd"


class LimitHold(Exception):
    """One limit held the run. ``setting`` is the key a person changes to lift
    it; ``reason`` is the whole sentence, which already begins with it."""

    def __init__(self, setting: str, reason: str) -> None:
        self.setting = setting
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Limits:
    """One job's copy of the firm's controls plus the month's state as the
    broker read it. A laptop run leaves the month fields None and is metered by
    the threshold and the cap alone; a seat run without them is a refusal (the
    driver's fail-closed check), because unmetered is not a state a client seat
    is allowed to be in."""

    cap_usd: float
    monthly_budget_usd: float
    single_matter_page_threshold: int
    usd_per_scanned_page: float
    usd_per_audit_claim: float
    month_cents_used: int | None = None
    allowance_remaining_pages: int | None = None
    allowance_month: str | None = None

    @property
    def month_spent_usd(self) -> float:
        return (self.month_cents_used or 0) / 100.0

    @property
    def month_label(self) -> str:
        return self.allowance_month or "this month"

    # ---- once, before the first paid stage ---------------------------------
    def check_before_first_paid(self, *, pages: int, projected_usd: float, spent_usd: float,
                                stage: str) -> None:
        """Threshold, then allowance, then the two cost limits. Order matters:
        the two page checks cost nothing and answer the bigger question (should
        this matter be built at all), so they go first."""
        if pages > self.single_matter_page_threshold:
            raise LimitHold(
                THRESHOLD_SETTING,
                f"{THRESHOLD_SETTING}: the matter's file is {pages:,} pages, above the firm's "
                "single-matter page threshold; the package was not started",
            )
        remaining = self.allowance_remaining_pages
        if remaining is not None and pages > remaining:
            raise LimitHold(
                ALLOWANCE_SETTING,
                f"{ALLOWANCE_SETTING}: the matter's file is {pages:,} pages and {remaining:,} pages "
                f"remain in {self.month_label}'s allowance; the package was not started",
            )
        self.check_before_paid(projected_usd=projected_usd, spent_usd=spent_usd, stage=stage)

    # ---- before every paid stage -------------------------------------------
    def check_before_paid(self, *, projected_usd: float, spent_usd: float, stage: str) -> None:
        """The month's budget and the job's cap against what this stage is
        projected to add. A projection of zero still catches a run that has
        already reached either line."""
        month = self.month_spent_usd + spent_usd
        if month >= self.monthly_budget_usd or month + projected_usd > self.monthly_budget_usd:
            raise LimitHold(
                BUDGET_SETTING,
                f"{BUDGET_SETTING}: this run's projected cost added to the month's spend to date "
                f"would exceed the monthly cost budget; the package was not started before {stage}",
            )
        if spent_usd >= self.cap_usd or spent_usd + projected_usd > self.cap_usd:
            raise LimitHold(
                CAP_SETTING,
                f"{CAP_SETTING}: the run's projected cost would exceed the job's cost cap before "
                f"{stage}; the package was not started",
            )

    # ---- before every paid call ---------------------------------------------
    def check_each_call(self, spent_usd: float, stage: str) -> None:
        """The doorway hook. No projection here: the question is only whether
        the money already spent reached a line, so the next call does not."""
        if self.month_spent_usd + spent_usd >= self.monthly_budget_usd:
            raise LimitHold(
                BUDGET_SETTING,
                f"{BUDGET_SETTING}: the month's spend reached the monthly cost budget during "
                f"{stage}; the run stopped",
            )
        if spent_usd >= self.cap_usd:
            raise LimitHold(
                CAP_SETTING,
                f"{CAP_SETTING}: the run's spend reached the job's cost cap during {stage}; "
                "the run stopped",
            )
