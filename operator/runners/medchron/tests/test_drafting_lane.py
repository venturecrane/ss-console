"""The daemon's drafting lane: its own queue, the drafting verbs, the wake text,
deferral on the drafting inputs only, and its start beside the demand lane."""

from __future__ import annotations

import json
import os
from pathlib import Path

from drafting_testkit import envelope, make_inputs
from medchron.drafting_lane import DraftingBroker, DraftingLane, build_lane

RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'drafting' and job['month_cents_used'] == 1234 and job['allowance_remaining'] == 5, job
assert job['slug'] == 'example' and job['document_class'] == 'mediation_brief', job
out = [{"unit": "drafting", "kind": "drafting", "outcome": "delivered", "stage": "file", "reason": None, "dollars": 12.5,
        "document_class": "mediation_brief", "folder_id": "folder-9",
        "files": [{"name": "Mediation Brief - 100001 - 10-07-26.docx", "size": 52000, "role": "draft"},
                  {"name": "Mediation Brief - 100001 - 10-07-26 - attorney notes.docx", "size": 9000, "role": "attorney_notes"}],
        "caption_discrepancies": [{"field": "case_number", "document_value": "CV-0001", "record_value": "CV-0010",
                                   "why": "differs", "source": "Complaint Gamma Example.pdf", "quote": "Case No. CV-0001"}],
        "caption_corrections": [{"field": "plaintiff", "from": "Gamma Exampel", "to": "GAMMA EXAMPLE",
                                 "source_document": "Complaint Gamma Example"}],
        "markers": [{"kind": "ATTORNEY", "text": "settlement authority"}]}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""
FAIL_RUNNER = """
import json
print(json.dumps([{"unit": "drafting", "outcome": "failed", "stage": "gate",
                   "reason": "the gate refused text quoting Secret Client.pdf", "dollars": 3.0}]))
"""


class FakeClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.requests: list[dict] = []

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        a = req["action"]
        if a == "drafting_job_status":
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "drafting_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            self.rows[req["job_id"]]["state"] = req["state"]
            return {"ok": True}
        if a == "drafting_allowance":
            return {"ok": True, "used": 0, "remaining": 5, "cents_used": 1234}
        raise AssertionError(f"the drafting lane must speak only drafting verbs, not {a}")


def _lane(tmp_path: Path, script: str = RUNNER, inputs: bool = True) -> tuple[DraftingLane, FakeClient]:
    run_dir = tmp_path / "run"
    q = run_dir / "drafting-queue"
    q.mkdir(parents=True)
    client = FakeClient()
    p = tmp_path / "runner.py"
    p.write_text(script)
    root = make_inputs(tmp_path / "inputs") if inputs else tmp_path / "missing"
    lane = DraftingLane(
        run_dir=run_dir,
        broker=DraftingBroker(client),
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


def _submit(lane: DraftingLane, client: FakeClient, job_id: str = "01DRAFTJ0B000000000000000A") -> str:
    client.rows[job_id] = {"state": "submitted"}
    (lane.queue / f"{job_id}.json").write_text(json.dumps(envelope(job_id)))
    return job_id


def test_the_lane_claims_from_its_queue_and_records_through_drafting_verbs(tmp_path):
    lane, client = _lane(tmp_path)
    jid = _submit(lane, client)
    assert lane.tick() == "delivered"
    assert [s for _j, s, _f in client.records] == ["running", "delivered"]
    f = client.records[-1][2]
    assert f["cents"] == 1250 and f["folder_id"] == "folder-9" and f["delivery"]["cents"] == 1250
    assert f["delivery"]["files"][0]["role"] == "draft"
    assert f["caption_discrepancies"][0]["field"] == "case_number" and f["markers"][0]["kind"] == "ATTORNEY"
    assert f["caption_corrections"][0]["to"] == "GAMMA EXAMPLE"
    assert {"action": "drafting_allowance", "exclude_job_id": jid} in client.requests
    assert (lane.run_dir / "drafting-jobs" / jid / "job.json").is_file()


def test_the_wake_asks_for_deliver_mode_and_carries_no_client_text(tmp_path):
    lane, client = _lane(tmp_path)
    jid = _submit(lane, client)
    lane.tick()
    task = lane._daemon_state(jid)["wake"]["task"]
    assert task.splitlines()[0] == f"Run the document-drafter skill's DELIVER mode for drafting job {jid}."
    for line in (
        "Kind: drafting.",
        "Document class: mediation_brief.",
        "Outcome: delivered.",
        "Spend cents: 1250.",
        "Matter number: 100001.",
        "Folder id: folder-9.",
        "Files: draft (52000 bytes); attorney_notes (9000 bytes).",
        "Requested by: admin@firm.example.",
        "Caption discrepancies: 1.",
        "Caption corrections: 1.",
    ):
        assert line in task
    assert "Gamma Example" not in task and "CV-0001" not in task
    assert "GAMMA EXAMPLE" not in task and "Gamma Exampel" not in task  # a correction's names never ride the wake


def test_a_failed_job_records_failed_and_wakes_with_the_stage_only(tmp_path):
    lane, client = _lane(tmp_path, script=FAIL_RUNNER)
    jid = _submit(lane, client)
    assert lane.tick() == "failed"
    assert "Secret Client" in client.records[-1][2]["reason"]
    task = lane._daemon_state(jid)["wake"]["task"]
    assert "Reason: stopped at gate." in task and "Secret Client" not in task


def test_a_schema_miss_defers_drafting_jobs_and_backs_off(tmp_path):
    lane, client = _lane(tmp_path, inputs=False)
    jid = _submit(lane, client)
    assert lane.tick() == "deferred"
    assert client.records == []
    assert lane._daemon_state(jid)["retry_after"] > 1_000_000.0


def test_no_queue_env_no_lane(monkeypatch, tmp_path):
    monkeypatch.delenv("SMD_DRAFTING_QUEUE_DIR", raising=False)

    class D:
        pass

    assert build_lane(D()) is None  # type: ignore[arg-type]


def test_the_daemon_starts_the_drafting_lane_beside_the_demand_lane_and_survives_its_failure(monkeypatch):
    src = (Path(__file__).resolve().parents[1] / "medchron" / "daemon.py").read_text()
    demand = src.index("start_lane(d, stop=")
    assert "start_other_lanes(d, stop=" in src[demand : demand + 400]
    from medchron import drafting_lane, lanes, litigation_lane, negotiation_lane

    assert lanes.LANES == ("drafting_lane", "litigation_lane", "negotiation_lane")
    assert lanes._module("drafting_lane") is drafting_lane and lanes._module("negotiation_lane") is negotiation_lane
    assert lanes._module("litigation_lane") is litigation_lane
    started: list[str] = []

    def boom(*_a, **_k):
        raise RuntimeError("drafting lane cannot start")

    monkeypatch.setattr(drafting_lane, "start_lane", boom)
    monkeypatch.setattr(litigation_lane, "start_lane", lambda *_a, **_k: started.append("litigation"))
    monkeypatch.setattr(negotiation_lane, "start_lane", lambda *_a, **_k: started.append("negotiation"))
    lanes.start_other_lanes(object(), stop=lambda: True, poll_seconds=1.0)
    assert started == ["litigation", "negotiation"]  # one lane failing to start stops neither other


def test_unusable_inputs_defer_once_then_fail_with_config_missing(tmp_path):
    lane, client = _lane(tmp_path, inputs=False)
    jid = _submit(lane, client)
    assert lane.tick() == "deferred"
    assert client.records == []
    lane._write_state(jid, retry_after=0)
    assert lane.tick() == "failed"
    state, fields = client.records[-1][1], client.records[-1][2]
    assert state == "failed" and fields["reason"].startswith("config_missing: ")
    task = lane._daemon_state(jid)["wake"]["task"]
    assert "Outcome: failed." in task and "Reason: stopped at config." in task


def test_a_running_demand_child_makes_drafting_wait_without_backoff(tmp_path):
    lane, client = _lane(tmp_path)
    jid = _submit(lane, client)
    (lane.run_dir / "demand-child.pid").write_text("123")
    assert lane.tick() == "waiting"
    st = lane._daemon_state(jid)
    assert st["retry_after"] == 1_000_000.0 + 30.0 and not st.get("deferrals")
    assert client.records == []


def test_the_drafting_child_joins_the_demand_lanes_memory_cgroup(tmp_path, monkeypatch):
    import medchron.drafting_lane as dl

    lane, _client = _lane(tmp_path)
    monkeypatch.setattr(dl, "memory_cap_mode", lambda _root: "cgroup2")
    lane.cgroup_root.mkdir(parents=True, exist_ok=True)
    pre = lane._cgroup_preexec()
    assert pre is not None
    assert (lane.cgroup_root / "demand").is_dir() and not (lane.cgroup_root / "drafting").exists()
