"""The litigation lane's billing-cycle list allowance (2026-10-24): a status
list past the cycle's included lists is HELD when it starts, before the child
runs and before anything is spent, with a ``list_allowance:`` reason that pages
SMD (``limit_held``) and a DELIVER wake carrying ``Hold class: smd_finishing.``
so the firm hears the lane's fixed wording. Every name here is invented."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from litigation_testkit import JOB, envelope, make_inputs
from medchron.litigation_lane import LitigationBroker, LitigationLane

#: A child that delivers. The lane only reaches it when the list may start.
DELIVERS = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
v = {"verdict": "delivered", "stage": "report", "cents": 1234, "matters_total": 3, "matters_reread": 1, "flags_new": 0,
     "files": [{"role": "workbook", "name": "Litigation status (Operator 0000AA).xlsx", "size": 2048, "sha256": "ab" * 32, "file_id": "up-1"}],
     "folder_id": "folder-7", "proof": {"readback": "confirmed", "files": [{"name": "x.xlsx", "file_id": "f1", "size": 10, "sha256": "a" * 64}], "review": "passed", "review_cents": 0}}
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
"""
#: A child that must never run: a held list spends nothing.
NEVER = "import sys\nsys.exit('the child ran for a list the allowance held')\n"

AT_CAP = {
    "authored": True, "readable": True, "unit": "lists", "allowance": 40, "used": 40, "remaining": 0,
    "cycle": "the cycle ending Nov 14", "cycle_start": "2026-10-15T00:00:00.000Z", "cycle_end": "2026-11-15T00:00:00.000Z",
}  # fmt: skip
UNDER = {**AT_CAP, "used": 39, "remaining": 1}


class FakeClient:
    def __init__(self, allowance: Any, row: dict | None = None) -> None:
        self.allowance = allowance
        self.row = row or {}
        self.records: list[tuple[str, str, dict]] = []
        self.alerts: list[str] = []

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a == "litigation_job_status":
            if "job_id" not in req:
                out: dict[str, Any] = {"ok": True, "month_cents": 0}
                if self.allowance is not None:
                    out["list_allowance"] = self.allowance
                return out
            return {"ok": True, "job": {"state": "queued", "row": self.row}}
        if a == "litigation_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            return {"ok": True}
        if a == "delivery_proof_alert":
            self.alerts.append(req["code"])
            return {"ok": True}
        if a.startswith("delivery_"):
            return {"ok": True, "replies": []}
        raise AssertionError(f"unexpected verb {a}")


def _lane(tmp_path: Path, client: Any, script: str, child_env: dict | None = None) -> LitigationLane:
    run_dir = tmp_path / "run"
    q = run_dir / "litigation-queue"
    q.mkdir(parents=True, exist_ok=True)
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
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        state_dir=str(tmp_path / "state"),
    )
    if child_env:
        lane.child_env = {**lane.child_env, **child_env}
    return lane


def _queue(lane: LitigationLane, job_id: str = JOB, **extra: Any) -> None:
    (lane.queue / f"{job_id}.json").write_text(json.dumps({**envelope(job_id), **extra}))


def _claimed(lane: LitigationLane, **extra: Any) -> None:
    """The job as the lane holds it once claimed: its envelope in the job dir."""
    jd = lane.job_dir(JOB)
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "envelope.json").write_text(json.dumps({**envelope(JOB), **extra}))


def test_a_list_under_the_allowance_runs(tmp_path):
    client = FakeClient(UNDER)
    lane = _lane(tmp_path, client, DELIVERS)
    _queue(lane)
    assert lane.tick() == "delivered"
    assert [s for _j, s, _f in client.records] == ["running", "delivered"]


def test_a_list_at_the_allowance_holds_without_running_or_spending(tmp_path):
    client = FakeClient(AT_CAP)
    lane = _lane(tmp_path, client, NEVER)
    _queue(lane)
    assert lane.tick() == "held"
    assert [s for _j, s, _f in client.records] == ["running", "held"]
    held = client.records[-1][2]
    assert held["cents"] == 0 and held["stage"] == "allowance"
    assert held["reason"].startswith("list_allowance: 40 of the 40 status lists included in the cycle ending Nov 14")
    assert client.alerts == ["limit_held"]  # SMD is paged to quote it
    assert not (lane.run_dir / "litigation-jobs" / JOB / "job.json").exists()  # the child was never staged
    task = lane._daemon_state(JOB)["wake"]["task"]
    assert "Outcome: held." in task and "Reason: stopped at allowance." in task
    assert "Hold class: smd_finishing." in task and "40" not in task.split("Outcome")[1]


def test_an_unset_allowance_never_holds(tmp_path):
    client = FakeClient({"authored": False})
    lane = _lane(tmp_path, client, DELIVERS)
    _queue(lane)
    assert lane.tick() == "delivered"


def test_an_unreadable_allowance_holds(tmp_path):
    client = FakeClient({"authored": True, "readable": False, "problem": "litigation_list_allowance_per_cycle: x"})
    lane = _lane(tmp_path, client, NEVER)
    _queue(lane)
    assert lane.tick() == "held"
    assert client.records[-1][2]["reason"].startswith("list_allowance: the authored list allowance")


@pytest.mark.parametrize("report", ["deadlines", "reassign"])
def test_a_deadlines_report_or_a_reassignment_is_not_a_status_list(tmp_path, report):
    client = FakeClient(AT_CAP)
    lane = _lane(tmp_path, client, DELIVERS)
    _claimed(lane)
    assert lane._allowance_reason(JOB) is not None  # the same job as a status list holds
    _claimed(lane, report=report)
    assert lane._allowance_reason(JOB) is None


def test_a_list_smd_quoted_runs_past_the_allowance(tmp_path):
    client = FakeClient(AT_CAP, row={"over_allowance_ref": "vfy_01M5ABCDEFGHJKMNPQRSTVWXYZ"})
    lane = _lane(tmp_path, client, DELIVERS)
    _queue(lane)
    assert lane.tick() == "delivered"


def test_a_proof_runs_twin_is_never_held_by_the_allowance(tmp_path):
    client = FakeClient(AT_CAP)
    lane = _lane(tmp_path, client, DELIVERS, child_env={"MEDCHRON_PROOF_RUN": "1"})
    _claimed(lane)
    assert lane._allowance_reason(JOB) is None
    lane.child_env = {k: v for k, v in lane.child_env.items() if k != "MEDCHRON_PROOF_RUN"}
    assert lane._allowance_reason(JOB) is not None  # the same job, not a proof run, holds


def test_a_broker_that_cannot_say_defers_and_records_nothing(tmp_path):
    client = FakeClient(None)
    lane = _lane(tmp_path, client, NEVER)
    _queue(lane)
    assert lane.tick() == "deferred" and client.records == []


# ---- end to end: the real broker ledger and verbs behind the lane ----------

_NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
_YAML = """
scope:
  admins: [admin@firm.example]
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
        settings:
          chronology_package_cycle_anchor_day: 15
      - name: litigation-status
        initiation: {manual: true, scheduled: true, webhook: false}
        enabled: true
        settings:
          folder_name: 'Litigation Status'
          file_to_matter_id: '99999999-9999-4999-8999-999999999999'
          scheduled_recipients: 'admin@firm.example'
          litigation_list_allowance_per_cycle: 40
    cron: []
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-LIBRARY'
"""


class RootClient:
    """The daemon's broker socket, as root, onto the real verbs."""

    def __init__(self, verbs: Any) -> None:
        self.verbs = verbs
        self.alerts: list[str] = []

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a.startswith("litigation_"):
            return self.verbs.handle(a, {k: v for k, v in req.items() if k != "action"}, 0)
        if a == "delivery_proof_alert":
            self.alerts.append(req["code"])
            return {"ok": True}
        if a.startswith("delivery_"):
            return {"ok": True, "replies": []}
        raise AssertionError(f"unexpected verb {a}")


def _real_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from workspace_broker import litigation_ledger
    from workspace_broker.litigation_ledger import LitigationLedger
    from workspace_broker.litigation_verbs import LitigationVerbs

    monkeypatch.setattr(litigation_ledger, "_iso_utc", lambda: "2026-10-20T15:00:00.000Z")
    y = tmp_path / "customer.yaml"
    y.write_text(_YAML)
    firm = tmp_path / "broker-firm.yaml"
    firm.write_text("monthly_budget_usd: 750\nper_job_cap_usd: 500\n")
    ledger = LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "run" / "litigation-queue")
    verbs = LitigationVerbs(
        ledger, customer_yaml=str(y), audit_append=lambda row: None, firm_config_path=str(firm), now=lambda: _NOW
    )
    return ledger, verbs


def _submit(verbs: Any, n: int) -> str:
    env = {
        "trigger": "request",
        "requester": "admin@firm.example",
        "message_ref": f"<list-{n}@firm.example>",
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
    }
    out = verbs.handle("litigation_job_submit", {"envelope": env}, 0)
    assert out["accepted"], out
    return out["job_id"]


def test_end_to_end_the_40th_list_runs_and_the_41st_holds(tmp_path, monkeypatch):
    ledger, verbs = _real_broker(tmp_path, monkeypatch)
    # 39 lists already delivered this cycle, plus a held and a failed one that never count
    for n in range(39):
        jid = _submit(verbs, n)
        ledger.record(jid, "running", {})
        ledger.record(jid, "delivered", {"cents": 100})
        (ledger.queue_dir / f"{jid}.json").unlink()
    for n, ending in ((100, "held"), (101, "failed")):
        jid = _submit(verbs, n)
        ledger.record(jid, "running", {})
        ledger.record(jid, ending, {"reason": "gate_refused: x"})
        (ledger.queue_dir / f"{jid}.json").unlink()
    client = RootClient(verbs)
    lane = _lane(tmp_path, client, DELIVERS)
    fortieth = _submit(verbs, 40)
    assert lane.tick() == "delivered"
    assert ledger.read(fortieth)["state"] == "delivered"
    (tmp_path / "runner.py").write_text(NEVER)  # from here on, a child that runs fails the test
    forty_first = _submit(verbs, 41)
    assert lane.tick() == "held"
    row = ledger.read(forty_first)
    assert row["state"] == "held" and row["cents"] == 0 and row["reason"].startswith("list_allowance: 40 of the 40")
    assert client.alerts == ["limit_held"]
    assert verbs.handle("litigation_job_status", {"job_id": forty_first}, 0)["job"]["hold_class"] == "smd_finishing"


def test_end_to_end_a_quoted_list_runs_past_the_allowance(tmp_path, monkeypatch):
    ledger, verbs = _real_broker(tmp_path, monkeypatch)
    for n in range(40):
        jid = _submit(verbs, n)
        ledger.record(jid, "running", {})
        ledger.record(jid, "delivered", {"cents": 100})
        (ledger.queue_dir / f"{jid}.json").unlink()
    lane = _lane(tmp_path, RootClient(verbs), DELIVERS)
    env = {
        "trigger": "request",
        "requester": "admin@firm.example",
        "message_ref": "<quoted@firm.example>",
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
    }
    out = verbs.handle(
        "litigation_job_submit", {"envelope": env, "over_allowance_ref": "vfy_01M5ABCDEFGHJKMNPQRSTVWXYZ"}, 0
    )
    assert lane.tick() == "delivered" and ledger.read(out["job_id"])["state"] == "delivered"
