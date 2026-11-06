"""SMD ends a job that never started, and an ended job's envelope never runs.

2026-11-06, the A&P seat: memo job 01M6PNY4 sat submitted in the drafting
queue, held ``lane_unproven``, after its ask had been answered by hand. No
verb ended it: ``jobs.stop`` refused a job with no job dir, and ``claim_next``
never read the ledger row, so marking the row failed would not have stopped it
running the day the memo lane was proven. The way out was a hand-built root
script. Each test here fails on the code before this change:

* ``jobs stop`` on a queued or proof-held job moves its envelope out of the
  queue beside the stop, the lane records it ``failed`` / ``stopped_by_smd``
  and sets its lane hold aside, and it reads back; twice is safe;
* a parked job (claimed, not running) is ended the same way, from the lane's
  idle wait and from beside another job's running child;
* ``claim_next`` refuses and sets aside an envelope whose ledger row ended,
  even once its lane is proven; SMD's own resume of a failed job still runs.
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

import test_drafting_lane as drafting_t
import test_smd_job_stop as stop_t
import test_stop_cut_short as cut_t
from jobs import daemon as daemon_mod, lane_proof, resume as resume_mod, smd_stop, stop
from jobs.broker_client import BrokerError

#: The CLI's lane name for each test lane's ``LANE``.
CLI_LANE = {"medchron": "medchron", "demand": "demand", "drafting": "drafting", "negotiation": "negotiation"}


def _cli(lane: Any, jid: str, *extra: str, sleep=lambda _s: None) -> int:
    name = CLI_LANE.get(lane.LANE, lane.LANE)
    argv = [name, jid, "--by", "scott", "--why", "answered by hand", "--run-dir", str(lane.run_dir), *extra]
    return stop.main(argv, euid=lambda: 0, clock=lambda: 100.0, sleep=sleep)


def _records(client: Any, jid: str) -> list[str]:
    return [s for j, s, _f in client.records if j == jid]


def _drafting(tmp_path: Path) -> tuple[Any, Any, str, str]:
    lane, client = drafting_t._lane(tmp_path)
    jid = drafting_t._submit(lane, client)
    env = json.loads((lane.queue / f"{jid}.json").read_text())
    return lane, client, jid, lane_proof.lane_key("drafting", env)


def _hold(lane: Any, client: Any, jid: str, key: str) -> None:
    """The incident's state: queued, noted ``lane_unproven``, a hold marker."""
    (lane.run_dir / lane_proof.AUTHORED_NAME).write_text("schema: 1\nlanes: {}\nrevoked: []\n")
    assert lane.tick() is None
    assert client.records == [(jid, "submitted", {"reason": f"{lane_proof.HOLD_PREFIX}{key}"})]
    assert [h["job_id"] for h in lane_proof.holds(lane.run_dir)] == [jid]


def _prove(lane: Any, key: str) -> None:
    lane_proof.append_event(lane.run_dir, {"event": "proven", "key": key, "evidence": "vfy_x"})


# ---- rule 1: the stop works on a queued and a held job ---------------------------------
def test_stop_ends_a_job_held_for_its_lane_proof_and_it_never_runs(tmp_path, real_lane_gate, capsys):
    lane, client, jid, key = _drafting(tmp_path)
    _hold(lane, client, jid, key)

    def lane_polls(_s: float) -> None:  # the lane's idle wait, a second later
        smd_stop.take_unclaimed(lane)

    assert _cli(lane, jid, sleep=lane_polls) == 0
    out = capsys.readouterr().out
    assert "before: queued" in out and "after: state=stopped_by_smd by=scott why=answered by hand" in out
    assert "ledger_recorded=True" in out
    jd = lane.job_dir(jid)
    assert not (lane.queue / f"{jid}.json").exists() and (jd / smd_stop.ENVELOPE).is_file()
    assert client.records[-1] == (jid, "failed", {"reason": "stopped_by_smd: by scott: answered by hand"})
    st = lane._daemon_state(jid)
    assert st["state"] == "stopped_by_smd" and not st.get("claimed") and not st.get("wake")
    assert lane_proof.holds(lane.run_dir) == [], "the hold is set aside, not left waiting on a proof"
    assert json.loads((jd / smd_stop.HOLD_KEPT).read_text())["job_id"] == jid
    # The lane is proven: the job still never runs, and nothing more is recorded.
    _prove(lane, key)
    n = len(client.records)
    assert lane.tick() is None
    assert len(client.records) == n and "running" not in _records(client, jid)
    assert not (lane.run_dir / "drafting-jobs" / jid / "job.json").exists(), "no child input was ever written"
    # Twice is safe: it reads the stop back.
    assert _cli(lane, jid) == 0
    assert "already stopped by scott" in capsys.readouterr().out
    assert len(client.records) == n


@cut_t.LANES
def test_stop_ends_a_queued_job_on_every_lane(tmp_path, make):
    lane, client, submit, _skill = make(tmp_path)
    jid = submit()
    assert _cli(lane, jid, "--wait", "0") == 3  # written; the lane has not polled yet
    assert not (lane.queue / f"{jid}.json").exists()
    assert lane.tick() is None, "the stopped job is never claimed"
    assert _records(client, jid) == ["failed"]
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd"
    assert _cli(lane, jid) == 0


def test_a_claim_racing_the_stop_still_finds_the_stop(tmp_path):
    """The marker is written before the envelope moves: a lane that claimed
    the envelope a moment earlier is refused at its child's start."""
    lane, client, jid, _key = _drafting(tmp_path)
    assert lane.claim_next() == jid  # the lane won the race
    assert smd_stop.stop_queued(lane.queue, lane.job_dir(jid), by="scott", why="dup", at=1.0) is False
    lane.queue.joinpath(f"{jid}.json").write_text("{}")  # the envelope seen by the CLI an instant before
    assert smd_stop.stop_queued(lane.queue, lane.job_dir(jid), by="scott", why="dup", at=1.0) is True
    stop_t._spin(lane, tmp_path)
    lane.tick()
    assert not (lane.job_dir(jid) / "child-ran").exists()
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd"
    assert smd_stop.read(lane.job_dir(jid))["at"] == 1.0
    lane.queue.joinpath(f"{jid}.json").write_text("{}")
    assert smd_stop.stop_queued(lane.queue, lane.job_dir(jid), by="other", why="again", at=2.0) is True
    assert smd_stop.read(lane.job_dir(jid))["by"] == "scott", "a second stop keeps the first request"
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd", "a second stop never reopens an ended one"
    assert not (lane.queue / f"{jid}.json").exists()


def test_stop_ends_a_parked_job_that_the_lane_would_never_reach(tmp_path, capsys):
    """A runner-held job is claimed and parked: no tick runs it, so a stop
    left for its child would wait forever."""
    lane, client, jid, _key = _drafting(tmp_path)
    assert lane.claim_next() == jid
    lane._write_state(jid, state="held", wake={"pending": True, "task": "tell the firm"})
    client.rows[jid]["state"] = "held"
    assert jid not in lane._in_progress()

    def lane_polls(_s: float) -> None:
        smd_stop.take_unclaimed(lane)

    assert _cli(lane, jid, sleep=lane_polls) == 0
    st = lane._daemon_state(jid)
    assert st["state"] == "stopped_by_smd" and st["wake"] is None, "no DELIVER turn reaches the firm"
    assert _records(client, jid)[-1] == "failed"


def test_a_stop_the_broker_cannot_record_is_retried_and_never_runs(tmp_path):
    lane, client, jid, _key = _drafting(tmp_path)
    real = client._request
    down = {"on": True}

    def flaky(req: dict) -> dict:
        if down["on"] and req["action"] == "drafting_job_record":
            raise BrokerError("broker refused: busy: db locked")
        return real(req)

    client._request = flaky
    assert smd_stop.stop_queued(lane.queue, lane.job_dir(jid), by="scott", why="dup", at=1.0)
    assert smd_stop.take_unclaimed(lane) == []
    assert lane._daemon_state(jid)["state"] == "submitted", "not closed while the ledger still says it is live"
    assert lane.tick() is None and "running" not in _records(client, jid)
    down["on"] = False
    lane.clock = lambda: 1_000_000.0 + smd_stop.RETRY_SECONDS + 1
    assert smd_stop.take_unclaimed(lane) == [jid]
    assert _records(client, jid) == ["failed"]


def test_the_lane_records_a_stop_from_its_idle_wait(tmp_path):
    lane, client, jid, _key = _drafting(tmp_path)
    smd_stop.stop_queued(lane.queue, lane.job_dir(jid), by="scott", why="dup", at=1.0)
    lane.tick = lambda: None  # only the wait between ticks runs
    deadline = time.monotonic() + 15
    done = lambda: lane._daemon_state(jid).get("state") == "stopped_by_smd" or time.monotonic() > deadline  # noqa: E731
    lane.run_forever(done, poll_seconds=3)
    assert lane._daemon_state(jid)["state"] == "stopped_by_smd"
    assert _records(client, jid) == ["failed"]


def test_the_lane_records_a_stop_beside_another_jobs_running_child(tmp_path, monkeypatch):
    """The lane thread sits in one child's wait for as long as that job runs;
    a stop of a queued job must not wait for it."""
    monkeypatch.setenv(smd_stop.GRACE_ENV, "0.3")
    lane, client = drafting_t._lane(tmp_path)
    a = drafting_t._submit(lane, client, "01DRAFTJ0B000000000000000A")
    b = drafting_t._submit(lane, client, "01DRAFTJ0B000000000000000B")
    stop_t._spin(lane, tmp_path)
    lane.stopping = lambda: False

    def go() -> None:
        deadline = time.monotonic() + 20
        try:
            while not (lane.job_dir(a) / "child-ran").is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            smd_stop.stop_queued(lane.queue, lane.job_dir(b), by="scott", why="dup", at=1.0)
            while lane._daemon_state(b).get("state") != "stopped_by_smd" and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:  # always end the spinning child, whatever the code under test did
            smd_stop.request(lane.job_dir(a), by="scott", why="end the test", at=1.0)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    lane.tick()
    t.join()
    assert lane._daemon_state(b)["state"] == "stopped_by_smd"
    order = [(j, s) for j, s, _f in client.records if s == "failed"]
    assert order[0] == (b, "failed"), "the queued job was recorded while the other child ran"
    assert _records(client, a).count("failed") == 1, "the running job is the lane's own to record, once"


# ---- rule 2: an ended job's envelope never runs ---------------------------------------
@pytest.mark.parametrize("ledger", ["failed", "delivered"])
def test_claim_sets_aside_an_envelope_whose_ledger_row_ended(tmp_path, real_lane_gate, ledger, caplog):
    """The row was marked ended by hand while the envelope sat held; the lane
    is proven later. The hold must not release it into a run."""
    lane, client, jid, key = _drafting(tmp_path)
    _hold(lane, client, jid, key)
    client.rows[jid]["state"] = ledger
    _prove(lane, key)
    n = len(client.records)
    assert lane.tick() is None
    assert len(client.records) == n, "nothing recorded: no running, no released note"
    assert not (lane.queue / f"{jid}.json").exists()
    assert (lane.queue / smd_stop.ENDED_DIR / f"{jid}.json").is_file()
    assert not lane.job_dir(jid).exists()
    assert lane_proof.holds(lane.run_dir) == []
    assert (lane.run_dir / lane_proof.HOLDS_DIR / smd_stop.ENDED_DIR / f"drafting-{jid}.json").is_file()
    said = [r.getMessage() for r in caplog.records if jid in r.getMessage() and "not claimed" in r.getMessage()]
    assert len(said) == 1 and f"its ledger row is {ledger}" in said[0]


def test_claim_sets_aside_an_envelope_whose_job_dir_carries_a_stop(tmp_path):
    """Even while the broker cannot record the stop (the row still looks live)."""
    lane, client, jid, _key = _drafting(tmp_path)
    real = client._request

    def no_record(req: dict) -> dict:
        if req["action"] == "drafting_job_record":
            raise BrokerError("broker refused: busy: db locked")
        return real(req)

    client._request = no_record
    lane.job_dir(jid).mkdir(parents=True)
    smd_stop.request(lane.job_dir(jid), by="scott", why="dup", at=1.0)
    assert lane.claim_next() is None
    assert client.rows[jid]["state"] == "submitted"
    assert (lane.queue / smd_stop.ENDED_DIR / f"{jid}.json").is_file()


def test_smd_resume_of_a_failed_job_still_runs(tmp_path):
    """The resume re-queues while the row still says failed: not stale."""
    lane, client, jid, _key = _drafting(tmp_path)
    assert lane.tick() == "delivered"
    client.rows[jid]["state"] = "failed"
    lane._write_state(jid, state="failed")
    (lane.queue / f"{resume_mod.MARKER_PREFIX}{jid}.json").write_text(json.dumps({"job_id": jid, "reason": "fixed"}))
    assert resume_mod.take_requests(lane) == [jid]
    assert lane.claim_next() == jid


def test_a_resume_of_a_stopped_job_is_refused_by_the_ledger_rule_once_it_has_ended_again(tmp_path):
    """A failed row whose job dir has ended (finished_at set) is not a resume."""
    lane, client, jid, _key = _drafting(tmp_path)
    assert lane.tick() == "delivered"
    client.rows[jid]["state"] = "failed"
    lane._write_state(jid, state="failed", resumes=1, finished_at=5.0)
    (lane.queue / f"{jid}.json").write_text(json.dumps(drafting_t.envelope(jid)))
    assert lane.claim_next() is None
    assert (lane.queue / smd_stop.ENDED_DIR / f"{jid}.json").is_file()


def test_the_sweep_and_the_daemon_agree_on_what_has_ended():
    assert smd_stop._ended_states() == daemon_mod.TERMINAL
    assert smd_stop.STATE in daemon_mod.TERMINAL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([os.path.abspath(__file__)]))
