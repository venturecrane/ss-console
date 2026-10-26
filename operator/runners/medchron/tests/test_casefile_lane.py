"""The daemon's case file lane: its queue and verbs, the stamped job, the wait
behind a live client-requested child, the yield pid list it hands the child,
the counts-only wake for a review and for an apply, and that the demand lane
never waits on it (the contention rule)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from casefile_testkit import JOB, envelope, make_inputs
from medchron.casefile_lane import CasefileBroker, CasefileLane

RUNNER = """
import json, os, sys, pathlib
jd = pathlib.Path(sys.argv[1])
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'casefile' and job['month_cents_used'] == 4321, job
assert job['per_job_cap_usd'] == 20.0 and job['monthly_budget_usd'] == 100.0 and job['slug'] == 'example', job
pathlib.Path(sys.argv[1], 'yield.txt').write_text(os.environ.get('MEDCHRON_CASEFILE_YIELD_PIDS', ''))
v = {"verdict": "delivered", "stage": "report", "mode": job["mode"], "cents": 1234, "matters_total": 12,
     "matters_read": 3, "matters_current": 8, "matters_held": 1, "lines_proposed": 4,
     "files": [{"role": "workbook", "name": "Case File Manager.xlsx", "size": 20480, "sha256": "ab", "file_id": "up-1"},
               {"role": "file_status", "name": "File Status 100001 2026-10-26.pdf", "size": 900, "sha256": "cd", "file_id": "up-2"}],
     "folder_id": "folder-7", "lines": [{"n": 1, "kind": "task", "matter_id": "m", "source_id": "t", "sha256": "e" * 64}],
     "line_outcomes": [{"n": 1, "kind": "task", "outcome": "completed"}, {"n": 2, "kind": "medicals", "outcome": "refused_changed"}],
     "metrics": {"stages": {}, "matters": {}},
     "proof": {"readback": "confirmed", "files": [{"name": "x", "file_id": "f1", "size": 10, "sha256": "a" * 64}], "review": "code_only", "review_cents": 0}}
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
"""


class FakeClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a == "casefile_job_status":
            if "job_id" not in req:
                return {"ok": True, "month_cents": 4321}
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "casefile_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            return {"ok": True}
        if a.startswith("delivery_"):
            return {"ok": True, "replies": []}
        raise AssertionError(f"the case file lane speaks only casefile verbs, not {a}")


def _lane(tmp_path: Path, script: str = RUNNER) -> tuple[CasefileLane, FakeClient]:
    run_dir = tmp_path / "run"
    q = run_dir / "casefile-queue"
    q.mkdir(parents=True)
    client = FakeClient()
    p = tmp_path / "runner.py"
    p.write_text(script)
    lane = CasefileLane(
        run_dir=run_dir,
        broker=CasefileBroker(client),
        runner_cmd=[sys.executable, str(p)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        state_dir=str(tmp_path / "state"),
    )
    return lane, client


def _submit(lane: CasefileLane, client: FakeClient, **extra) -> str:
    client.rows[JOB] = {"state": "queued"}
    env = {k: v for k, v in envelope().items() if k != "kind"}
    (lane.queue / f"{JOB}.json").write_text(json.dumps({**env, **extra}))
    return JOB


def test_the_lane_runs_a_review_and_records_ids_counts_and_lines(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    assert lane.tick() == "delivered"
    f = client.records[-1][2]
    assert (f["matters_total"], f["matters_read"], f["matters_current"], f["lines_proposed"]) == (12, 3, 8, 4)
    assert f["lines"][0]["n"] == 1 and "metrics" in f and "reason" not in f
    yields = (lane.run_dir / "casefile-jobs" / JOB / "yield.txt").read_text().split(",")
    assert sorted(Path(p).name for p in yields) == ["child.pid", "demand-child.pid", "drafting-child.pid"]


def test_the_review_wake_carries_counts_only(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    lane.tick()
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert task.splitlines()[0] == f"Run the case-file-manager skill's DELIVER mode for casefile job {JOB}."
    assert (
        "Matters: 12; read this run: 3; already current: 8; not reviewed: 1; File Status filed: 1; changes proposed: 4."
        in task
    )
    assert "100001" not in task and "File Status 1" not in task


def test_the_apply_wake_names_the_numbers_made_and_not_made(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client, mode="apply")
    lane.tick()
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert "Mode: apply." in task
    assert "Changes applied: 1 of 2; numbers applied: 1; numbers not applied: 2." in task


def test_a_live_client_child_holds_the_start_and_the_demand_lane_never_waits_on_this_one(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    (lane.run_dir / "demand-child.pid").write_text(str(os.getpid()))
    assert lane.tick() == "waiting" and client.records == []
    from medchron import drafting_lane, litigation_lane

    src = (Path(drafting_lane.__file__).parent / "demand_lane.py").read_text()
    assert "casefile-child.pid" not in src  # a demand never waits on a case file job
    # The demand cgroup's other lanes wait on a running case file job (2026-10-26 review).
    from medchron import negotiation_lane

    assert "casefile-child.pid" in litigation_lane.BUSY_PIDS
    assert "casefile-child.pid" in negotiation_lane.BUSY_PIDS


def test_a_resumed_apply_runs_only_the_lines_the_broker_still_holds_for_it(tmp_path):
    lane, client = _lane(tmp_path)
    lines = [{"n": n, "kind": "task", "matter_id": "m", "source_id": f"t{n}", "sha256": "e" * 64} for n in (1, 2)]
    _submit(lane, client, mode="apply", apply={"source_job_id": JOB, "approver": "a@firm.example",
                                              "thread_ref": "t", "lines": lines})  # fmt: skip
    client.rows[JOB] = {"state": "queued", "owned_lines": [1]}  # line 2 was taken by a newer apply
    lane.tick()
    job = json.loads((lane.run_dir / "casefile-jobs" / JOB / "job.json").read_text())
    assert [ln["n"] for ln in job["apply"]["lines"]] == [1]
