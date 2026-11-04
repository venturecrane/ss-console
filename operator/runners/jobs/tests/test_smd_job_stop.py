"""SMD's job stop (``smd_stop.py``, ``python -m jobs.stop``) on every lane.

2026-11-04: a duplicate A&P drafting job spent $56.64 at about a dollar a
minute and could not be stopped. The only stop was the Machine's, read between
stages, and the drafting finish has none inside it; and that stop means "resume
me on the next boot". Each test here fails on the code before this change:

* a stop requested mid-finish lands before the next model call, and nothing
  is filed;
* a child that never reaches a checkpoint is terminated within the grace;
* the job ends ``stopped_by_smd``: ``failed`` in the ledger with the reason,
  no DELIVER wake, and the daemon never runs it again (not on the next tick,
  not on a seat restart);
* a Machine stop still leaves the job claimed for the next boot (unchanged);
* only root can ask.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import test_stop_cut_short as cut_t
from jobs import daemon as daemon_mod, llm, resume as resume_mod, smd_stop, stop, stop_request
from jobs import ledger as ledger_mod

# A child with no checkpoint at all: it runs until it is killed, writing a
# heartbeat so the test can see it is alive (the drafting finish, for the
# daemon's purposes, between two model calls).
SPIN = """
import pathlib, sys, time
here = pathlib.Path.cwd()
(here / 'child-ran').write_text('yes')
while True:
    time.sleep(0.05)
"""


@pytest.fixture(autouse=True)
def _disarm():
    yield
    stop_request.arm(None)


def _spin(lane: Any, tmp_path: Path) -> None:
    script = tmp_path / "spin" / "child.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(SPIN)
    lane.runner_cmd = [os.environ.get("PYTHON", sys.executable), str(script)]


def _stop_when_running(lane: Any, jid: str, *, by: str = "scott", why: str = "duplicate job") -> threading.Thread:
    """Write SMD's stop the moment the child is alive, as the CLI would."""

    def go() -> None:
        jd = lane.job_dir(jid)
        deadline = time.monotonic() + 20
        while not (jd / "child-ran").is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        smd_stop.request(jd, by=by, why=why, at=time.time())

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


@cut_t.LANES
def test_a_child_with_no_checkpoint_is_terminated_and_recorded_stopped_by_smd(tmp_path, make, monkeypatch):
    monkeypatch.setenv(smd_stop.GRACE_ENV, "0.5")
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    _spin(lane, tmp_path)
    lane.stopping = lambda: False
    t = _stop_when_running(lane, jid)
    t0 = time.monotonic()
    assert lane.tick() == "interrupted"
    t.join()
    assert time.monotonic() - t0 < 30  # terminated at the grace, not left to spin
    recs = [(s, f) for j, s, f in client.records if j == jid]
    assert recs[-1][0] == "failed"
    assert recs[-1][1]["reason"] == "stopped_by_smd: by scott: duplicate job"
    st = lane._daemon_state(jid)
    assert st["state"] == smd_stop.STATE == "stopped_by_smd" and smd_stop.STATE in daemon_mod.TERMINAL
    assert st["stopped_by"] == "scott" and st["stopped_why"] == "duplicate job"
    assert not st.get("wake"), "no DELIVER turn: nothing reaches the client"
    # never again: the next tick runs nothing and records nothing
    n = len(client.records)
    (lane.job_dir(jid) / "child-ran").unlink()
    assert lane.tick() in (None, "paused")
    assert len(client.records) == n and not (lane.job_dir(jid) / "child-ran").exists()
    assert jid not in lane._in_progress()


@cut_t.LANES
def test_smd_stop_wins_over_a_machine_stop_landing_at_the_same_time(tmp_path, make, monkeypatch):
    monkeypatch.setenv(smd_stop.GRACE_ENV, "0.3")
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    _spin(lane, tmp_path)
    lane.stopping = lambda: True
    lane.drain_seconds = 60.0  # the Machine's drain alone would wait a minute
    t = _stop_when_running(lane, jid)
    assert lane.tick() == "interrupted"
    t.join()
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd"
    assert [s for j, s, _f in client.records if j == jid][-1] == "failed"


@cut_t.LANES
def test_a_seat_restart_before_the_stop_was_recorded_never_starts_the_job(tmp_path, make):
    """The stop was asked; the seat restarted before the lane recorded it. The
    job is still claimed and running in daemon.json, so the next boot would
    resume it; it must be recorded stopped without a child ever starting."""
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    cut_t._with_child(lane, tmp_path, "term")
    lane.stopping = lambda: True
    assert lane.tick() == "interrupted"  # the Machine stop: left claimed
    assert lane._daemon_state(jid).get("state") not in daemon_mod.TERMINAL
    smd_stop.request(lane.job_dir(jid), by="scott", why="stop it", at=time.time())
    _spin(lane, tmp_path)
    lane.stopping = lambda: False
    assert lane.tick() == "interrupted"
    assert not (lane.job_dir(jid) / "child-ran").exists(), "a stopped job must not start again"
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd"


def test_a_machine_stop_alone_still_leaves_the_job_claimed_for_the_next_boot(tmp_path):
    """The daemon-stop path is unchanged: no SMD marker, a resume on the next boot."""
    lane, client, submit, _skill = cut_t._drafting(tmp_path)
    jid = submit()
    cut_t._with_child(lane, tmp_path, "term")
    lane.stopping = lambda: True
    assert lane.tick() == "interrupted"
    st = lane._daemon_state(jid)
    assert st.get("claimed") and st.get("state") not in daemon_mod.TERMINAL
    assert jid in lane._in_progress()


def test_an_explicit_smd_resume_clears_the_old_stop(tmp_path):
    lane, client, submit, _skill = cut_t._drafting(tmp_path)
    jid = submit()
    jd = lane.job_dir(jid)
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "envelope.json").write_text("{}")
    smd_stop.request(jd, by="scott", why="dup", at=0)
    lane.queue.mkdir(parents=True, exist_ok=True)
    (lane.queue / f"{resume_mod.MARKER_PREFIX}{jid}.json").write_text(json.dumps({"job_id": jid, "reason": "run it"}))
    assert resume_mod.take_requests(lane) == [jid]
    assert smd_stop.read(jd) is None


# ---- the child: every paid call and the filing ---------------------------------------
def _doorway(tmp_path: Path, replies: list[str]):
    import test_llm_ledger as t

    c = t.client_for(t.mk_msg("ok"))
    return llm.Doorway(ledger_mod.Ledger(tmp_path / "usage-ledger.jsonl"), client=c, log=lambda *_: None), c


def test_the_doorway_refuses_the_next_paid_call_once_smd_stops_the_job(tmp_path):
    d, c = _doorway(tmp_path, [])
    stop_request.arm_smd(tmp_path)
    msg = [{"role": "user", "content": "x"}]
    d.call("audit", model="m", max_tokens=1, messages=msg)
    smd_stop.request(tmp_path, by="scott", why="dup", at=0)
    with pytest.raises(stop_request.SmdStopRequested):
        d.call("audit", model="m", max_tokens=1, messages=msg)
    assert len(c.messages.calls) == 1, "no model call after the stop"
    with pytest.raises(stop_request.StopRequested):  # every existing stop handler lets it through
        d.call("audit", model="m", max_tokens=1, messages=msg)


def test_the_doorway_ignores_a_machine_stop(tmp_path):
    """A Machine stop keeps its own checkpoints (stage boundaries): a seat
    restart drains a job as it did before, it is not cut mid-stage."""
    d, c = _doorway(tmp_path, [])
    stop_request.arm(tmp_path)
    (tmp_path / stop_request.NAME).write_text("1")
    d.call("audit", model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert len(c.messages.calls) == 1


def test_a_stop_mid_finish_halts_before_the_next_model_call_and_files_nothing(tmp_path, monkeypatch):
    """The incident's shape: the drafting finish, inside the per-unit source
    check, with no stage boundary between its model calls."""
    import test_drafting_evidence_lane as ev

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(ev.CHECKER))
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(ev.PRICING), encoding="utf-8")
    jd = tmp_path / "job"

    class StopsMidFinish(ev.LedgerClient):
        stopped_at: int | None = None

        def _answer(self, params: dict[str, Any]) -> str:
            system = ev._text(params.get("system") or "")
            if system.startswith("SOURCE CHECK") and self.stopped_at is None:
                smd_stop.request(jd, by="scott", why="duplicate job", at=0)
                self.stopped_at = len(self.calls)
            return super()._answer(params)

    client = StopsMidFinish()
    stop_request.arm(jd)
    with pytest.raises(stop_request.SmdStopRequested):
        ev._run(tmp_path, pricing, client)
    assert client.stopped_at is not None, "the run never reached the finish's source check"
    assert len(client.calls) == client.stopped_at, "a model call ran after SMD's stop"


def test_the_drafting_lane_files_nothing_once_smd_stops_it(tmp_path, monkeypatch):
    """A stop that lands after the last model call (the manifest is built)
    must still keep the files off the matter."""
    import test_drafting_review_first as rf
    import test_drafting_evidence_lane as ev

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(ev.CHECKER))
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(ev.PRICING), encoding="utf-8")
    r, v, _log, seat = ev._run(tmp_path, pricing, ev.LedgerClient(), evidence=ev.HELD)
    assert r._is_done("manifest") and seat.created == []
    jd = tmp_path / "job"
    (jd / "release-held.json").write_text(json.dumps({"by": "SMD", "why": "read it"}))
    smd_stop.request(jd, by="scott", why="stop", at=0)
    stop_request.arm(jd)
    with pytest.raises(stop_request.SmdStopRequested):
        rf._again(tmp_path, pricing, seat)
    assert seat.created == [] and seat.sent == []


# ---- the CLI ------------------------------------------------------------------------
def _job(tmp_path: Path, lane: str = "drafting", state: str = "running") -> Path:
    jobs_name, _pid = stop.LANES[lane]
    jd = tmp_path / "run" / jobs_name / "01JOB"
    jd.mkdir(parents=True)
    (jd / "daemon.json").write_text(json.dumps({"state": state, "claimed": True, "attempts": 1}))
    return jd


def _cli(tmp_path: Path, *extra: str, lane: str = "drafting", euid: int = 0, sleep=lambda _s: None) -> int:
    argv = [lane, "01JOB", "--by", "scott", "--why", "duplicate job", "--run-dir", str(tmp_path / "run"), *extra]
    return stop.main(argv, euid=lambda: euid, clock=lambda: 100.0, sleep=sleep)


def test_the_cli_refuses_anyone_but_root(tmp_path, capsys):
    jd = _job(tmp_path)
    assert _cli(tmp_path, euid=10001) == 1
    assert "only root" in capsys.readouterr().out
    assert smd_stop.read(jd) is None


def test_the_cli_writes_the_stop_and_reads_back_the_recorded_state(tmp_path, capsys):
    jd = _job(tmp_path)

    def lane_records_it(_s: float) -> None:  # the daemon, a beat later
        st = json.loads((jd / "daemon.json").read_text())
        st.update(state="stopped_by_smd", stopped_by="scott", stopped_why="duplicate job", stopped_cents=5664)
        st.update(stopped_recorded=True, stopped_exit=-15)
        (jd / "daemon.json").write_text(json.dumps(st))

    assert _cli(tmp_path, sleep=lane_records_it) == 0
    out = capsys.readouterr().out
    assert "before: state=running" in out
    assert "after: state=stopped_by_smd by=scott why=duplicate job spend=$56.64 ledger_recorded=True" in out
    assert smd_stop.read(jd) == {"by": "scott", "why": "duplicate job", "at": 100.0}


def test_the_cli_says_when_the_lane_has_not_recorded_it_yet(tmp_path, capsys):
    jd = _job(tmp_path)
    assert _cli(tmp_path, "--wait", "2") == 3
    assert "not yet recorded" in capsys.readouterr().out
    assert smd_stop.read(jd) is not None, "the request stays"


@pytest.mark.parametrize("state", ["delivered", "failed"])
def test_the_cli_refuses_a_job_that_already_ended(tmp_path, state):
    jd = _job(tmp_path, state=state)
    assert _cli(tmp_path) == 1
    assert smd_stop.read(jd) is None


def test_the_cli_refuses_an_unknown_job_and_a_path(tmp_path):
    (tmp_path / "run").mkdir()
    assert _cli(tmp_path) == 1
    argv = ["drafting", "../x", "--by", "s", "--why", "w", "--run-dir", str(tmp_path / "run")]
    assert stop.main(argv, euid=lambda: 0) == 1


def test_the_cli_knows_every_lane_that_runs_a_child():
    from jobs import lanes

    names = {n.removesuffix("_lane") for n in lanes.LANES} - {"note"}  # the note lane runs no child
    assert names == set(stop.LANES)
