"""SMD's per-job cost cap on a drafting job (``cap_cents``, 2026-11-24).

SMD needed a $35 cap on one A&P drafting job and could enforce it only by
watching and stopping it. The broker now carries an optional ``cap_cents`` in
the queue file; the runner holds the job to it through the same spend gate as
the firm's ``per_job_cap_usd`` (the estimate before the first paid stage, and
every paid call), and refuses before anything is paid when SMD's cap is above
the firm's, because a per-job cap only lowers the firm's. Absent, nothing
changes. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs import limits as limits_mod
from jobs.drafting import job as job_mod
from jobs.drafting.run import CAP_OVER_FIRM, JOB_CAP_LABEL, DraftingRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def _run(tmp_path: Path, cap_cents: int | None, client: ScriptedClient, seat=None) -> DraftingRun:
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    make_inputs(inputs)  # the firm's per_job_cap_usd is 100.0
    jd = make_job(tmp_path / "job")
    if cap_cents is not None:
        doc = json.loads((jd / "job.json").read_text())
        (jd / "job.json").write_text(json.dumps({**doc, "cap_cents": cap_cents}))
    return DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat if seat is not None else seat_with(standard_docs()),
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
    )


def test_smds_cap_is_the_cap_the_spend_gate_holds_every_paid_call_to(tmp_path):
    r = _run(tmp_path, 3500, ScriptedClient())
    assert r.limits.cap_usd == 35.0 and r.budget.cap_usd == 35.0
    r.doorway.before_request("compose")  # the doorway's hook before every paid call: nothing spent, it goes ahead
    r.budget.refresh = lambda: 34.99  # type: ignore[method-assign]
    r.doorway.before_request("compose")
    r.budget.refresh = lambda: 35.0  # type: ignore[method-assign]
    with pytest.raises(limits_mod.LimitHold) as held:
        r.doorway.before_request("compose")
    assert held.value.setting == limits_mod.CAP_SETTING
    assert (
        held.value.reason == f"per_job_cap_usd: the run's spend reached {JOB_CAP_LABEL} during compose; the run stopped"
    )


def test_no_cap_keeps_the_firms_cap_and_its_words(tmp_path):
    r = _run(tmp_path, None, ScriptedClient())
    assert r.job.cap_cents is None and r.limits.cap_usd == 100.0
    assert r.limits.cap_label == "the job's cost cap"


def test_an_estimate_over_smds_cap_stops_the_job_before_anything_is_paid(tmp_path):
    client, seat = ScriptedClient(), seat_with(standard_docs())
    v = _run(tmp_path, 100, client, seat).run()  # $1: under the drafting estimate's fixed part alone
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd"
    assert v.reason.startswith("limit: per_job_cap_usd: ") and JOB_CAP_LABEL in v.reason
    assert client.calls == [] and seat.sent == [] and v.dollars == 0


def test_a_cap_above_the_firms_guard_is_refused_and_runs_nothing(tmp_path):
    client, seat = ScriptedClient(), seat_with(standard_docs())
    v = _run(tmp_path, 10_001, client, seat).run()  # $100.01 against the firm's $100
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd"
    assert v.reason == f"limit: {CAP_OVER_FIRM}"
    assert client.calls == [] and seat.sent == [] and seat.created == []


def test_a_cap_equal_to_the_firms_guard_is_accepted(tmp_path):
    r = _run(tmp_path, 10_000, ScriptedClient())
    assert not r.cap_over_firm and r.limits.cap_usd == 100.0


@pytest.mark.parametrize("bad", [0, -5, 35.0, "3500", True])
def test_the_runner_refuses_a_queue_file_whose_cap_is_not_whole_positive_cents(tmp_path, bad):
    jd = make_job(tmp_path / "job")
    doc = json.loads((jd / "job.json").read_text())
    with pytest.raises(job_mod.DraftingJobError, match="cap_cents"):
        job_mod.parse({**doc, "cap_cents": bad}, jd)
