"""A running drafting job says where it is, on its ledger row (2026-11-24).

A drafting job pulling about 500 files showed cents 0 and an ``updated_at``
frozen for 26 minutes. The pull now writes its n of N, the child its stage, and
the daemon's heartbeat sends them to the broker when they move and at least
every two minutes; the stage timings and expected finish ride on the ending
record so they outlive the run dir. Ids are invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs.broker_client import BrokerError
from jobs.drafting import progress
from jobs.matter import pull as matter_pull


class _Seat:
    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": f, "url": f"https://fake.invalid/{f}", "size": 3} for f in file_ids]

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(url[-3:].encode())
        return 3


def test_the_pull_writes_how_far_it_has_got(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: drop ``write_progress`` from ``fetch_all`` and nothing says n of N."""
    seen: list[dict] = []
    real = matter_pull.write_progress

    def spy(data: Path, done: int, total: int) -> None:
        real(data, done, total)
        seen.append(json.loads((data / matter_pull.PROGRESS).read_text()))

    targets = [{"id": f"f{i:02d}", "name": "x.pdf", "ext": ".pdf", "folder": "f", "size": 3} for i in range(20)]
    monkeypatch.setattr(matter_pull, "write_progress", spy)
    matter_pull.fetch_all(_Seat(), "m", targets, tmp_path, lambda _m: None, progress=True)
    assert [(s["done"], s["total"]) for s in seen] == [(0, 20), (8, 20), (16, 20), (20, 20)]


def _job(tmp_path: Path, state: dict, pulled: dict | None = None) -> Path:
    jd = tmp_path / "01JOBPROGRESSAAAAAAAAAAAAA"
    (jd / "data").mkdir(parents=True)
    (jd / "data" / "state.json").write_text(json.dumps(state))
    if pulled is not None:
        (jd / "data" / matter_pull.PROGRESS).write_text(json.dumps(pulled))
    return jd


def test_the_note_reads_the_stage_the_count_and_the_timings(tmp_path: Path) -> None:
    state = {
        "facts": {"status": "done", "seconds": 2.0},
        "destination": {"status": "done", "seconds": 1.5},
        "current": {"stage": "pull", "started": "2026-11-24T19:00:00Z"},
        "date": "11-24-26",
    }
    jd = _job(tmp_path, state, {"done": 120, "total": 502, "at": "2026-11-24T19:10:00Z"})
    assert progress.current(jd) == {
        "stage": "pull",
        "progress": "120/502",
        "stage_seconds": {"facts": 2.0, "destination": 1.5},
    }
    assert progress.final_fields(jd) == {"stage_seconds": {"facts": 2.0, "destination": 1.5}}


def test_a_count_is_named_only_while_the_pull_runs(tmp_path: Path) -> None:
    state = {"pull": {"status": "done", "seconds": 1560.0}, "current": {"stage": "transcribe"}}
    jd = _job(tmp_path, state, {"done": 502, "total": 502})
    assert "progress" not in progress.current(jd) and progress.current(jd)["stage"] == "transcribe"


class _Lane:
    def __init__(self, jd: Path, fail: bool = False) -> None:
        self.jd, self.fail, self.sent = jd, fail, []
        self.broker = self

    def job_dir(self, job_id: str) -> Path:
        return self.jd

    def record(self, job_id: str, state: str, fields: dict) -> dict:
        if self.fail:
            raise BrokerError("job ended")
        self.sent.append((state, dict(fields)))
        return {}


def test_the_heartbeat_sends_on_a_move_and_at_least_every_two_minutes(tmp_path: Path) -> None:
    """FALSIFIER: send only on change and a long, slow batch leaves the row frozen;
    send every beat and the broker is written every 30 seconds for nothing."""
    jd = _job(tmp_path, {"current": {"stage": "pull"}}, {"done": 8, "total": 502})
    lane, sent, t = _Lane(jd), {}, [1000.0]
    progress.report(lane, "j", sent, lambda: t[0])
    t[0] += 30
    progress.report(lane, "j", sent, lambda: t[0])  # nothing moved, under two minutes
    assert len(lane.sent) == 1
    t[0] += progress.REFRESH_SECONDS
    progress.report(lane, "j", sent, lambda: t[0])  # the refresh
    (jd / "data" / matter_pull.PROGRESS).write_text(json.dumps({"done": 16, "total": 502}))
    t[0] += 30
    progress.report(lane, "j", sent, lambda: t[0])  # moved
    assert [f["progress"] for _s, f in lane.sent] == ["8/502", "8/502", "16/502"]
    assert all(s == "running" for s, _f in lane.sent)


def test_a_refused_note_is_tried_again_and_never_raises(tmp_path: Path) -> None:
    jd = _job(tmp_path, {"current": {"stage": "pull"}})
    sent: dict = {}
    progress.report(_Lane(jd, fail=True), "j", sent, lambda: 1.0)
    assert sent == {}


@pytest.mark.parametrize("bad", [{"current": {"stage": "Pull Files"}}, {}])
def test_nothing_is_sent_without_a_stage_the_broker_reads(tmp_path: Path, bad: dict) -> None:
    lane = _Lane(_job(tmp_path, bad))
    progress.report(lane, "j", {}, lambda: 1.0)
    assert lane.sent == []


def test_a_stage_names_itself_as_it_starts(tmp_path: Path) -> None:
    """FALSIFIER: drop the ``current`` put from ``DraftingRun._stage`` and a
    running stage is unnamed until it finishes (a 26-minute pull said nothing)."""
    from jobs.drafting.run import DraftingRun

    class _Run:
        data = tmp_path
        _state, _put, _done, _is_done = (
            DraftingRun._state,
            DraftingRun._put,
            DraftingRun._done,
            DraftingRun._is_done,
        )

        def log(self, _line: str) -> None:
            pass

    run = _Run()
    seen: list[str] = []
    DraftingRun._stage(run, "pull", lambda: seen.append(run._state()["current"]["stage"]))
    assert seen == ["pull"] and run._state()["pull"]["status"] == "done"


TIMED_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
(jd / 'data').mkdir(exist_ok=True)
(jd / 'data' / 'state.json').write_text(json.dumps({"pull": {"status": "done", "seconds": 1560.4},
    "current": {"stage": "pull"}, "expected_finish": {"at": "2026-11-24T21:40:00Z"}}))
print(json.dumps([{"unit": "drafting", "outcome": "failed", "stage": "gate", "reason": "x", "dollars": 3.0}]))
"""


def test_the_ending_record_keeps_the_timings_and_the_heartbeat_sends_the_note(tmp_path: Path) -> None:
    """FALSIFIER: drop ``final_fields`` from ``_report`` and the timings go with
    the run dir; drop the lane's ``heartbeat`` override and a running row never moves."""
    from test_drafting_lane import _lane, _submit

    lane, client = _lane(tmp_path, TIMED_RUNNER)
    jid = _submit(lane, client)
    assert lane.tick() == "failed"
    ending = next(f for _j, s, f in client.records if s == "failed")
    assert ending["stage_seconds"] == {"pull": 1560.4} and ending["expected_finish"] == "2026-11-24T21:40:00Z"
    client.rows[jid]["state"] = "running"
    lane.heartbeat(running=jid)
    note = client.records[-1]
    assert note[1] == "running" and note[2]["stage"] == "pull"
