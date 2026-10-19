"""A Machine stop drains running jobs instead of killing them (fleet job-safe restarts).

Before this, every job child shared the daemon's process group, and tini ``-g``
signals the entrypoint's whole group: a deploy killed a chronology mid-stage,
and the next boot repeated the stage. Now each child runs in its own session,
the daemon asks it to checkpoint (``.stop-request``), a chronology or drafting
child exits 76 at its next safe point and stays claimed, and one that never
answers is terminated at the drain deadline. While a job runs, its lane
heartbeat stays fresh; while a quiesce file is live, nothing new starts.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import test_decisions_driver as driver_t
from medchron import child as child_mod, daemon as daemon_mod, quiesce, stop_request
from medchron.state import RunState, state_path
from medchron_testkit import seed_folders
from test_stop_cut_short import LANES, _chronology, _demand, _drafting, _states

#: The test child: one script, its behaviour read from a file beside it.
CHILD = """
import json, os, pathlib, signal, sys, time
here = pathlib.Path(__file__).parent
mode = (here / 'child-mode').read_text().strip()
jd = pathlib.Path(sys.argv[1])
(here / 'session.json').write_text(json.dumps({"pid": os.getpid(), "sid": os.getsid(0), "pgid": os.getpgid(0)}))
if mode == 'checkpoint':
    end = time.time() + 30
    while time.time() < end:
        if (jd / '.stop-request').exists():
            sys.exit(76)
        time.sleep(0.05)
    sys.exit(9)
if mode == 'sleep':
    time.sleep(60)
if mode == 'deaf':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
if mode == 'slow':
    time.sleep(0.6)
    sys.exit(76)
"""


def _with(lane: Any, tmp_path: Path, mode: str) -> list[str]:
    script = tmp_path / "drain-child" / "child.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(CHILD)
    (script.parent / "child-mode").write_text(mode)
    own = lane.runner_cmd
    lane.runner_cmd = [os.environ.get("PYTHON", sys.executable), str(script)]
    return own


def _session(tmp_path: Path) -> dict[str, int]:
    return json.loads((tmp_path / "drain-child" / "session.json").read_text())


def _quiesce(lane: Any, until: float | str) -> None:
    body = until if isinstance(until, str) else json.dumps({"until": until, "by": "test", "reason": "rollout"})
    (lane.run_dir / quiesce.NAME).write_text(body)


def _job_id(lane: Any) -> str:
    return next(d.name for d in lane.jobs.iterdir() if (d / "daemon.json").is_file())


# ---- the drain -------------------------------------------------------------------------
@LANES
def test_a_stop_asks_the_child_to_checkpoint_and_the_checkpointed_job_stays_claimed(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    own = _with(lane, tmp_path, "checkpoint")
    lane.stopping = lambda: True
    t0 = time.monotonic()
    assert lane.tick() == "interrupted"
    assert time.monotonic() - t0 < 10  # the child answered the request; no deadline was reached
    jid = _job_id(lane)
    assert (lane.job_dir(jid) / stop_request.NAME).is_file()
    assert _states(client) == ["running"]  # nothing terminal reached the ledger
    st = lane._daemon_state(jid)
    assert st.get("claimed") and st.get("state") not in daemon_mod.TERMINAL and not st.get("wake")
    # Its own session: the Machine's group signal (tini -g) can no longer reach it.
    s = _session(tmp_path)
    assert s["sid"] == s["pid"] == s["pgid"] and s["sid"] != os.getsid(0)
    # The next boot: the request that cut it is cleared before the attempt runs.
    lane.runner_cmd = own
    lane.stopping = lambda: False
    assert lane.tick() == "delivered"
    assert not (lane.job_dir(jid) / stop_request.NAME).exists()


@LANES
def test_a_child_that_ignores_the_request_is_terminated_at_the_drain_deadline(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _with(lane, tmp_path, "sleep")
    lane.drain_seconds = 0.5
    lane.stopping = lambda: True
    t0 = time.monotonic()
    assert lane.tick() == "interrupted"
    assert time.monotonic() - t0 < 10
    assert _states(client) == ["running"]


def test_a_child_deaf_to_sigterm_is_killed_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(child_mod, "TERM_GRACE_SECONDS", 0.3)
    d, broker, submit, _skill = _chronology(tmp_path)
    submit()
    _with(d, tmp_path, "deaf")
    d.drain_seconds = 0.2
    d.stopping = lambda: True
    t0 = time.monotonic()
    assert d.tick() == "interrupted"  # SIGKILLed past the deadline, during a stop: still the stop's
    assert time.monotonic() - t0 < 10
    assert _states(broker) == ["running"]


def test_without_a_stop_the_child_is_never_asked_or_terminated(tmp_path):
    d, broker, submit, _skill = _chronology(tmp_path)
    submit()
    _with(d, tmp_path, "slow")
    d.drain_seconds = 0.0
    asked = {"n": 0}

    def stopping() -> bool:
        asked["n"] += 1
        return False

    d.stopping = stopping
    d.tick()
    assert not (d.job_dir("01A") / stop_request.NAME).exists()


# ---- the beat ------------------------------------------------------------------------
@pytest.mark.parametrize("make", [_chronology, _demand, _drafting], ids=["chronology", "demand", "drafting"])
def test_the_heartbeat_is_rewritten_while_a_long_child_runs(tmp_path, make, monkeypatch):
    """FALSIFIER: these lanes wrote their heartbeat only at tick start, so a
    chronology running for hours read as a stale lane to the boot smoke and to
    the fleet's busy gate."""
    lane, _client, submit, _skill = make(tmp_path)
    submit()
    _with(lane, tmp_path, "slow")
    lane.heartbeat_seconds = 0.05
    beats: list[Any] = []
    real = lane.heartbeat
    monkeypatch.setattr(lane, "heartbeat", lambda running=None: (beats.append(running), real(running=running)))
    lane.tick()
    jid = _job_id(lane)
    assert beats.count(jid) >= 4  # the tick's own beat plus the thread's during the child


# ---- quiesce -------------------------------------------------------------------------
@LANES
def test_a_live_quiesce_claims_nothing_and_the_heartbeat_says_so(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _quiesce(lane, lane.clock() + 600)
    assert lane.tick() is None
    assert _states(client) == []
    hb = json.loads((lane.run_dir / f"{lane.HEARTBEAT}.json").read_text())
    assert hb["quiesced"] is True and hb["running"] is None


@LANES
def test_an_expired_quiesce_is_ignored(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    submit()
    _quiesce(lane, lane.clock() - 1)
    assert lane.tick() is not None
    assert "running" in _states(client)
    hb = json.loads((lane.run_dir / f"{lane.HEARTBEAT}.json").read_text())
    assert hb["quiesced"] is False


@pytest.mark.parametrize("body", ["not json", '{"by": "x"}', '{"until": "soon"}'])
def test_an_unparseable_quiesce_is_ignored_and_logged_once(tmp_path, body, caplog):
    d, broker, submit, _skill = _chronology(tmp_path)
    submit()
    _quiesce(d, body)
    for _ in range(3):
        assert quiesce.active(d) is False
    assert sum("quiesce file" in r.getMessage() for r in caplog.records) == 1
    assert d.tick() is not None and "running" in _states(broker)


def test_a_quiesce_also_holds_the_resume_of_a_claimed_job(tmp_path):
    d, broker, submit, _skill = _chronology(tmp_path)
    submit()
    own = _with(d, tmp_path, "checkpoint")
    d.stopping = lambda: True
    assert d.tick() == "interrupted"
    d.runner_cmd, d.stopping = own, (lambda: False)
    _quiesce(d, d.clock() + 600)
    assert d.tick() is None and _states(broker) == ["running"]
    (d.run_dir / quiesce.NAME).unlink()
    assert d.tick() == "delivered"


# ---- the join ------------------------------------------------------------------------
def test_join_lanes_waits_for_a_draining_lane_and_names_one_past_the_deadline(monkeypatch):
    monkeypatch.setattr(child_mod, "TERM_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(child_mod, "JOIN_SLACK_SECONDS", 0.0)
    release = threading.Event()
    quick = threading.Thread(target=lambda: time.sleep(0.2), name="quick-lane", daemon=True)
    stuck = threading.Thread(target=release.wait, name="stuck-lane", daemon=True)
    quick.start()
    stuck.start()
    t0 = time.monotonic()
    assert child_mod.join_lanes([quick, stuck], since=t0, budget=0.6) == ["stuck-lane"]
    assert not quick.is_alive()
    assert 0.5 < time.monotonic() - t0 < 5
    release.set()


def test_main_waits_for_a_lane_still_draining_when_the_loop_ends(monkeypatch):
    """FALSIFIER: lane threads are daemon threads; without the join the process
    returned 130 while a lane was mid-checkpoint, and its exit killed the lane."""
    import medchron.demand_lane as dl
    import medchron.lanes as lanes

    done: list[bool] = []

    def lane_thread(_d, *, stop, poll_seconds):
        def run() -> None:
            while not stop():
                time.sleep(0.01)
            time.sleep(0.4)  # the lane's child checkpointing
            done.append(True)

        t = threading.Thread(target=run, name="slow-lane", daemon=True)
        t.start()
        return t

    def run_forever(self, stop, poll_seconds):
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop()

    monkeypatch.setattr(
        daemon_mod,
        "build",
        lambda: daemon_mod.Daemon(run_dir=Path("/nonexistent"), broker=None, runner_cmd=[], customer_slug="example"),
    )
    monkeypatch.setattr(daemon_mod, "memory_cap_mode", lambda _root: "v2")
    monkeypatch.setattr(dl, "start_lane", lane_thread)
    monkeypatch.setattr(lanes, "start_other_lanes", lambda *a, **k: [])
    monkeypatch.setattr(daemon_mod.Daemon, "run_forever", run_forever)
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert daemon_mod.main([]) == 130
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)
    assert done == [True]


# ---- the child's side: checkpoints -------------------------------------------------
def test_the_driver_stops_between_stages_and_the_resume_repeats_nothing(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    stop_request.arm(job_dir)
    try:
        d = driver_t._driver(job_dir, firm_config_path, pricing_path)
        from medchron import dag

        real = dag.BY_NAME["render"].runner

        def render_then_stop(sr):
            code = real(sr)
            (job_dir / stop_request.NAME).write_text("1")  # the daemon's request lands mid-stage
            return code

        d._runner_override = {"render": render_then_stop}
        with pytest.raises(stop_request.StopRequested):
            d.run()
        st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")
        assert st.is_done("render")  # the stage in flight finished and was recorded
        assert st.outcome is None  # no ending: the next attempt resumes
        (job_dir / stop_request.NAME).unlink()
        driver_t._driver(job_dir, firm_config_path, pricing_path).run()
        st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")
        assert st.outcome == "delivered"
        assert st.stage("render").attempts == 1 and st.stage("download").attempts == 1
    finally:
        stop_request.arm(None)


def test_the_cli_exits_76_on_a_stop_and_writes_no_verdict(tmp_path, monkeypatch):
    import medchron.__main__ as cli

    jd = tmp_path / "job"
    jd.mkdir()

    def fake_run(args):
        assert stop_request.requested() is False
        (jd / stop_request.NAME).write_text("1")
        stop_request.check("render")
        raise AssertionError("not reached")

    monkeypatch.setattr(cli, "_cmd_run", fake_run)
    try:
        assert cli.main(["run", str(jd), "--json"]) == stop_request.STOP_EXIT == 76
    finally:
        stop_request.arm(None)
    assert not (jd / "verdict.json").exists()


def test_a_dry_run_is_never_armed(tmp_path, monkeypatch):
    import medchron.__main__ as cli

    jd = tmp_path / "job"
    jd.mkdir()
    (jd / stop_request.NAME).write_text("1")
    seen = {}
    monkeypatch.setattr(cli, "_cmd_run", lambda args: seen.setdefault("armed", stop_request.requested()) and 0)
    cli.main(["run", str(jd), "--dry-run"])
    assert seen["armed"] is False


def test_the_drafting_run_checks_before_each_stage(tmp_path):
    from medchron.drafting.run import DraftingRun

    ran: list[str] = []

    class Fake:
        log = staticmethod(lambda _m: None)

        def _is_done(self, _n):
            return False

        def _done(self, n, **_kw):
            ran.append(n)

    stop_request.arm(tmp_path)
    try:
        DraftingRun._stage(Fake(), "facts", lambda: None)
        (tmp_path / stop_request.NAME).write_text("1")
        with pytest.raises(stop_request.StopRequested):
            DraftingRun._stage(Fake(), "compose", lambda: ran.append("compose-body"))
    finally:
        stop_request.arm(None)
    assert ran == ["facts"]


def test_a_batch_poll_honours_the_request_and_keeps_the_batch(tmp_path, monkeypatch):
    from medchron.ledger import Ledger
    from medchron.llm import Doorway

    class Batches:
        def retrieve(self, _bid):
            return type("S", (), {"processing_status": "in_progress"})()

    client = type("C", (), {"messages": type("M", (), {"batches": Batches()})()})()
    door = Doorway(ledger=Ledger(tmp_path / "l.jsonl"), client=client, log=lambda _m: None, poll_s=30.0)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: (slept.append(s), (tmp_path / stop_request.NAME).write_text("1")))
    stop_request.arm(tmp_path)
    try:
        with pytest.raises(stop_request.StopRequested):
            door._poll(client, ["batch-1"])
    finally:
        stop_request.arm(None)
    assert slept == [1.0]  # woke within a second of the request, not after the 30 s interval


def test_an_unarmed_process_never_stops(tmp_path, monkeypatch):
    (tmp_path / stop_request.NAME).write_text("1")
    stop_request.arm(None)
    stop_request.check("x")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    stop_request.sleep(5)


def test_exit_76_collides_with_no_other_runner_exit():
    """76 is reserved for the checkpoint: no runner module returns or exits it,
    and it is none of the CLI's verdict codes, the refused-envelope 2, the
    gateway's service restart 75, or the daemon's stopped 130."""
    import re

    import medchron.__main__ as cli

    assert stop_request.STOP_EXIT not in {0, 1, 2, 3, 4, 75, 130}
    assert cli._exit_code([]) != stop_request.STOP_EXIT
    pkg = Path(stop_request.__file__).parent
    pat = re.compile(r"(?:exit|return)\s*\(?\s*76\b")
    hits = [str(f.relative_to(pkg)) for f in pkg.rglob("*.py") if pat.search(f.read_text(encoding="utf-8"))]
    assert hits == []
