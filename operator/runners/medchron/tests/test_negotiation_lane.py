"""The daemon's negotiation lane: one wake per new offer (each on its own
notice id), one wake for a failed job, none for a run with nothing new, and
the wait behind any other lane's live child."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from medchron.negotiation_lane import NegotiationBroker, NegotiationLane, wake_task

JOB = "01KTJ0BX0000000000000000NG"
N1 = "01KTJ0BX0000000000000000N1"
N2 = "01KTJ0BX0000000000000000N2"

DELIVERED = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'negotiation' and job['month_cents_used'] == 77 and job['slug'] == 'example', job
v = {"verdict": "delivered", "stage": "report", "cents": 40, "matters_total": 9, "matters_seeded": 0,
     "docs_read": 2, "docs_failed": 0, "notices": NOTICES}
print(json.dumps(v))
"""


class FakeClient:
    def __init__(self, notice_ids=()):
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.notice_ids = list(notice_ids)

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a == "negotiation_job_status":
            if "job_id" not in req:
                return {"ok": True, "month_cents": 77}
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "negotiation_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            return {"ok": True, "notice_ids": self.notice_ids if req["state"] == "delivered" else []}
        raise AssertionError(f"the negotiation lane speaks only negotiation verbs, not {a}")


def _lane(tmp_path: Path, notices: list, notice_ids=(), script: str | None = None):
    run_dir = tmp_path / "run"
    q = run_dir / "negotiation-queue"
    q.mkdir(parents=True)
    client = FakeClient(notice_ids)
    p = tmp_path / "runner.py"
    p.write_text(script or DELIVERED.replace("NOTICES", json.dumps(notices)))
    lane = NegotiationLane(
        run_dir=run_dir,
        broker=NegotiationBroker(client),
        runner_cmd=[sys.executable, str(p)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        state_dir=str(tmp_path / "state"),
    )
    client.rows[JOB] = {"state": "queued"}
    (q / f"{JOB}.json").write_text(json.dumps({"kind": "negotiation", "trigger": "scheduled"}))
    return lane, client


def test_each_new_offer_gets_its_own_wake_and_the_job_none(tmp_path):
    notices = [{"matter_id": "m", "matter_number": "1", "status": "entered", "text": "x"}] * 2
    lane, client = _lane(tmp_path, notices, [N1, N2])
    assert lane.tick() == "delivered"
    assert [s for _j, s, _f in client.records] == ["running", "delivered"]
    assert len(client.records[-1][2]["notices"]) == 2
    assert "wake" not in lane._daemon_state(JOB)
    for nid in (N1, N2):
        st = lane._daemon_state(nid)
        assert st["state"] == "delivered" and st["wake"]["pending"] is True
        assert st["wake"]["task"] == wake_task(nid, "notice")
    assert lane._wakes_pending() == [N1, N2] and lane._in_progress() == []


def test_the_wake_text_carries_no_matter_fact(tmp_path):
    task = wake_task(N1, "notice")
    assert task.splitlines() == [
        f"Run the negotiation-watch skill's DELIVER mode for negotiation job {N1}.",
        "Kind: negotiation.",
        "Trigger: scheduled.",
        "Outcome: notice.",
    ]


def test_a_run_with_nothing_new_wakes_nobody(tmp_path):
    lane, _ = _lane(tmp_path, [])
    assert lane.tick() == "delivered"
    assert lane._wakes_pending() == []


def test_a_failed_job_wakes_once_on_its_own_id(tmp_path):
    lane, client = _lane(tmp_path, [], script="print('no verdict here')")
    assert lane.tick() == "failed"
    assert client.records[-1][1] == "failed" and client.records[-1][2]["notices"] == []
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert "Outcome: failed." in task and f"negotiation job {JOB}" in task


def test_the_lane_waits_behind_a_live_demand_or_drafting_child(tmp_path):
    lane, client = _lane(tmp_path, [])
    for name in ("demand-child.pid", "drafting-child.pid"):
        (lane.run_dir / name).write_text(str(os.getpid()))
        assert lane.tick() == "waiting" and client.records == []
        lane._write_state(JOB, retry_after=None)
        (lane.run_dir / name).unlink()
    assert lane.tick() == "delivered"
