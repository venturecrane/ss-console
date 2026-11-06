"""A drafting job's ledger row carries what the job really cost.

The 2026-11-06 answer key: a runner killed with -15 during a release wrote no
verdict, and the lane recorded the row failed at 0 cents while the job's usage
ledgers showed $2.22 and $7.07. The firm's allowance and SMD's cost read low.
Each test fails if the rule it defends is removed from ``drafting_lane.py``.
"""

from __future__ import annotations

import json

import pytest
from jobs import budget, resume
from jobs_testkit import PRICING
from test_drafting_lane import FakeClient, _lane, _submit

# One million Opus input tokens at 500 cents per million: $5.00.
ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "draft"}

KILLED = """
import json, os, pathlib, signal, sys
jd = pathlib.Path(sys.argv[1])
(jd / 'data').mkdir(exist_ok=True)
(jd / 'data' / 'usage-ledger.jsonl').write_text(json.dumps(%s) + '\\n')
os.kill(os.getpid(), signal.SIGTERM)
""" % repr(ROW)

CRASHED = KILLED.replace("os.kill(os.getpid(), signal.SIGTERM)", "sys.exit(1)")

FAILED_AT_ZERO = KILLED.replace(
    "os.kill(os.getpid(), signal.SIGTERM)",
    'print(json.dumps([{"unit": "drafting", "outcome": "failed", "stage": "gate", "reason": "x", "dollars": 0}]))',
)

FAILED_ABOVE_LEDGER = KILLED.replace(
    "os.kill(os.getpid(), signal.SIGTERM)",
    'print(json.dumps([{"unit": "drafting", "outcome": "failed", "stage": "gate", "reason": "x", "dollars": 12.5}]))',
)


@pytest.fixture(autouse=True)
def _pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(p))
    monkeypatch.setattr(resume, "STOP_FLAG_GRACE_SECONDS", 0.2)


def _last(client: FakeClient) -> tuple[str, dict]:
    _jid, state, fields = client.records[-1]
    return state, fields


def test_a_runner_killed_without_a_verdict_records_its_ledger_spend(tmp_path):
    """MUTATION: record cents from the verdict alone and this reads 0."""
    lane, client = _lane(tmp_path, script=KILLED)
    jid = _submit(lane, client)
    assert lane.tick() == "failed"
    state, fields = _last(client)
    assert state == "failed" and fields["reason"].startswith("no_verdict: ")
    assert fields["cents"] == 500
    assert "Spend cents: 500." in lane._daemon_state(jid)["wake"]["task"]


def test_a_crash_without_a_verdict_records_its_ledger_spend(tmp_path):
    lane, client = _lane(tmp_path, script=CRASHED)
    _submit(lane, client)
    assert lane.tick() == "failed"
    assert _last(client)[1]["cents"] == 500


def test_a_failed_verdict_reporting_zero_records_the_ledger_spend(tmp_path):
    """MUTATION: apply the ledger only when there is no verdict and this reads 0."""
    lane, client = _lane(tmp_path, script=FAILED_AT_ZERO)
    _submit(lane, client)
    assert lane.tick() == "failed"
    assert _last(client)[1]["cents"] == 500


def test_a_verdict_above_the_ledger_keeps_its_own_figure(tmp_path):
    """MUTATION: replace the verdict's figure with the ledger's and this reads 500."""
    lane, client = _lane(tmp_path, script=FAILED_ABOVE_LEDGER)
    _submit(lane, client)
    assert lane.tick() == "failed"
    assert _last(client)[1]["cents"] == 1250


def test_an_unreadable_price_table_keeps_the_reported_figure(tmp_path, monkeypatch):
    monkeypatch.setenv(budget.PRICING_ENV, str(tmp_path / "absent.json"))
    lane, client = _lane(tmp_path, script=FAILED_ABOVE_LEDGER)
    _submit(lane, client)
    assert lane.tick() == "failed"
    assert _last(client)[1]["cents"] == 1250


def test_config_missing_on_a_job_that_already_spent_records_that_spend(tmp_path):
    """A resumed job whose inputs went missing keeps the spend of its earlier
    attempt. MUTATION: record 0 there and this reads 0."""
    lane, client = _lane(tmp_path, inputs=False)
    jid = _submit(lane, client)
    assert lane.tick() == "deferred"
    data = lane.job_dir(jid) / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "usage-ledger.jsonl").write_text(json.dumps(ROW) + "\n")
    lane._write_state(jid, retry_after=0)
    assert lane.tick() == "failed"
    state, fields = _last(client)
    assert state == "failed" and fields["reason"].startswith("config_missing: ")
    assert fields["cents"] == 500
    assert "Spend cents: 500." in lane._daemon_state(jid)["wake"]["task"]
