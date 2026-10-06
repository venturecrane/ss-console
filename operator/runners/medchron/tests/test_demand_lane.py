"""The daemon's demand lane: kind dispatch by queue, the demand verbs, the wake
text, deferral on the demand inputs only, and the separate slot."""

from __future__ import annotations

import json
import os
from pathlib import Path

from demand_testkit import job_doc, make_inputs
from medchron.daemon import Daemon
from medchron.demand_lane import DemandBroker, DemandLane, build_lane

RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'demand' and job['month_cents_used'] == 1234, job
out = [{"unit": "demand", "kind": "demand", "outcome": "delivered", "stage": "file", "reason": None, "dollars": 6.5,
        "folder_id": "folder-7", "coverage_report": False,
        "files": [{"name": "Demand.Alpha Example.docx", "size": 41000}, {"name": "Gap Audit - 100001 - 10-06-26.docx", "size": 9000}]}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""
FAIL_RUNNER = """
import json, sys
print(json.dumps([{"unit": "demand", "outcome": "failed", "stage": "per_job_cap_usd",
                   "reason": "per_job_cap_usd: over the cap; the file named Secret Client.pdf", "dollars": 0}]))
"""


class FakeClient:
    """The raw socket client the DemandBroker wraps."""

    def __init__(self, cents: int | None = 1234) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.requests: list[dict] = []
        self.cents = cents

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        a = req["action"]
        if a == "demand_job_status":
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "demand_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            self.rows[req["job_id"]]["state"] = req["state"]
            return {"ok": True}
        if a == "demand_allowance":
            return {"ok": True, "used": 0, **({"cents_used": self.cents} if self.cents is not None else {})}
        raise AssertionError(f"the demand lane must speak only demand verbs, not {a}")


def _lane(
    tmp_path: Path, script: str = RUNNER, cents: int | None = 1234, inputs: bool = True
) -> tuple[DemandLane, FakeClient]:
    run_dir = tmp_path / "run"
    q = run_dir / "demand-queue"
    q.mkdir(parents=True)
    client = FakeClient(cents)
    p = tmp_path / "runner.py"
    p.write_text(script)
    root = make_inputs(tmp_path / "inputs") if inputs else tmp_path / "missing"
    lane = DemandLane(
        run_dir=run_dir,
        broker=DemandBroker(client),
        runner_cmd=[os.environ.get("PYTHON", "python3"), str(p)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        inputs_dir=str(root),
    )
    return lane, client


def _submit(lane: DemandLane, client: FakeClient, job_id: str = "01DEMAND0000000000000000AA") -> str:
    client.rows[job_id] = {"state": "submitted"}
    env = {k: v for k, v in job_doc(job_id).items() if k not in ("slug", "month_cents_used")}
    (lane.queue / f"{job_id}.json").write_text(json.dumps(env))
    return job_id


def test_the_lane_claims_from_the_demand_queue_and_records_through_demand_verbs(tmp_path):
    lane, client = _lane(tmp_path)
    jid = _submit(lane, client)
    assert lane.tick() == "delivered"
    assert [s for _j, s, _f in client.records] == ["running", "delivered"]
    fields = client.records[-1][2]
    assert fields["cents"] == 650 and fields["folder_id"] == "folder-7"
    assert fields["delivery"]["files"][0] == {"name": "Demand.Alpha Example.docx", "size": 41000}
    assert (lane.jobs / jid / "job.json").is_file()
    assert {"action": "demand_allowance", "exclude_job_id": jid} in client.requests


def test_the_wake_asks_for_deliver_mode_with_the_contract_fields(tmp_path):
    lane, client = _lane(tmp_path)
    jid = _submit(lane, client)
    lane.tick()
    task = lane._daemon_state(jid)["wake"]["task"]
    assert task.startswith(f"Run the demand-letter-drafter skill's DELIVER mode for demand job {jid}.")
    for line in (
        "Kind: demand.",
        "Outcome: delivered.",
        "Matter number: 100001.",
        "Folder id: folder-7.",
        "Demand.Alpha Example.docx (41000 bytes)",
        "Requested by: admin@firm.example.",
        "Request ref: <req-1@firm.example>.",
    ):
        assert line in task


def test_a_failed_job_wakes_with_the_stage_never_the_free_text_reason(tmp_path):
    lane, client = _lane(tmp_path, script=FAIL_RUNNER)
    jid = _submit(lane, client)
    assert lane.tick() == "failed"
    assert "Secret Client" in client.records[-1][2]["reason"]  # the ledger keeps it for a person
    task = lane._daemon_state(jid)["wake"]["task"]
    assert "Reason: stopped at per_job_cap_usd." in task and "Secret Client" not in task


def test_a_schema_miss_in_the_demand_inputs_defers_demand_jobs_only(tmp_path):
    lane, client = _lane(tmp_path, inputs=False)
    _submit(lane, client)
    assert lane.tick() == "deferred"
    assert client.records == []


def test_an_unknown_month_spend_defers_rather_than_running_unmetered(tmp_path):
    lane, client = _lane(tmp_path, cents=None)
    _submit(lane, client)
    assert lane.tick() == "deferred" and client.records == []


def test_the_lane_has_its_own_slot_queue_cgroup_and_heartbeat(tmp_path, monkeypatch):
    lane, client = _lane(tmp_path)
    chron = Daemon(
        run_dir=lane.run_dir,
        broker=object(),
        runner_cmd=["true"],
        customer_slug="example",
        cgroup_root=tmp_path / "cg",
        child_uid=None,
    )
    assert lane.queue != chron.queue and lane.jobs != chron.jobs
    assert lane.LANE == "demand" and chron.LANE == "medchron"
    # a chronology job in progress does not stop the demand lane from running its own
    (chron.jobs / "01CHRON").mkdir(parents=True)
    (chron.jobs / "01CHRON" / "daemon.json").write_text(json.dumps({"claimed": True, "state": "running"}))
    assert chron._in_progress() == ["01CHRON"]
    _submit(lane, client)
    assert lane.tick() == "delivered"
    assert (lane.run_dir / "demand-heartbeat.json").is_file() and not (lane.run_dir / "heartbeat.json").exists()
    monkeypatch.setenv("SMD_DEMAND_QUEUE_DIR", str(lane.queue))
    chron.broker = client  # build_lane wraps the chronology daemon's own socket client
    built = build_lane(chron)
    assert built is not None and built.queue == lane.queue and built.memory_max == 1024 * 1024 * 1024
    monkeypatch.delenv("SMD_DEMAND_QUEUE_DIR")
    assert build_lane(chron) is None
