"""Every lane records what a job really spent, by one rule (``jobs/spend.py``).

The 2026-11-06 answer key, drafting only until now: a runner killed with -15
during a release wrote no verdict, and the lane recorded the row at 0 cents
while the job's usage ledgers showed $2.22 and $7.07. Six of the seven paid
lanes still did (the 2026-11-10 lanes review, finding M1). Each case below runs
a lane's REAL class with a child that writes one priced usage row ($5.00) and
then ends without a verdict, and reads the cents the lane sent its broker.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jobs import budget, resume
from jobs_testkit import PRICING

# One million Opus input tokens at 500 cents per million: $5.00.
ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "draft"}

#: Where each lane's runner writes the usage its budget counts (``spend.ledgers``):
#: the chronology's per-unit ledger, every other lane's ``data/`` ledger.
LEDGER_REL = {"medchron": "data/example/runs/alpha/usage-ledger.jsonl"}
WRITE_ROW = """
import json, os, pathlib, signal, sys
ledger = pathlib.Path(sys.argv[1]) / %r
ledger.parent.mkdir(parents=True, exist_ok=True)
ledger.write_text(json.dumps(%s) + '\\n')
"""
CRASHED = "sys.exit(1)\n"
KILLED = "os.kill(os.getpid(), signal.SIGTERM)\n"


@pytest.fixture(autouse=True)
def _pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(p))
    monkeypatch.setattr(resume, "STOP_FLAG_GRACE_SECONDS", 0.2)


def _script(lane: Any, text: str) -> None:
    """The lane's child, rewritten: every lane test kit runs ``[python, path]``."""
    Path(lane.runner_cmd[-1]).write_text(text)


def _tuple_cents(client: Any) -> int | None:
    return client.records[-1][2].get("cents")


def _demand(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_demand_lane as t

    lane, client = t._lane(tmp_path)
    t._submit(lane, client)
    return lane, lambda: _tuple_cents(client)


def _drafting(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_drafting_lane as t

    lane, client = t._lane(tmp_path)
    t._submit(lane, client)
    return lane, lambda: _tuple_cents(client)


def _litigation(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_litigation_lane as t

    lane, client = t._lane(tmp_path)
    t._submit(lane, client)
    return lane, lambda: _tuple_cents(client)


def _casefile(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_casefile_lane as t

    lane, client = t._lane(tmp_path)
    t._submit(lane, client)
    return lane, lambda: _tuple_cents(client)


def _negotiation(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_negotiation_lane as t

    lane, client = t._lane(tmp_path, [])
    return lane, lambda: _tuple_cents(client)


def _records(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_records_lane as t

    seat = inspect.unwrap(t.seat)(tmp_path, monkeypatch)
    lane, client, _ = t._lane(tmp_path, seat, [])
    t._submit(lane, client)
    return lane, lambda: client.records[-1].get("cents")


def _medchron(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Callable[[], int | None]]:
    import test_upload_daemon as t

    d, broker = t._daemon(tmp_path)
    t._submit(d, broker, "01A")
    return d, lambda: broker.records[-1][2].get("cents")


LANES = {
    "demand": _demand,
    "drafting": _drafting,
    "litigation": _litigation,
    "casefile": _casefile,
    "negotiation": _negotiation,
    "records": _records,
    "medchron": _medchron,
}


@pytest.mark.parametrize("lane_name", sorted(LANES))
@pytest.mark.parametrize("ending", ["crashed", "killed"])
def test_a_job_that_ends_without_a_verdict_records_its_priced_usage(tmp_path, monkeypatch, lane_name, ending):
    """MUTATION: record the verdict's figure alone and every lane reads 0 here."""
    lane, cents = LANES[lane_name](tmp_path, monkeypatch)
    rel = LEDGER_REL.get(lane_name, "data/usage-ledger.jsonl")
    _script(lane, WRITE_ROW % (rel, repr(ROW)) + (CRASHED if ending == "crashed" else KILLED))
    lane.tick()
    assert cents() == 500


@pytest.mark.parametrize("lane_name", sorted(LANES))
def test_a_job_without_usage_or_verdict_still_records_nothing_spent(tmp_path, monkeypatch, lane_name):
    """The rule never invents spend: no usage rows, no verdict, 0 (or no figure)."""
    lane, cents = LANES[lane_name](tmp_path, monkeypatch)
    _script(lane, "import sys\nsys.exit(1)\n")
    lane.tick()
    assert not cents()


def test_the_rule_takes_the_larger_figure_and_keeps_the_reported_one_without_prices(tmp_path, monkeypatch):
    from jobs import spend

    jd = tmp_path / "job"
    (jd / "data").mkdir(parents=True)
    (jd / "data" / "usage-ledger.jsonl").write_text(json.dumps(ROW) + "\n")
    assert spend.recorded_cents(jd, "drafting", 0) == 500
    assert spend.recorded_cents(jd, "drafting", 1250) == 1250
    monkeypatch.setenv(budget.PRICING_ENV, str(tmp_path / "absent.json"))
    assert spend.recorded_cents(jd, "drafting", 1250) == 1250
    assert spend.recorded_cents(jd, "drafting", 0) == 0
