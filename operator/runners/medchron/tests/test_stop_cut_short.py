"""A Machine stop is not a job failure, on every lane.

A deploy stops the Machine; the stop signal reaches the child and the daemon
together and the child dies without a verdict. Recorded as ``failed``, that
was final: the requester held a reply saying the work was underway and nothing
would ever follow it short of a root resume. Each lane must leave such a job
claimed so the next boot resumes it, while a real failure (an OOM kill, a
signal with the daemon still running, a verdict reached before the stop) is
recorded exactly as before.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

import test_demand_lane as demand_t
import test_drafting_lane as drafting_t
import test_upload_daemon as chron_t
from medchron import daemon as daemon_mod, resume as resume_mod

# The child under test: what it does is read from a file beside it, so one
# script serves every lane and every scenario.
CHILD = """
import json, os, pathlib, signal, sys
mode = (pathlib.Path(__file__).parent / 'child-mode').read_text().strip()
if mode == 'term':
    os.kill(os.getpid(), signal.SIGTERM)
if mode == 'kill':
    os.kill(os.getpid(), signal.SIGKILL)
if mode == 'caught':
    # A child that traps the stop and reports it as its own failure.
    print(json.dumps([{"unit": "u", "outcome": "failed", "stage": "x", "reason": "unexpected: interrupted", "dollars": 0}]))
    sys.exit(1)
if mode == 'held':
    print(json.dumps([{"unit": "u", "outcome": "held", "stage": "render", "reason": "format_check: held", "dollars": 1.0}]))
    sys.exit(0)
"""


def _demand(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    lane, client = demand_t._lane(tmp_path)
    return lane, client, lambda: demand_t._submit(lane, client), "demand-letter-drafter"


def _drafting(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    lane, client = drafting_t._lane(tmp_path)
    return lane, client, lambda: drafting_t._submit(lane, client), "document-drafter"


def _chronology(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    d, broker = chron_t._daemon(tmp_path)

    def submit() -> str:
        chron_t._submit(d, broker, "01A")
        return "01A"

    return d, broker, submit, "medchron"


LANES = pytest.mark.parametrize("make", [_demand, _drafting, _chronology], ids=["demand", "drafting", "chronology"])


def _with_child(lane: Any, tmp_path: Path, mode: str) -> list[str]:
    """Point the lane at the test child for one attempt; returns the lane's own
    runner command so the next boot can run the real (fake) runner."""
    script = tmp_path / "stop-child" / "child.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(CHILD)
    (script.parent / "child-mode").write_text(mode)
    own = lane.runner_cmd
    lane.runner_cmd = [os.environ.get("PYTHON", sys.executable), str(script)]
    return own


def _states(client: Any) -> list[str]:
    return [s for _j, s, _f in client.records]


@LANES
def test_a_child_killed_by_the_machine_stop_is_left_claimed_and_resumed_on_the_next_boot(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    own = _with_child(lane, tmp_path, "term")
    lane.stopping = lambda: True
    assert lane.tick() == "interrupted"
    assert _states(client) == ["running"]  # nothing terminal reached the ledger
    st = lane._daemon_state(jid)
    assert st.get("claimed") and st.get("state") not in daemon_mod.TERMINAL
    assert not st.get("wake") and not st.get("finished_at")  # no DELIVER turn, no wipe clock
    # The next boot: same job dir, the daemon running, the real runner.
    lane.runner_cmd = own
    lane.stopping = lambda: False
    assert lane.tick() == "delivered"
    assert _states(client) == ["running", "running", "delivered"]
    assert lane._daemon_state(jid)["attempts"] == 2


@LANES
def test_the_stop_flag_arriving_just_after_the_child_died_still_counts(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with_child(lane, tmp_path, "term")
    calls = {"n": 0}

    def stopping() -> bool:  # the main thread's handler runs a beat after the child dies
        calls["n"] += 1
        return calls["n"] > 3

    lane.stopping = stopping
    assert lane.tick() == "interrupted"
    assert _states(client) == ["running"]


@LANES
def test_a_child_that_reports_the_stop_as_its_own_failure_is_still_cut_short(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with_child(lane, tmp_path, "caught")
    lane.stopping = lambda: True
    assert lane.tick() == "interrupted"
    assert _states(client) == ["running"]


@LANES
def test_a_real_ending_reached_before_the_stop_is_recorded(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with_child(lane, tmp_path, "held")
    lane.stopping = lambda: True
    assert lane.tick() == "held"
    assert _states(client)[-1] == "held"


@LANES
def test_an_oom_kill_with_the_daemon_running_is_a_failure_recorded_at_once(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    _with_child(lane, tmp_path, "kill")
    asked = {"n": 0}

    def stopping() -> bool:
        asked["n"] += 1
        return False

    lane.stopping = stopping
    assert lane.tick() == "failed"
    assert _states(client)[-1] == "failed"
    assert asked["n"] == 1  # SIGKILL is not a stop signal: no grace wait
    assert lane._daemon_state(jid)["state"] == "failed"


@LANES
def test_a_stop_signal_with_no_stop_after_the_grace_is_a_failure(tmp_path, make, monkeypatch):
    monkeypatch.setattr(resume_mod, "STOP_FLAG_GRACE_SECONDS", 0.3)
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with_child(lane, tmp_path, "term")
    lane.stopping = lambda: False
    assert lane.tick() == "failed"
    assert _states(client)[-1] == "failed"


def test_run_forever_binds_the_loop_stop_flag_the_lanes_read(tmp_path):
    d, _broker, _submit, _skill = _chronology(tmp_path)
    assert d.stopping() is False  # unbound: never stopping
    flag = {"stop": True}
    d.run_forever(stop=lambda: flag["stop"], poll_seconds=0)
    assert d.stopping() is True
    flag["stop"] = False
    assert d.stopping() is False
