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
import signal
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

import test_casefile_lane as casefile_t
import test_demand_lane as demand_t
import test_drafting_lane as drafting_t
import test_litigation_lane as litigation_t
import test_negotiation_lane as negotiation_t
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
# The lanes whose runner writes ONE verdict object (negotiation, litigation).
if mode == 'caught_dict':
    print(json.dumps({"verdict": "failed", "stage": "x", "reason": "unexpected: interrupted"}))
    sys.exit(1)
if mode == 'held_dict':
    print(json.dumps({"verdict": "held", "stage": "review", "reason": "review_held: one finding", "cents": 10}))
    sys.exit(1)
if mode == 'paged_dict':
    print(json.dumps({"verdict": "failed", "stage": "review", "reason": "review_held: 1 finding; nothing was filed", "cents": 10}))
    sys.exit(2)
if mode == 'delivered_dict':
    print(json.dumps({"verdict": "delivered", "stage": "report", "cents": 10, "notices": [],
                      "proof": {"readback": "confirmed", "rows": 0, "review": "code_only", "review_cents": 0, "nothing_to_file": True}}))
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


def _negotiation(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    """A scheduled negotiation run (2026-10-15: one cut short by a deploy was
    recorded failed and paged SMD for a lost wake)."""
    lane, client = negotiation_t._lane(tmp_path, [])
    lane._stop_test_dict = {"caught": "caught_dict", "real_end": ("delivered_dict", "delivered")}
    return lane, client, lambda: negotiation_t.JOB, "negotiation-watch"


def _litigation(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    lane, client = litigation_t._lane(tmp_path)
    lane._stop_test_dict = {"caught": "caught_dict", "real_end": ("held_dict", "held")}
    return lane, client, lambda: litigation_t._submit(lane, client), "litigation-status"


def _casefile(tmp_path: Path) -> tuple[Any, Any, Callable[[], str], str]:
    lane, client = casefile_t._lane(tmp_path)
    lane._stop_test_dict = {"caught": "caught_dict", "real_end": ("held_dict", "held")}
    return lane, client, lambda: casefile_t._submit(lane, client), "case-file-manager"


LANES = pytest.mark.parametrize(
    "make",
    [_demand, _drafting, _chronology, _negotiation, _litigation, _casefile],
    ids=["demand", "drafting", "chronology", "negotiation", "litigation", "casefile"],
)


def _mode(lane: Any, mode: str) -> str:
    """The child mode in this lane's verdict shape."""
    return (getattr(lane, "_stop_test_dict", None) or {}).get(mode, mode)


def _real_end(lane: Any) -> tuple[str, str]:
    return (getattr(lane, "_stop_test_dict", None) or {}).get("real_end", ("held", "held"))


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
    _with_child(lane, tmp_path, _mode(lane, "caught"))
    lane.stopping = lambda: True
    assert lane.tick() == "interrupted"
    assert _states(client) == ["running"]


@LANES
def test_a_real_ending_reached_before_the_stop_is_recorded(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    mode, ending = _real_end(lane)
    _with_child(lane, tmp_path, mode)
    lane.stopping = lambda: True
    assert lane.tick() == ending
    assert _states(client)[-1] == ending


@pytest.mark.parametrize("make", [_negotiation, _litigation, _casefile], ids=["negotiation", "litigation", "casefile"])
def test_a_paged_failure_reached_before_the_stop_is_recorded_not_rerun(tmp_path, make):
    """FALSIFIER (review of #3256): a ``failed`` carrying review_held: is the
    work's own ending and pages SMD; re-running it on the next boot would spend
    again to reach the same hold, and page late."""
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with_child(lane, tmp_path, "paged_dict")
    lane.stopping = lambda: True
    assert lane.tick() == "failed"
    assert _states(client)[-1] == "failed"


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
    if lane.LANE == "medchron":
        # Stop audit row 20 (2026-10-24): on the chronology lane a runner
        # killed by a signal is an infrastructure ending, retried after a
        # delay a bounded number of times (requeue.py), not terminal at once.
        assert lane.tick() == "requeued"
        assert asked["n"] == 1  # SIGKILL is not a stop signal: no grace wait
        assert lane._daemon_state(jid)["state"] == "running" and lane._daemon_state(jid)["infra_requeues"] == 1
        assert "failed" not in _states(client)
        return
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
    if lane.LANE == "medchron":  # retried, not failed (row 20; see above)
        assert lane.tick() == "requeued" and "failed" not in _states(client)
        return
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


# ---- the stop is final for the daemon process (2026-10-15) --------------------------
def _main_with(monkeypatch, run_forever) -> int:
    """``daemon.main`` with the seat parts stubbed: no broker socket, no lanes."""
    monkeypatch.setattr(
        daemon_mod,
        "build",
        lambda: daemon_mod.Daemon(run_dir=Path("/nonexistent"), broker=None, runner_cmd=[], customer_slug="example"),
    )
    monkeypatch.setattr(daemon_mod, "memory_cap_mode", lambda _root: "v2")
    import medchron.demand_lane as dl
    import medchron.lanes as lanes

    monkeypatch.setattr(dl, "start_lane", lambda *a, **k: None)
    monkeypatch.setattr(lanes, "start_other_lanes", lambda *a, **k: None)
    monkeypatch.setattr(daemon_mod.Daemon, "run_forever", run_forever)
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        return daemon_mod.main([])
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def test_main_exits_stopped_when_the_machine_stop_ended_the_loop(monkeypatch):
    """FALSIFIER: the entrypoint respawns the daemon on any exit but this one, and
    a daemon respawned in a stopping Machine has no stop flag (2026-10-15: it
    spent a DELIVER wake on a gateway already gone, and SMD was paged)."""

    def run_forever(self, stop, poll_seconds):
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop()

    assert _main_with(monkeypatch, run_forever) == daemon_mod.STOPPED_EXIT == 130


def test_main_exits_zero_when_the_loop_ends_without_a_stop(monkeypatch):
    assert _main_with(monkeypatch, lambda self, stop, poll_seconds: None) == 0
