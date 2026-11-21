"""SMD's decision on a chronology held on its whole-job price (2026-11-20).

A job priced past ``per_job_cap_usd`` or the cycle's ``monthly_budget_usd``
holds before its first paid stage (``test_whole_job_price.py``). SMD is paged
with the price as integer cents and the two commands; ``approve --usd`` resumes
it through the broker with that ceiling (written to the same
``allowance-approval.json``), and ``decline`` ends it failed. The firm hears
nothing either way (a held chronology sends nothing). Invented values only.

Pins: the page carries the price and an approve command that names --usd, and
no dollar text; approve requires the dollars and sends them to the broker's
resume; the approval file reads them back; a refused resume takes them out;
decline ends the job failed and pages SMD.
"""

from __future__ import annotations

import pytest

import test_proof_daemon as pd
from jobs.medchron import allowance
from test_allowance_decide import JOB, _alerts, _held

CAP_PRICE_HOLD = (
    "per_job_cap_usd: this run's projected cost would exceed the job's cost cap before vision; "
    "the package was not started"
)
CYCLE_PRICE_HOLD = (
    "monthly_budget_usd: this run's projected cost added to the month's spend to date would exceed the "
    "monthly cost budget; the package was not started before vision"
)


def _price_held(tmp_path, reason: str = CAP_PRICE_HOLD):
    unit = pd._unit(outcome="held", reason=reason, stage="vision", dollars=0.0, price_cents=40310)
    return _held(tmp_path, unit)


@pytest.mark.parametrize("reason", [CAP_PRICE_HOLD, CYCLE_PRICE_HOLD])
def test_a_price_hold_pages_smd_with_the_price_and_the_usd_command(tmp_path, reason):
    d, broker, state = _price_held(tmp_path, reason)
    assert state == "held"
    (a,) = _alerts(broker)
    assert a["code"] == "limit_held"
    assert "price_cents 40,310" in a["detail"]
    assert f"python -m jobs.medchron.allowance approve {JOB} --by NAME --usd" in a["detail"]
    assert f"python -m jobs.medchron.allowance decline {JOB} --by NAME" in a["detail"]
    assert "$" not in a["detail"] and "USD" not in a["detail"] and len(a["detail"]) <= 300
    # The ledger's reason stays the dollar-free hold sentence; no wake.
    assert broker.records[-1][2]["reason"] == reason
    assert "wake" not in d._daemon_state(JOB)


def test_approve_on_a_price_hold_needs_the_dollars(tmp_path):
    d, _broker, _ = _price_held(tmp_path)
    with pytest.raises(allowance.Refused, match="--usd"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None)


def test_approve_usd_resumes_through_the_broker_with_the_ceiling(tmp_path):
    d, broker, _ = _price_held(tmp_path)
    allowance.apply_approve(d, allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=200.0))
    (req,) = broker.resumes
    assert req["action"] == "medchron_job_resume" and req["redo"] == []
    assert req["allowance_approved_usd"] == 200.0 and req["allowance_approved_by"] == "Scott"
    assert "allowance_approved_pages" not in req
    assert allowance.approved_usd(d.job_dir(JOB)) == 200.0
    assert allowance.approved_pages(d.job_dir(JOB)) == 0  # dollars never widen the page check
    assert d._daemon_state(JOB)["allowance_decision"]["decision"] == "approved"


def test_approve_usd_refuses_a_non_positive_figure(tmp_path):
    d, _broker, _ = _price_held(tmp_path)
    for bad in (0.0, -5.0):
        with pytest.raises(allowance.Refused, match="--usd"):
            allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=bad)


def test_a_refused_resume_takes_the_dollar_approval_back_out(tmp_path):
    d, broker, _ = _price_held(tmp_path)
    broker.refuse_resume = True
    with pytest.raises(allowance.Refused, match="taken back out"):
        allowance.apply_approve(d, allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=200.0))
    assert allowance.approved_usd(d.job_dir(JOB)) is None


def test_decline_on_a_price_hold_ends_the_job_failed(tmp_path):
    d, broker, _ = _price_held(tmp_path)
    allowance.apply_decline(d, allowance.plan_decline(d, JOB, by="Scott"))
    job, state, fields = broker.records[-1]
    assert (job, state) == (JOB, "failed")
    assert fields["pages"] == 0  # the held unit read nothing it is charged for
    assert "$" not in fields["reason"] and "USD" not in fields["reason"]
    st = d._daemon_state(JOB)
    assert st["state"] == "failed" and "wake" not in st
    assert _alerts(broker)[-1]["code"] == "limit_held"


def test_the_page_prints_the_ceiling_to_pass_and_a_lower_approval_is_refused(tmp_path):
    """The job had spent 90.00 when it held on an invented 403.10 figure: the page prints
    spent_cents and the whole-dollar ceiling to pass (494); an approval at or
    below what is already spent is refused with the minimum named."""
    unit = pd._unit(outcome="held", reason=CAP_PRICE_HOLD, stage="vision", dollars=90.0, price_cents=40310)
    d, broker, _ = _held(tmp_path, unit)
    (a,) = _alerts(broker)
    assert "spent_cents 9,000" in a["detail"] and f"approve {JOB} --by NAME --usd 494 " in a["detail"]
    for low in (90.0, 50.0):
        with pytest.raises(allowance.Refused, match=r"at least 90\.01"):
            allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=low)
    assert (
        allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=90.01)["resume"]["allowance_approved_usd"] == 90.01
    )
    assert broker.resumes == []


def test_decline_on_a_cap_stop_mid_run_fails_the_job_and_keeps_its_pages(tmp_path):
    reason = "per_job_cap_usd: the run's spend reached the job's cost cap during map; the run stopped"
    d, broker, _ = _held(tmp_path, pd._unit(outcome="held", reason=reason, stage="map", dollars=90.0, price_cents=500))
    allowance.apply_decline(d, allowance.plan_decline(d, JOB, by="Scott"))
    job, state, fields = broker.records[-1]
    assert (job, state) == (JOB, "failed") and fields["reason"].startswith("per_job_cap_usd: declined by SMD")
    assert fields["pages"] == 3  # it spent and read: its pages are debited


def test_a_page_allowance_decline_records_its_pages_as_before(tmp_path):
    """C: a held allowance unit is dropped, any other unit's pages kept; every
    outcome now carries a ``price_cents`` key (None), which must not drop it."""
    from test_allowance_decide import FIG, HOLD

    d, broker, _ = _held(
        tmp_path,
        pd._unit(unit="beta", outcome="held", reason=HOLD, stage="vision", allowance=FIG, pages=4210, price_cents=None),
        pd._unit(
            unit="alpha", outcome="held", reason="audit coverage: a gap", stage="audit", pages=40, price_cents=None
        ),
    )
    assert broker.records[-1][2]["reason"] == HOLD
    allowance.apply_decline(d, allowance.plan_decline(d, JOB, by="Scott"))
    assert broker.records[-1][2]["pages"] == 40


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_a_non_finite_ceiling_is_refused(tmp_path, bad):
    d, _broker, _ = _price_held(tmp_path)
    with pytest.raises(allowance.Refused, match="finite"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=bad)


def test_a_page_allowance_hold_refuses_dollars(tmp_path):
    from test_allowance_decide import FIG, HOLD

    d, _broker, _ = _held(tmp_path, pd._unit(outcome="held", reason=HOLD, stage="vision", allowance=FIG))
    with pytest.raises(allowance.Refused, match="--usd does not apply"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None, usd=50.0)


def test_both_commands_survive_the_cut_for_the_longest_ids():
    """D: the alert is cut at 300 characters; the commands come first."""
    job, matter = "Z" * 26, "M" * 60
    for fig in (
        {"spent_cents": 9_999_999, "price_cents": 99_999_999, "approve_usd": 1_099_999},
        {"spent_cents": 9_999_999, "price_cents": None, "approve_usd": None},
    ):
        cut = allowance.cost_alert_detail(job, matter, "monthly_budget_usd", fig)[:300]
        assert f"approve {job} --by NAME --usd " in cut and f"decline {job} --by NAME" in cut
