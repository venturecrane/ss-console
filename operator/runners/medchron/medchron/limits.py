"""The three routine-11 limits, and the one grammar their holds speak.

Three settings, checked at two moments:

* The seat's cycle page allowance is read ONCE, before the first paid stage,
  from the pages the extract stage actually found. Nothing is spent to learn
  it, so a matter that does not fit costs nothing to refuse.
* ``monthly_budget_usd`` and ``per_job_cap_usd`` are re-checked before every
  paid call in LIVE mode, through the doorway's ``before_request`` hook, and
  before every BATCH submission with the batch's projected cost, through
  ``before_batch``. So an overshoot is bounded to one call or one batch.
  Between-stage checking was the old shape and it let a job run far past its
  cap inside one long stage. Batch mode needs the other hook because a batch is
  one commitment: nothing checks between its items and the whole thing is
  billed, so the only place a limit can bind is before the submission.

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
    def check_before_first_paid(self, *, pages: int, projected_usd: float, spent_usd: float, stage: str) -> None:
        """Allowance, then the two cost limits. The page check costs nothing and
        answers the bigger question (does this matter fit what the firm bought),
        so it goes first.

        There is deliberately NO per-matter page gate here (removed 2026-09-10,
        Captain). The firm buys a CYCLE allowance; how it spends it is its own
        business, so one matter that consumes the whole cycle is a legitimate
        use of it and not a refusal. A per-matter page line was the per-job cost
        cap wearing client-facing clothes: both were sized off the same measured
        rate, so it refused matters the cycle allowance could plainly afford --
        matter 200454 measured 3,098+ pages against a 3,000-page line while the
        firm's full 15,000-page allowance sat unused. Margin is not exposed by
        its removal: ``monthly_budget_usd`` binds in DOLLARS, independently, on
        every paid stage and every paid call, and dollars are what actually
        track cost (a scanned page measured ~4x a text page)."""
        remaining = self.allowance_remaining_pages
        if remaining is not None and pages > remaining:
            raise LimitHold(
                ALLOWANCE_SETTING,
                f"{ALLOWANCE_SETTING}: the matter's file is {pages:,} pages and {remaining:,} pages "
                f"remain in {self.month_label}'s allowance; the package was not started",
            )
        self.check_before_paid(projected_usd=projected_usd, spent_usd=spent_usd, stage=stage)

    # ---- before every paid stage -------------------------------------------
    def check_before_paid(self, *, projected_usd: float, spent_usd: float, stage: str, batch: bool = False) -> None:
        """The month's budget and the job's cap against what this stage -- or,
        with `batch`, this one batch submission -- is projected to add. A
        projection of zero still catches a run that has already reached either
        line."""
        what = "this batch's projected cost" if batch else "this run's projected cost"
        tail = "the batch was not submitted" if batch else "the package was not started"
        # "at" reads right for one batch inside a stage; "before" for the stage.
        when = "at" if batch else "before"
        month = self.month_spent_usd + spent_usd
        if month >= self.monthly_budget_usd or month + projected_usd > self.monthly_budget_usd:
            raise LimitHold(
                BUDGET_SETTING,
                f"{BUDGET_SETTING}: {what} added to the month's spend to date would exceed the "
                f"monthly cost budget; {tail} {when} {stage}",
            )
        if spent_usd >= self.cap_usd or spent_usd + projected_usd > self.cap_usd:
            raise LimitHold(
                CAP_SETTING,
                f"{CAP_SETTING}: {what} would exceed the job's cost cap {when} {stage}; {tail}",
            )

    # ---- before every paid call ---------------------------------------------
    def check_each_call(self, spent_usd: float, stage: str) -> None:
        """The doorway hook. No projection here: the question is only whether
        the money already spent reached a line, so the next call does not."""
        if self.month_spent_usd + spent_usd >= self.monthly_budget_usd:
            raise LimitHold(
                BUDGET_SETTING,
                f"{BUDGET_SETTING}: the month's spend reached the monthly cost budget during {stage}; the run stopped",
            )
        if spent_usd >= self.cap_usd:
            raise LimitHold(
                CAP_SETTING,
                f"{CAP_SETTING}: the run's spend reached the job's cost cap during {stage}; the run stopped",
            )
