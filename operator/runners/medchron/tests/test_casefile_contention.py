"""Seat contention: a client-requested demand that starts while a case file
review is running waits on nothing, and the review waits for it before its next
matter: at most the matter already in flight overlaps the demand."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

import casefile_testkit as kit
from medchron.casefile import matter as matter_mod
from medchron.casefile.run import CasefileRun


def test_a_demand_submitted_mid_review_overlaps_at_most_one_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pricing_path: Path
):
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    pid = tmp_path / "demand-child.pid"
    monkeypatch.setenv(matter_mod.YIELD_ENV, str(pid))
    events: list[str] = []
    base = kit.Scripted()

    class Model(kit.Scripted):
        def _msg(self, **params):
            text = kit.first_text(params)
            if "record_review" in {t["name"] for t in params.get("tools") or []} and "# MATTER" in text:
                number = text.split("# MATTER ", 1)[1].split(":", 1)[0]
                events.append(f"read {number}")
                if number == "100001" and not pid.exists():
                    pid.write_text(str(os.getpid()))  # a demand child starts now
                    events.append("demand started")
            return base._msg(**params)

    def sleep(_s: float) -> None:
        events.append("waited")
        if events.count("waited") == 3:
            pid.unlink()
            events.append("demand ended")

    monkeypatch.setattr(matter_mod, "SLEEP", sleep)
    model = Model()
    model.messages = type(base.messages)(stream=model._stream, create=model._msg)
    jd = kit.make_job(tmp_path / "job")
    r = CasefileRun(
        jd, inputs_dir=str(kit.make_inputs(tmp_path / "inputs")), pricing=str(pricing_path),
        state_dir=tmp_path / "state", seat_factory=kit.CfSeat, client=model, log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26), readback_pause=0,
    )  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "delivered", v
    started = events.index("demand started")
    ended = events.index("demand ended")
    reads_between = [e for e in events[started:ended] if e.startswith("read ") and e != "read 100001"]
    assert reads_between == []  # the next matter waited for the demand
    assert "read 100002" in events[ended:]
