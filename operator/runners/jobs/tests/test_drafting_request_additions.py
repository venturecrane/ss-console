"""An attorney's words added while his drafting job runs (2026-11-05, A&P).

The answer key: he asked for a discovery set, then widened it in the same
thread two minutes later; the Operator said the direction was noted, and
nothing carried it to the job. Each test fails if the line it defends goes.
Synthetic names only."""

from __future__ import annotations

import json
import os
from pathlib import Path

from drafting_testkit import envelope, make_inputs, make_job
from jobs import request_additions as ra
from jobs import smd_stop
from jobs.drafting import job as job_mod
from jobs.drafting_lane import DraftingBroker, DraftingLane

JOB = "01DRAFTJ0B000000000000000A"
WORDS = "All appropriate topics, including those listed."


class _Client:
    def _request(self, req: dict) -> dict:
        if req["action"] == "drafting_allowance":
            return {"ok": True, "used": 0, "remaining": 5, "cents_used": 0}
        if req["action"] == "drafting_job_status":
            return {"ok": True, "job": {"state": "submitted"}}
        return {"ok": True}


def _lane(tmp_path: Path) -> DraftingLane:
    q = tmp_path / "run" / "drafting-queue"
    q.mkdir(parents=True)
    return DraftingLane(
        run_dir=tmp_path / "run",
        broker=DraftingBroker(_Client()),
        runner_cmd=["python3", "-c", "pass"],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
    )


def _claimed(lane: DraftingLane) -> Path:
    """A job past its claim, with the job file the lane stamps and paid stages done."""
    jd = lane.job_dir(JOB)
    jd.mkdir(parents=True)
    (jd / "envelope.json").write_text(json.dumps(envelope(JOB, cls="discovery_set")))
    make_job(jd, cls="discovery_set", job_id=JOB)
    (jd / "data").mkdir()
    (jd / "data" / "state.json").write_text(json.dumps({"transcribe": {"status": "done"}}))
    lane._write_state(JOB, claimed=True, state="submitted")
    return jd


def _mark(lane: DraftingLane, ref: str = "<later-1@firm.example>", words: str = WORDS) -> str:
    key = ra.key_of(ref)
    marker = {"job_id": JOB, "key": key, "by": "admin@firm.example", "at": "2026-11-05T17:01:00Z", "words": words}
    (lane.queue / ra.marker_name(JOB, key)).write_text(json.dumps(marker))
    return key


def _answer(lane: DraftingLane, key: str) -> dict:
    return json.loads((lane.queue / ra.answer_name(JOB, key)).read_text())


def test_words_before_compose_reach_the_job_the_next_process_reads_with_paid_stages_kept(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    paid = (jd / "data" / "state.json").read_bytes()
    key = _mark(lane)
    assert ra.take(lane) == [JOB]
    answer = _answer(lane, key)
    assert answer["result"] == "applied" and answer["envelope_digest"]
    text = job_mod.load(jd).request_text
    assert text.startswith("Please draft the discovery set.") and text.endswith(WORDS)
    assert "admin@firm.example" in text
    assert WORDS in json.loads((jd / "envelope.json").read_text())["request_text"]
    assert (jd / "data" / "state.json").read_bytes() == paid
    assert not list(lane.queue.glob(f"{ra.MARKER_PREFIX}*"))


def test_the_next_attempts_job_file_is_stamped_with_the_words(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    _mark(lane)
    ra.take(lane)
    (jd / "job.json").unlink()
    lane._write_job(JOB)
    assert job_mod.load(jd).request_text.endswith(WORDS)


def test_a_running_process_gets_the_words_when_it_closes_the_door(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    before = job_mod.load(jd)
    _mark(lane)
    ra.take(lane)
    assert WORDS not in before.request_text
    ra.close_door(jd)
    assert job_mod.load(jd).request_text.endswith(WORDS)


def test_words_after_the_door_closed_leave_the_request_as_it_was(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    ra.close_door(jd)
    was = job_mod.load(jd).request_text
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "compose_started"
    assert job_mod.load(jd).request_text == was
    assert json.loads((jd / "envelope.json").read_text())["request_text"] == was


def test_a_repeat_is_the_same_answer_and_adds_nothing(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    _mark(lane)
    ra.take(lane)
    once = job_mod.load(jd).request_text
    key = _mark(lane)
    ra.take(lane)
    answer = _answer(lane, key)
    assert answer["result"] == "applied" and answer["repeat"] is True
    assert job_mod.load(jd).request_text == once
    assert once.count(WORDS) == 1


def test_a_repeat_after_the_door_closed_still_says_it_was_applied(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    _mark(lane)
    ra.take(lane)
    ra.close_door(jd)
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "applied"


def test_a_job_still_in_the_queue_takes_the_words_in_its_queue_file(tmp_path) -> None:
    lane = _lane(tmp_path)
    (lane.queue / f"{JOB}.json").write_text(json.dumps(envelope(JOB, cls="discovery_set")))
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "applied"
    assert lane.claim_next() == JOB
    lane._write_job(JOB)
    assert job_mod.load(lane.job_dir(JOB)).request_text.endswith(WORDS)


def test_an_ended_job_is_refused_and_no_stop_lands(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    lane._write_state(JOB, state="delivered")
    was = (jd / "envelope.json").read_bytes()
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "job_ended"
    assert (jd / "envelope.json").read_bytes() == was
    assert smd_stop.read(jd) is None
    assert not (jd / ".stop-request").exists()


def test_a_held_job_is_ended_but_a_seat_pause_hold_is_not(tmp_path) -> None:
    lane = _lane(tmp_path)
    _claimed(lane)
    lane._write_state(JOB, state="held")
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "job_ended"
    lane._write_state(JOB, held_paused=True)
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "applied"


def test_a_stopped_job_is_ended(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    smd_stop.request(jd, by="scott", why="duplicate", at=1.0)
    key = _mark(lane)
    ra.take(lane)
    assert _answer(lane, key)["result"] == "job_ended"


def test_a_request_past_the_envelopes_ceiling_is_refused_whole(tmp_path) -> None:
    lane = _lane(tmp_path)
    jd = _claimed(lane)
    key = _mark(lane, words="x" * (ra.MAX_REQUEST_TEXT + 1))
    ra.take(lane)
    assert _answer(lane, key)["result"] == "too_long"
    assert "xxx" not in job_mod.load(jd).request_text


def test_a_job_with_no_files_left_is_ended_and_an_unreadable_marker_is_dropped(tmp_path) -> None:
    lane = _lane(tmp_path)
    key = _mark(lane)
    (lane.queue / ra.marker_name(JOB, "bad")).write_text("{not json")
    ra.take(lane)
    assert _answer(lane, key)["result"] == "job_ended"
    assert not list(lane.queue.glob(f"{ra.MARKER_PREFIX}*"))


def test_the_lane_takes_them_while_its_child_runs(tmp_path) -> None:
    """FALSIFIER: the lane thread sits in ``run_child`` for the whole job, so
    without the poll inside that wait the words wait for the job to end."""
    lane = _lane(tmp_path)
    lane.runner_cmd = [
        os.environ.get("PYTHON", "python3"),
        "-c",
        "import time, pathlib, sys\n"
        f"q = pathlib.Path({str(lane.queue)!r})\n"
        "for _ in range(100):\n"
        "    if list(q.glob('.added-*')):\n"
        "        break\n"
        "    time.sleep(0.1)\n"
        "else:\n"
        "    sys.exit(9)\n",
    ]
    (lane.queue / f"{JOB}.json").write_text(json.dumps(envelope(JOB, cls="discovery_set")))
    lane.claim_next()
    key = _mark(lane)
    lane._report = lambda job_id, code, out: str(code)  # type: ignore[method-assign]
    assert lane.run_job(JOB) == "0"
    assert _answer(lane, key)["result"] == "applied"
