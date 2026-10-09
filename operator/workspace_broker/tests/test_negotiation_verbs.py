"""The negotiation watch's broker verbs (negotiation_verbs.py): the scheduled
submit and its peer pins, each submit check, one job per cron slot and one at a
time, the runner's record writing one notice per offer, the notice's status
read, and the monthly spend on root's status."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.negotiation_ledger import NegotiationLedger
from workspace_broker.negotiation_verbs import NegotiationVerbs, slot_ref
from workspace_broker.server import Broker

GATEWAY_PID = 4242
CRON_PID = 5151
AGENT_UID = 10000
ADMIN = "admin@firm.example"
DESIGN = "f6719448-d924-4c59-9c1e-3bd2b76550ca"
FIXED_NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)  # 08:00 Pacific, Mon Oct 12

YAML = """
scope:
  admins:
    - Admin@Firm.example
smokeball_layouts:
  settlement_negotiations_design: {design}
personas:
  - slug: operator
    skills:
      - name: negotiation-watch
        initiation:
          manual: false
          scheduled: {scheduled}
          webhook: false
        enabled: true
        settings:
          scheduled_recipients: '{recipients}'
          monthly_budget_usd: {budget}
          per_job_cap_usd: 8
          firm_words: 'ashton,price'
          seed_saved_before: '{seed}'
    cron:{cron}
"""
LIVE = """
      - skill: negotiation-watch
        schedule: '0 8,12,16 * * 1-5'
        pre_run: pre_run.py
        wake_policy: pre_run_decides"""
NOTICE = {"matter_id": "m-1", "matter_number": "200123", "status": "entered", "text": "New offer on matter 200123."}


def _yaml(scheduled="true", recipients=ADMIN, budget="25", cron=LIVE, seed="2026-10-09T07:00:00-07:00") -> str:
    return YAML.format(design=DESIGN, scheduled=scheduled, recipients=recipients, budget=budget, cron=cron, seed=seed)


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml())
    queue = tmp_path / "negotiation-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.negotiation = NegotiationVerbs(
        NegotiationLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, now=lambda: FIXED_NOW
    )
    return broker, db, yaml_path, queue


def call(broker, action, *, pid=CRON_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def submit(broker, **kw):
    return call(broker, "negotiation_job_submit", envelope={"trigger": "scheduled"}, **kw)


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


def test_the_cron_submits_one_job_per_slot_with_the_authored_settings(seat):
    broker, db, _y, queue = seat
    out = submit(broker)
    assert out["accepted"] is True
    env = json.loads((queue / f"{out['job_id']}.json").read_text())
    assert env["kind"] == "negotiation" and env["requester"] == ADMIN
    assert env["message_ref"] == "scheduled:2026-10-12T08" == slot_ref(FIXED_NOW)
    assert env["negotiation_design"] == DESIGN and env["firm_words"] == ["ashton", "price"]
    assert env["monthly_budget_usd"] == 25.0 and env["per_job_cap_usd"] == 8.0
    assert env["seed_saved_before"] == "2026-10-09T14:00:00Z"  # normalized to UTC
    assert audit_types(db) == ["NEGOTIATION_JOB_SUBMITTED"]
    again = submit(broker)
    assert again["accepted"] is False and again["job_id"] == out["job_id"]


def test_one_job_at_a_time(seat):
    broker, _db, _y, _q = seat
    first = submit(broker)
    broker.negotiation._now = lambda: datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)
    out = submit(broker)
    assert out["accepted"] is False and "already underway" in out["reason"] and out["job_id"] == first["job_id"]


def test_a_turn_never_submits(seat):
    broker, _db, _y, _q = seat
    with pytest.raises(PermissionError):
        broker.negotiation.handle(
            "negotiation_job_submit", {"envelope": {"trigger": "scheduled"}}, AGENT_UID, from_gateway=True
        )
    with pytest.raises(PermissionError):
        call(broker, "negotiation_job_submit", pid=GATEWAY_PID, envelope={"trigger": "scheduled"})


def test_the_agent_uid_needs_the_live_cron_row_root_does_not(seat):
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(_yaml(cron=" []"))
    assert "not enabled" in submit(broker)["reason"]
    assert submit(broker, uid=0)["accepted"] is True


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"scheduled": "false"}, "not switched on"),
        ({"recipients": "someone@firm.example"}, "Named Administrators"),
        ({"budget": "0"}, "budget is not authored"),
        ({"seed": "the morning of the fill"}, "not a UTC timestamp"),
        ({"seed": "2026-10-09T14:00:00"}, "not a UTC timestamp"),
    ],
)
def test_each_submit_check_refuses(seat, over, needle):
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(_yaml(**over))
    out = submit(broker)
    assert out["accepted"] is False and needle in out["reason"]


def test_a_spent_month_refuses(seat):
    broker, _db, _y, _q = seat
    job = submit(broker)["job_id"]
    call(broker, "negotiation_job_record", uid=0, job_id=job, state="running", fields={})
    call(broker, "negotiation_job_record", uid=0, job_id=job, state="delivered", fields={"cents": 2500, "notices": []})
    broker.negotiation._now = lambda: datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
    assert "budget is spent" in submit(broker)["reason"]
    assert call(broker, "negotiation_job_status", uid=0)["month_cents"] == 2500


def test_a_delivery_writes_one_notice_per_offer_once(seat):
    broker, db, _y, _q = seat
    job = submit(broker)["job_id"]
    call(broker, "negotiation_job_record", uid=0, job_id=job, state="running", fields={})
    out = call(
        broker,
        "negotiation_job_record",
        uid=0,
        job_id=job,
        state="delivered",
        fields={"cents": 40, "docs_read": 2, "notices": [NOTICE, {**NOTICE, "status": "not_entered"}]},
    )
    ids = out["notice_ids"]
    assert len(ids) == 2 and len(set(ids)) == 2
    again = call(broker, "negotiation_job_record", uid=0, job_id=job, state="delivered", fields={"notices": [NOTICE]})
    assert again["notice_ids"] == ids
    status = call(broker, "negotiation_job_status", pid=GATEWAY_PID, job_id=ids[0])["job"]
    assert (
        status["kind"] == "notice"
        and status["message"] == NOTICE["text"]
        and status["subject"] == "New offer, matter 200123"
    )
    assert audit_types(db)[-1] == "NEGOTIATION_JOB_DELIVERED"


def test_a_notice_with_an_em_dash_is_refused(seat):
    broker, _db, _y, _q = seat
    job = submit(broker)["job_id"]
    call(broker, "negotiation_job_record", uid=0, job_id=job, state="running", fields={})
    with pytest.raises(Exception):
        broker.negotiation.handle(
            "negotiation_job_record",
            {"job_id": job, "state": "delivered", "fields": {"notices": [{**NOTICE, "text": "a — b"}]}},
            0,
        )


def test_record_and_resume_are_root_only(seat):
    broker, _db, _y, _q = seat
    job = submit(broker)["job_id"]
    with pytest.raises(PermissionError):
        call(broker, "negotiation_job_record", job_id=job, state="running", fields={})
    with pytest.raises(PermissionError):
        call(broker, "negotiation_job_resume", job_id=job, reason="x")
