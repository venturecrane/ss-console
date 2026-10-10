"""The daemon's litigation lane: its queue and verbs, the stamped job, the
wait behind a live demand or drafting child, the heartbeat during a job, the
wake text, and the CLI's one-line verdict."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from litigation_testkit import JOB, envelope, make_inputs, make_job
from medchron.litigation_lane import LitigationBroker, LitigationLane, read_verdict

RUNNER = """
import json, sys, pathlib, time
jd = pathlib.Path(sys.argv[1])
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'litigation' and job['month_cents_used'] == 4321, job
assert job['per_job_cap_usd'] == 50.0 and job['monthly_budget_usd'] == 100.0 and job['slug'] == 'example', job
time.sleep(float(job.get('sleep', 0)))
print('a library banner on stdout')
v = {"verdict": "delivered", "stage": "report", "cents": 1234, "matters_total": 12, "matters_reread": 3, "flags_new": 2,
     "files": [{"role": "workbook", "name": "Litigation status 2026-10-07 (Operator 0000AA).xlsx", "size": 20480, "sha256": "ab", "file_id": "up-1"}],
     "folder_id": "folder-7"}
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
"""
HELD = """
import json
print(json.dumps({"verdict": "held", "stage": "parity", "cents": 900, "matters_total": 4, "matters_reread": 1, "flags_new": 0,
                  "files": [], "folder_id": "", "reason": "parity_hold: 1 values changed for Gamma Example"}))
"""


class FakeClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a == "litigation_job_status":
            if "job_id" not in req:
                return {"ok": True, "month_cents": 4321}
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "litigation_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            return {"ok": True}
        raise AssertionError(f"the litigation lane speaks only litigation verbs, not {a}")


def _lane(tmp_path: Path, script: str = RUNNER, inputs: bool = True) -> tuple[LitigationLane, FakeClient]:
    run_dir = tmp_path / "run"
    q = run_dir / "litigation-queue"
    q.mkdir(parents=True)
    client = FakeClient()
    p = tmp_path / "runner.py"
    p.write_text(script)
    lane = LitigationLane(
        run_dir=run_dir,
        broker=LitigationBroker(client),
        runner_cmd=[sys.executable, str(p)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        inputs_dir=str(make_inputs(tmp_path / "inputs") if inputs else tmp_path / "missing"),
        state_dir=str(tmp_path / "state"),
    )
    return lane, client


def _submit(lane: LitigationLane, client: FakeClient, **extra) -> str:
    client.rows[JOB] = {"state": "queued"}
    (lane.queue / f"{JOB}.json").write_text(json.dumps({**envelope(), **extra}))
    return JOB


def test_the_lane_runs_a_job_and_records_the_counts(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    assert lane.tick() == "delivered"
    assert [s for _j, s, _f in client.records] == ["running", "delivered"]
    f = client.records[-1][2]
    assert f["cents"] == 1234 and f["matters_total"] == 12 and f["matters_reread"] == 3 and f["flags_new"] == 2
    assert f["folder_id"] == "folder-7" and f["files"][0]["role"] == "workbook" and "reason" not in f
    assert (lane.run_dir / "litigation-jobs" / JOB / "job.json").is_file()
    assert not (lane.run_dir / "litigation-child.pid").exists() and (lane.run_dir / "litigation-tick").exists()


def test_the_wake_is_the_interface_text_with_counts_only(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    lane.tick()
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert task.splitlines() == [
        f"Run the litigation-status skill's DELIVER mode for litigation job {JOB}.",
        "Kind: litigation.",
        "Trigger: request.",
        "Outcome: delivered.",
        "Matters: 12; re-read this run: 3; new flags: 2.",
        "Folder id: folder-7.",
        "Files: workbook (20480 bytes).",
        "Requested by: admin@firm.example.",
    ]
    assert "Litigation status" not in task


def test_a_held_job_wakes_with_the_stage_never_the_reason(tmp_path):
    lane, client = _lane(tmp_path, script=HELD)
    _submit(lane, client)
    assert lane.tick() == "held"
    assert client.records[-1][2]["reason"].startswith("parity_hold: ")
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert "Outcome: held." in task and "Reason: stopped at parity." in task and "Gamma" not in task


def test_the_lane_waits_behind_a_live_demand_or_drafting_child(tmp_path):
    lane, client = _lane(tmp_path)
    _submit(lane, client)
    for name in ("demand-child.pid", "drafting-child.pid"):
        (lane.run_dir / name).write_text(str(os.getpid()))  # a live process
        assert lane.tick() == "waiting" and client.records == []
        assert lane._daemon_state(JOB)["retry_after"] == 1_000_030.0
        lane._write_state(JOB, retry_after=None)
        (lane.run_dir / name).unlink()
    (lane.run_dir / "demand-child.pid").write_text("999999999")  # stale: no such process
    lane._write_state(JOB, retry_after=None)
    assert lane.tick() == "delivered"


def test_the_heartbeat_keeps_the_tick_fresh_during_a_long_job(tmp_path, monkeypatch):
    lane, client = _lane(tmp_path)
    lane.heartbeat_seconds = 0.05
    beats = []
    real = lane.heartbeat
    monkeypatch.setattr(lane, "heartbeat", lambda running=None: (beats.append(running), real(running=running)))
    _submit(lane, client, sleep=0.6)
    assert lane.tick() == "delivered"
    assert beats.count(JOB) >= 4  # the tick's own beat plus the thread's during the child


def test_unusable_inputs_defer_once_then_fail_config_missing(tmp_path):
    lane, client = _lane(tmp_path, inputs=False)
    _submit(lane, client)
    assert lane.tick() == "deferred" and client.records == []
    lane._write_state(JOB, retry_after=None)
    assert lane.tick() == "failed"
    assert client.records[-1][1] == "failed" and client.records[-1][2]["reason"].startswith("config_missing: ")


def test_read_verdict_prefers_the_file_and_ignores_noise(tmp_path):
    (tmp_path / "verdict.json").write_text(json.dumps({"verdict": "held", "stage": "gates"}))
    assert read_verdict(tmp_path, '{"verdict": "delivered"}')["verdict"] == "held"
    (tmp_path / "verdict.json").unlink()
    assert read_verdict(tmp_path, 'banner\n{"verdict": "failed", "stage": "x"}\n')["verdict"] == "failed"
    assert read_verdict(tmp_path, "banner only") is None


def test_the_cli_prints_one_verdict_line_and_exits_by_outcome(tmp_path):
    jd = make_job(tmp_path / "job", job_id="bad")
    env = {k: v for k, v in os.environ.items() if k != "MEDCHRON_LITIGATION_INPUTS"}
    r = subprocess.run(  # noqa: S603 - the test interpreter running this repo's own CLI on a temp dir
        [sys.executable, "-m", "medchron", "litigate", str(jd), "--inputs", str(make_inputs(tmp_path / "in"))],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, r.stdout
    v = json.loads(lines[0])
    assert v["verdict"] == "failed" and v["reason"].startswith("envelope_invalid: ") and r.returncode == 2
    assert json.loads((jd / "verdict.json").read_text()) == v


def test_the_cli_routes_a_runs_own_stdout_to_stderr(tmp_path, monkeypatch, capsys):
    from medchron import __main__ as cli
    from medchron.litigation import run as run_mod
    from medchron.litigation.outcome import Verdict

    def noisy(self):
        print("progress that must not reach stdout")
        return Verdict("held", stage="gates", reason="gate_refused: x")

    monkeypatch.setattr(run_mod.LitigationRun, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(run_mod.LitigationRun, "reopen", lambda self, s: None)
    monkeypatch.setattr(run_mod.LitigationRun, "run", noisy)
    jd = tmp_path / "j"
    jd.mkdir()
    assert cli.main(["litigate", str(jd)]) == 1
    out = capsys.readouterr()
    assert out.out.strip().count("\n") == 0 and json.loads(out.out)["verdict"] == "held"
    assert "progress that must not reach stdout" in out.err
