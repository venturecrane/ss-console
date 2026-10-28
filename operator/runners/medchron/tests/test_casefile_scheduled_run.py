"""The runner's half of the Case File Manager's standing schedule: a weekday
``recheck`` reads only what changed, the month's scheduled ``full`` reads every
file whole (a requested job keeps the economies), and the month's billing
count (open versus Pending, by Smokeball status) is taken once, from the matter
list only, as the first step of the month's first scheduled job, standing
whatever the read does. The lane's wake quotes it as one line. Each test fails
if the line it defends is removed. Everything here is synthetic."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from pathlib import Path

import pytest

import casefile_testkit as kit
from medchron.casefile import billing
from medchron.casefile.run import CasefileRun

SCHEDULED_REF = "scheduled:2026-10-26:0123456789ab"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pricing_path: Path) -> dict:
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    return {
        "inputs": kit.make_inputs(tmp_path / "inputs"),
        "state": tmp_path / "state",
        "pricing": str(pricing_path),
        "tmp": tmp_path,
    }


def _run(env: dict, seat: kit.CfSeat, model: kit.Scripted, job: str, **over) -> tuple[CasefileRun, dict]:
    jd = kit.make_job(env["tmp"] / f"job-{job[-4:]}", job_id=job, **over)
    r = CasefileRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=env["pricing"],
        state_dir=env["state"],
        seat_factory=lambda: seat,
        client=model,
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26),
        readback_pause=0,
    )
    return r, json.loads(r.run().to_json())


def _scheduled(mode: str) -> dict:
    return {"trigger": "scheduled", "mode": mode, "message_ref": SCHEDULED_REF, "request_text": f"scheduled ({mode})"}


def test_a_recheck_runs_and_pays_nothing_on_an_unchanged_caseload(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted(), kit.JOB)
    model = kit.Scripted()
    _r, v = _run(env, seat, model, "01KTJ0BX0000000000000000D1", **_scheduled("recheck"))
    assert v["verdict"] == "delivered", v
    assert (v["matters_current"], v["matters_read"]) == (2, 0)
    assert model.calls == []


def test_the_scheduled_full_read_reads_every_file_whole(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted(), kit.JOB)
    model = kit.Scripted()
    _r, v = _run(env, seat, model, "01KTJ0BX0000000000000000D2", **_scheduled("full"))
    assert v["verdict"] == "delivered", v
    assert (v["matters_current"], v["matters_read"]) == (0, 2)
    assert model.calls  # paid: the monthly ground truth


def test_a_requested_full_keeps_the_economies(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted(), kit.JOB)
    _r, v = _run(env, seat, kit.Scripted(), "01KTJ0BX0000000000000000D3")
    assert (v["matters_current"], v["matters_read"]) == (2, 0)


def test_the_months_first_scheduled_job_takes_the_billing_count_from_the_matter_list(env: dict) -> None:
    seat = kit.CfSeat()
    _r, v = _run(env, seat, kit.Scripted(), "01KTJ0BX0000000000000000D4", **_scheduled("full"))
    assert v["metrics"]["billing_count"] == {"Open": 1, "Pending": 1}
    doc = json.loads(next((env["state"] / "billing").glob("*/2026-10.json")).read_text())
    assert doc["matters"] == {"Open": [kit.M1], "Pending": [kit.M2]}
    assert doc["job_id"] == "01KTJ0BX0000000000000000D4" and doc["taken_on"] == "2026-10-26"


def test_the_count_is_taken_once_a_month_and_a_recheck_does_not_quote_it(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted(), "01KTJ0BX0000000000000000D5", **_scheduled("full"))
    seat.matters[1]["status"] = "Open"  # a file moved after the 1st
    _r, v = _run(env, seat, kit.Scripted(), "01KTJ0BX0000000000000000D6", **_scheduled("recheck"))
    assert "billing_count" not in v["metrics"]
    doc = json.loads(next((env["state"] / "billing").glob("*/2026-10.json")).read_text())
    assert doc["counts"] == {"Open": 1, "Pending": 1} and doc["job_id"].endswith("D5")


def test_the_count_stands_when_the_read_that_follows_does_not(env: dict) -> None:
    seat = kit.CfSeat()
    _r, v = _run(
        env, seat, kit.Scripted(), "01KTJ0BX0000000000000000D7",
        file_to_matter_id="", file_to_matter_number="NO-SUCH", **_scheduled("full"),
    )  # fmt: skip
    assert v["verdict"] == "held" and v["reason"].startswith("library_unresolved"), v
    assert v["metrics"]["billing_count"] == {"Open": 1, "Pending": 1}


def test_a_requested_job_takes_no_count(env: dict) -> None:
    seat = kit.CfSeat()
    _r, v = _run(env, seat, kit.Scripted(), kit.JOB)
    assert "billing_count" not in v["metrics"]
    assert not (env["state"] / "billing").exists()


def test_the_caseload_key_is_the_brokers() -> None:
    """workspace_broker/casefile_schedule.caseload_key, restated: the broker
    is not on the runner's venv, so the rule is pinned here and there."""
    from medchron.casefile.job import Scope

    want = hashlib.sha256(b"pat example").hexdigest()[:12]
    assert billing.caseload_key(Scope(names=("Pat  Example",))) == want


def test_the_lane_wake_quotes_the_count_as_one_line(tmp_path: Path) -> None:
    from medchron.casefile_lane import CasefileLane

    fields = {"metrics": {"billing_count": {"Open": 41, "Pending": 7}}, "files": []}
    lane = CasefileLane.__new__(CasefileLane)
    lane.job_dir = lambda job_id: tmp_path  # type: ignore[method-assign]
    (tmp_path / "envelope.json").write_text(json.dumps(_scheduled("full")))
    task = lane._compose_wake(kit.JOB, "delivered", fields)
    assert "Count on the 1st: 41 Open, 7 Pending." in task.splitlines()
