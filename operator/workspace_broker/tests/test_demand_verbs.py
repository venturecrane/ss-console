"""The demand job's broker verbs (demand_verbs.py): each submit check, the
peer gates, the runner's record, the cents the runner budgets against, and the
resume marker. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.demand_verbs import DemandVerbs
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
OTHER_MATTER = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
ADMIN = "admin@firm.example"

YAML = """
scope:
  admins:
    - Admin@Firm.example
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
        settings:
          chronology_package_page_allowance_per_month: 1000
      - name: demand-letter-drafter
        enabled: true
        settings:
          demand_allowance_per_cycle: {allowance}
"""
NO_SKILL_YAML = """
scope:
  admins:
    - admin@firm.example
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
"""


def _envelope(**over):
    env = {
        "matter": {"id": MATTER, "number": "900201"},
        "file_to": None,
        "requested_by": ADMIN,
        "request_ref": "<CA1x2y3z@mail.firm.example>",
        "request_text": "Gap audit and draft demand, please.",
        "deliverables": ["gap_audit", "demand"],
    }
    env.update(over)
    return env


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(YAML.format(allowance=25))
    queue = tmp_path / "demand-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.demand = DemandVerbs(DemandLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append)
    return broker, db, yaml_path, queue


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def submit(broker, **over):
    return call(broker, "demand_job_submit", envelope=_envelope(**over))


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# -- submit -----------------------------------------------------------------


def test_an_admin_request_is_queued_and_audited(seat) -> None:
    broker, db, _, queue = seat
    out = submit(broker)
    assert out["accepted"] is True
    assert (queue / f"{out['job_id']}.json").is_file()
    assert audit_types(db) == ["DEMAND_JOB_SUBMITTED"]
    # The audit row never carries the request text.
    conn = sqlite3.connect(db)
    meta = conn.execute("SELECT metadata FROM audit_log").fetchone()[0]
    conn.close()
    assert "Gap audit and draft demand, please." not in meta


def test_a_seat_without_the_skill_queues_nothing(seat) -> None:
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(NO_SKILL_YAML)
    out = submit(broker)
    assert out["accepted"] is False and "not enabled" in out["reason"]
    assert not queue.exists() or not list(queue.glob("*.json"))


def test_an_unauthored_allowance_refuses(seat) -> None:
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(
        YAML.replace("          demand_allowance_per_cycle: {allowance}\n", "").replace('settings:\n"""', "")
    )
    out = submit(broker)
    assert out["accepted"] is False and "demand_allowance_per_cycle" in out["reason"]


def test_a_non_admin_requester_refuses(seat) -> None:
    broker, *_ = seat
    out = submit(broker, requested_by="staff@firm.example")
    assert out["accepted"] is False and "Named Administrators" in out["reason"]


def test_a_second_job_on_the_same_matter_refuses(seat) -> None:
    broker, *_ = seat
    first = submit(broker)
    out = submit(broker)
    assert out["accepted"] is False and out["job_id"] == first["job_id"]
    assert submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})["accepted"] is True


def test_a_spent_allowance_refuses(seat) -> None:
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(YAML.format(allowance=1))
    first = submit(broker)
    # Only a job that paid counts against the allowance.
    call(broker, "demand_job_record", uid=0, job_id=first["job_id"], state="running", fields={"cents": 500})
    out = submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})
    assert out["accepted"] is False and "spent" in out["reason"]


def test_a_malformed_envelope_refuses_by_field(seat) -> None:
    broker, *_ = seat
    out = submit(broker, request_ref="not a message id")
    assert out["accepted"] is False and "request_ref" in out["reason"]


# -- gates --------------------------------------------------------------------


@pytest.mark.parametrize("action", ["demand_job_record", "demand_job_resume"])
def test_the_runner_verbs_are_root_only(seat, action) -> None:
    broker, *_ = seat
    with pytest.raises(PermissionError):
        call(broker, action, job_id="x", state="running", fields={}, reason="r")


def test_submit_is_not_reachable_from_an_agent_uid_child(seat) -> None:
    broker, *_ = seat
    with pytest.raises(PermissionError):
        call(broker, "demand_job_submit", pid=9999, envelope=_envelope())


def test_the_agent_sees_the_projection_and_root_the_row(seat) -> None:
    broker, *_ = seat
    job = submit(broker)["job_id"]
    agent = call(broker, "demand_job_status", pid=9999, job_id=job)["job"]
    root = call(broker, "demand_job_status", pid=9999, uid=0, job_id=job)["job"]
    assert "request_ref" not in agent and root["request_ref"] == "<CA1x2y3z@mail.firm.example>"


# -- record, allowance cents, resume -------------------------------------------


def test_record_moves_the_row_and_pins_the_audit_type(seat) -> None:
    broker, db, *_ = seat
    job = submit(broker)["job_id"]
    call(broker, "demand_job_record", uid=0, job_id=job, state="running", fields={})
    out = call(
        broker,
        "demand_job_record",
        uid=0,
        job_id=job,
        state="delivered",
        fields={"cents": 712, "folder_id": "F1", "delivery": {"files": [{"name": "Demand.X.docx", "size": 10}]}},
    )
    assert out["job"]["state"] == "delivered" and out["job"]["files"][0]["name"] == "Demand.X.docx"
    assert audit_types(db)[-2:] == ["DEMAND_JOB_RUNNING", "DEMAND_JOB_DELIVERED"]
    with pytest.raises(ValueError):
        call(broker, "demand_job_record", uid=0, job_id=job, state="running", fields={})


def test_allowance_reports_cents_used_without_the_excluded_job(seat) -> None:
    broker, *_ = seat
    a = submit(broker)["job_id"]
    b = submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})["job_id"]
    call(broker, "demand_job_record", uid=0, job_id=a, state="running", fields={"cents": 300})
    call(broker, "demand_job_record", uid=0, job_id=b, state="running", fields={"cents": 450})
    full = call(broker, "demand_allowance", uid=0)
    assert full["cents_used"] == 750 and full["used"] == 2 and full["remaining"] == 23
    assert call(broker, "demand_allowance", uid=0, exclude_job_id=b)["cents_used"] == 300


def test_resume_writes_the_marker_for_a_failed_job_only(seat) -> None:
    broker, _db, _y, queue = seat
    job = submit(broker)["job_id"]
    with pytest.raises(ValueError):
        call(broker, "demand_job_resume", uid=0, job_id=job, reason="fixed the cap")
    call(broker, "demand_job_record", uid=0, job_id=job, state="failed", fields={"reason": "cap"})
    with pytest.raises(ValueError):
        call(broker, "demand_job_resume", uid=0, job_id=job, reason="")
    assert call(broker, "demand_job_resume", uid=0, job_id=job, reason="cap raised")["queued"] is True
    marker = json.loads((queue / f".resume-{job}.json").read_text())
    assert marker == {"job_id": job, "reason": "cap raised"}


def test_an_unconfigured_broker_refuses(seat) -> None:
    broker, *_ = seat
    broker.demand = None
    with pytest.raises(ValueError):
        submit(broker)
