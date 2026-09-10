"""The four routine-11 limits: boundaries, order, and the one grammar.

Each boundary is checked on BOTH sides -- the value that proceeds and the value
that holds -- because a limit tested only where it fires cannot tell an
off-by-one from a correct bound, and a limit tested only where it passes cannot
fail at all.
"""
from __future__ import annotations

import re

import pytest

from medchron import limits as limits_mod
from medchron.limits import LimitHold, Limits


def _limits(**kw) -> Limits:
    base = dict(cap_usd=150.0, monthly_budget_usd=800.0,
                usd_per_scanned_page=0.02, usd_per_audit_claim=0.05)
    base.update(kw)
    return Limits(**base)


def _hold(fn, **kw) -> LimitHold:
    with pytest.raises(LimitHold) as exc:
        fn(**kw)
    return exc.value


# ---- the one page limit -------------------------------------------------------
def test_one_matter_may_consume_the_whole_cycle_allowance() -> None:
    """The firm buys a CYCLE allowance and spends it as it likes: a single
    matter that eats all of it is a legitimate use, not a refusal.

    The falsifier: reintroduce ANY per-matter page ceiling below 15,000 and the
    first call raises instead of returning. Matter 200454 measured 3,098+ pages
    against the 3,000-page line this replaces, with the firm's full allowance
    unused -- that refusal is what this test now forbids.
    """
    lim = _limits(allowance_remaining_pages=15_000)
    lim.check_before_first_paid(pages=15_000, projected_usd=1.0, spent_usd=0.0, stage="vision")
    lim.check_before_first_paid(pages=3_098, projected_usd=1.0, spent_usd=0.0, stage="vision")
    # One page past what the cycle affords is still a hold, and it names the
    # allowance -- the only page gate left.
    assert _hold(lim.check_before_first_paid, pages=15_001, projected_usd=1.0, spent_usd=0.0,
                 stage="vision").setting == "chronology_package_page_allowance_per_month"


def test_no_page_gate_fires_when_the_cycle_state_is_absent() -> None:
    """A laptop run carries no cycle state. With the per-matter line gone there
    is no page ceiling at all off-seat; cost is what bounds it. A seat run
    without cycle state is refused earlier, by the driver's fail-closed check.
    """
    _limits().check_before_first_paid(pages=1_000_000, projected_usd=1.0, spent_usd=0.0,
                                      stage="vision")


def test_the_allowance_proceeds_at_the_remainder_and_holds_one_page_over() -> None:
    lim = _limits(allowance_remaining_pages=40, allowance_month="2026-09")
    lim.check_before_first_paid(pages=40, projected_usd=1.0, spent_usd=0.0, stage="vision")
    hold = _hold(lim.check_before_first_paid, pages=41, projected_usd=1.0, spent_usd=0.0, stage="vision")
    assert hold.setting == "chronology_package_page_allowance_per_month"
    assert "41 pages" in hold.reason and "40 pages remain in 2026-09's allowance" in hold.reason


def test_an_unknown_allowance_is_not_a_zero_allowance() -> None:
    """A laptop run carries no month state. None must read as "not metered
    here", never as "nothing remains"; coercing it to 0 would refuse every
    laptop run."""
    _limits(allowance_remaining_pages=None).check_before_first_paid(
        pages=2_999, projected_usd=1.0, spent_usd=0.0, stage="vision")


# ---- the two cost limits, before a stage --------------------------------------
def test_the_budget_counts_the_month_the_run_and_the_projection_together() -> None:
    lim = _limits(monthly_budget_usd=10.0, month_cents_used=600)   # 6.00 already this month
    lim.check_before_paid(projected_usd=3.0, spent_usd=1.0, stage="vision")          # 6+1+3 = 10, at the line
    hold = _hold(lim.check_before_paid, projected_usd=3.01, spent_usd=1.0, stage="vision")
    assert hold.setting == "monthly_budget_usd"
    assert "was not started before vision" in hold.reason


def test_the_cap_holds_on_the_projection_and_on_spend_already_past_it() -> None:
    lim = _limits(cap_usd=5.0)
    lim.check_before_paid(projected_usd=5.0, spent_usd=0.0, stage="vision")
    assert _hold(lim.check_before_paid, projected_usd=5.01, spent_usd=0.0,
                 stage="vision").setting == "per_job_cap_usd"
    assert _hold(lim.check_before_paid, projected_usd=0.0, spent_usd=5.0,
                 stage="audit").setting == "per_job_cap_usd"


# ---- the two cost limits, per paid call ---------------------------------------
def test_each_call_holds_at_the_cap_and_at_the_budget() -> None:
    assert _hold(_limits(cap_usd=2.0).check_each_call, spent_usd=2.0, stage="vision").setting == "per_job_cap_usd"
    lim = _limits(monthly_budget_usd=2.0, month_cents_used=150)     # 1.50 this month
    lim.check_each_call(0.49, "vision")
    hold = _hold(lim.check_each_call, spent_usd=0.50, stage="vision")
    assert hold.setting == "monthly_budget_usd" and "during vision; the run stopped" in hold.reason


def test_a_month_with_no_recorded_spend_does_not_hold_a_run() -> None:
    _limits(monthly_budget_usd=1.0, month_cents_used=None).check_each_call(0.5, "vision")


# ---- the grammar --------------------------------------------------------------
FIGURE = re.compile(r"\$|USD|\d+\.\d\d")


def _every_reason() -> list[LimitHold]:
    """One of every hold that can reach a ledger row, a console note, or the
    Operator's reply."""
    out = [
        _hold(_limits(allowance_remaining_pages=1, allowance_month="2026-09").check_before_first_paid,
              pages=9_999, projected_usd=0.0, spent_usd=0.0, stage="vision"),
        _hold(_limits(monthly_budget_usd=1.0).check_before_paid,
              projected_usd=99.99, spent_usd=0.0, stage="vision"),
        _hold(_limits(cap_usd=1.0).check_before_paid, projected_usd=99.99, spent_usd=0.0, stage="audit"),
        _hold(_limits(monthly_budget_usd=1.0, month_cents_used=100).check_each_call,
              spent_usd=12.34, stage="vision"),
        _hold(_limits(cap_usd=1.0).check_each_call, spent_usd=12.34, stage="audit"),
        # The batch-mode variants: same two settings, different tail clause.
        _hold(_limits(monthly_budget_usd=1.0).check_before_paid,
              projected_usd=99.99, spent_usd=0.0, stage="vision", batch=True),
        _hold(_limits(cap_usd=1.0).check_before_paid,
              projected_usd=99.99, spent_usd=0.0, stage="vision", batch=True),
    ]
    assert len(out) == 7
    return out


def test_reasons_carry_no_dollar_figure() -> None:
    """The seat's content gates refuse an agent-drafted dollar amount, so a
    reason carrying one cannot be relayed to the requester at all; and these
    strings are fixtures in a PUBLIC repo. Page counts are the exception: they
    are the metered unit and the firm authored the allowance."""
    for hold in _every_reason():
        assert not FIGURE.search(hold.reason), f"{hold.setting}: reason carries a figure: {hold.reason}"


def test_every_reason_starts_with_the_setting_that_held_it() -> None:
    for hold in _every_reason():
        assert hold.reason.startswith(f"{hold.setting}: "), hold.reason


def test_the_settings_named_are_the_three_the_firm_and_the_seat_author() -> None:
    assert {h.setting for h in _every_reason()} == {
        limits_mod.ALLOWANCE_SETTING, limits_mod.BUDGET_SETTING, limits_mod.CAP_SETTING,
    }
    # No per-matter page ceiling exists to hold a run (removed 2026-09-10).
    assert not hasattr(limits_mod, "THRESHOLD_SETTING")
    # The allowance setting is the seat key by name: a reply that named the
    # runner's field instead would point a firm at a key it cannot edit.
    assert limits_mod.ALLOWANCE_SETTING == "chronology_package_page_allowance_per_month"


# ---- batch mode ---------------------------------------------------------------
def test_a_batch_holds_on_its_own_projected_cost_and_says_the_batch_was_not_submitted() -> None:
    """A batch is one commitment: nothing checks between its items and the whole
    thing is billed, so the projection has to be checked before submission. The
    reason says the batch was not submitted, not that the package was not
    started, because earlier stages may well have run."""
    lim = _limits(cap_usd=5.0)
    lim.check_before_paid(projected_usd=5.0, spent_usd=0.0, stage="vision", batch=True)
    hold = _hold(lim.check_before_paid, projected_usd=5.01, spent_usd=0.0, stage="vision", batch=True)
    assert hold.setting == "per_job_cap_usd"
    assert "this batch's projected cost" in hold.reason
    assert "the batch was not submitted" in hold.reason
    assert "the package was not started" not in hold.reason


def test_the_batch_variant_also_answers_to_the_monthly_budget() -> None:
    lim = _limits(monthly_budget_usd=10.0, month_cents_used=900)     # 9.00 already
    lim.check_before_paid(projected_usd=1.0, spent_usd=0.0, stage="compose", batch=True)
    hold = _hold(lim.check_before_paid, projected_usd=1.01, spent_usd=0.0, stage="compose", batch=True)
    assert hold.setting == "monthly_budget_usd" and "the batch was not submitted at compose" in hold.reason
