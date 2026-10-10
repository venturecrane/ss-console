"""The litigation status job's broker verbs (litigation_verbs.py): both submit
forms and their peer pins, each submit check in order, the runner's record and
its counts-only audit, the monthly spend on root's status, and the resume
marker. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.litigation_ledger import LitigationLedger
from workspace_broker.litigation_verbs import LitigationVerbs, month_window
from workspace_broker.server import Broker

GATEWAY_PID = 4242
CRON_PID = 5151
AGENT_UID = 10000
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
ATTORNEY_A = "11111111-2222-3333-4444-555555555555"
ATTORNEY_B = "66666666-7777-8888-9999-000000000000"
ADMIN = "admin@firm.example"
REF = "<CA1x2y3z@mail.firm.example>"
FIXED_NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)  # 07:00 Pacific, Oct 8

YAML = """
scope:
  admins:
    - Admin@Firm.example
    - second@firm.example
personas:
  - slug: operator
    skills:
      - name: litigation-status
        initiation:
          manual: {manual}
          scheduled: {scheduled}
          webhook: false
        enabled: {enabled}
        settings:
          folder_name: 'Litigation Status'
          file_to_matter_id: '{library}'
          scheduled_recipients: '{recipients}'
    cron:{cron}
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-OPERATOR-LIBRARY'
"""
LIVE_CRON = """
      - skill: litigation-status
        schedule: '37 6 * * 1-5'
        pre_run: pre_run.py
        wake_policy: pre_run_decides"""
FIRM = """
monthly_budget_usd: {budget}
per_job_cap_usd: 500
attorneys:
  Avery Stone: '{a}'
  Blake Stone: '{b}'
""".format(budget="{budget}", a=ATTORNEY_A, b=ATTORNEY_B)


def _yaml(manual="true", scheduled="true", enabled="true", recipients=ADMIN, cron=" []", library=LIBRARY) -> str:
    return YAML.format(
        manual=manual, scheduled=scheduled, enabled=enabled, recipients=recipients, cron=cron, library=library
    )


def _env(**over):
    env = {
        "trigger": "request",
        "requester": ADMIN,
        "message_ref": REF,
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
    }
    env.update(over)
    return env


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml())
    firm = tmp_path / "litigation-broker-firm.yaml"
    firm.write_text(FIRM.format(budget=750))
    queue = tmp_path / "litigation-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.litigation = LitigationVerbs(
        LitigationLedger(db, queue),
        customer_yaml=str(yaml_path),
        audit_append=writer.append,
        firm_config_path=str(firm),
        now=lambda: FIXED_NOW,
    )
    return broker, db, yaml_path, queue, firm


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def submit(broker, *, pid=GATEWAY_PID, uid=AGENT_UID, **over):
    return call(broker, "litigation_job_submit", pid=pid, uid=uid, envelope=_env(**over))


def scheduled(broker, *, pid=CRON_PID, uid=AGENT_UID):
    return call(broker, "litigation_job_submit", pid=pid, uid=uid, envelope={"trigger": "scheduled"})


def audit_rows(db: str) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(db)
    try:
        return [
            (r[0], json.loads(r[1] or "{}"))
            for r in conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid")
        ]
    finally:
        conn.close()


def queued(queue: Path) -> list[Path]:
    return sorted(queue.glob("*.json")) if queue.exists() else []


# -- the request form ---------------------------------------------------------


def test_an_admin_request_is_queued_in_the_runners_shape_and_audited(seat) -> None:
    broker, db, _, queue, _ = seat
    out = submit(broker)
    assert out["accepted"] is True and out["state"] == "queued"
    (job_file,) = queued(queue)
    job = json.loads(job_file.read_text())
    assert job == {
        "kind": "litigation",
        "job_id": out["job_id"],
        "trigger": "request",
        "requester": ADMIN,
        "message_ref": REF,
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
        "file_to_matter_id": LIBRARY,
        "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
        "folder_name": "Litigation Status",
    }
    ((kind, meta),) = audit_rows(db)
    assert kind == "LITIGATION_JOB_SUBMITTED"
    assert meta["job_id"] == out["job_id"] and meta["trigger"] == "request"
    assert "request_text" not in meta


def test_named_attorneys_resolve_to_staff_ids_by_full_or_unique_name(seat) -> None:
    broker, _, _, queue, _ = seat
    out = submit(broker, scope={"attorneys": ["avery stone"]})
    assert out["accepted"] is True
    assert json.loads(queued(queue)[0].read_text())["scope"] == {"attorney_staff_ids": [ATTORNEY_A]}
    out = submit(broker, scope={"attorneys": ["Blake"]}, message_ref="<two@mail.firm.example>")
    assert out["accepted"] is True


def test_an_ambiguous_or_unknown_attorney_is_refused(seat) -> None:
    broker, _, _, queue, _ = seat
    out = submit(broker, scope={"attorneys": ["Stone"]})
    assert out["accepted"] is False and "more than one" in out["reason"]
    out = submit(broker, scope={"attorneys": ["Nobody"]})
    assert out["accepted"] is False and "not an attorney" in out["reason"]
    assert queued(queue) == []


def test_a_turn_may_not_name_staff_ids_but_root_may(seat) -> None:
    broker, _, _, _, _ = seat
    with pytest.raises(PermissionError):
        submit(broker, scope={"attorney_staff_ids": [ATTORNEY_A]})
    out = submit(broker, pid=1, uid=0, scope={"attorney_staff_ids": [ATTORNEY_A]})
    assert out["accepted"] is True


def test_a_request_from_a_bare_agent_uid_is_refused(seat) -> None:
    broker, _, _, _, _ = seat
    with pytest.raises(PermissionError):
        submit(broker, pid=CRON_PID)


def test_an_unlisted_or_disabled_skill_refuses(seat) -> None:
    broker, _, yaml_path, queue, _ = seat
    yaml_path.write_text(_yaml(enabled="false"))
    out = submit(broker)
    assert out["accepted"] is False and "not enabled" in out["reason"]
    assert queued(queue) == []


def test_manual_initiation_off_refuses_a_request(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(manual="false"))
    out = submit(broker)
    assert out["accepted"] is False and "on request" in out["reason"]


def test_a_non_admin_requester_is_refused(seat) -> None:
    broker, _, _, queue, _ = seat
    out = submit(broker, requester="paralegal@firm.example")
    assert out["accepted"] is False and "Named Administrators" in out["reason"]
    assert queued(queue) == []


def test_an_unauthored_filing_target_refuses(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(library=""))
    out = submit(broker)
    assert out["accepted"] is False and "filed" in out["reason"]


def test_no_firm_budget_refuses(seat) -> None:
    broker, _, _, _, firm = seat
    firm.unlink()
    out = submit(broker)
    assert out["accepted"] is False and "budget is not authored" in out["reason"]


def test_a_spent_month_refuses(seat) -> None:
    broker, _, _, _, firm = seat
    firm.write_text(FIRM.format(budget=1))
    job = submit(broker)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="failed", fields={"cents": 100})
    out = submit(broker, message_ref="<again@mail.firm.example>")
    assert out["accepted"] is False and "budget is spent" in out["reason"]


def test_the_same_scope_twice_and_the_same_email_twice_refuse(seat) -> None:
    broker, _, _, queue, _ = seat
    first = submit(broker)["job_id"]
    out = submit(broker, message_ref="<other@mail.firm.example>")
    assert out["accepted"] is False and out["job_id"] == first
    out = submit(broker, scope={"attorneys": ["Avery Stone"]})
    assert out["accepted"] is False and out["job_id"] == first
    assert len(queued(queue)) == 1


def test_an_extra_envelope_key_refuses(seat) -> None:
    broker, _, _, _, _ = seat
    env = _env()
    env["file_to_matter_id"] = LIBRARY
    out = call(broker, "litigation_job_submit", envelope=env)
    assert out["accepted"] is False and "exactly" in out["reason"]


# -- the scheduled form -------------------------------------------------------


def test_a_scheduled_run_from_the_gateway_is_refused(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(cron=LIVE_CRON))
    with pytest.raises(PermissionError):
        scheduled(broker, pid=GATEWAY_PID)


def test_a_scheduled_run_from_the_cron_needs_the_row_live(seat) -> None:
    broker, _, yaml_path, queue, _ = seat
    out = scheduled(broker)
    assert out["accepted"] is False and "not enabled" in out["reason"]
    assert queued(queue) == []
    yaml_path.write_text(_yaml(cron=LIVE_CRON))
    out = scheduled(broker)
    assert out["accepted"] is True


def test_a_scheduled_run_fills_the_requester_and_the_pacific_date(seat) -> None:
    broker, _, yaml_path, queue, _ = seat
    yaml_path.write_text(_yaml(cron=LIVE_CRON, recipients=f"{ADMIN}, second@firm.example"))
    out = scheduled(broker)
    job = json.loads(queued(queue)[0].read_text())
    assert job["trigger"] == "scheduled" and job["requester"] == ADMIN
    assert job["message_ref"] == "scheduled:2026-10-08" and job["scope"] == {"all": True}
    assert out["trigger"] == "scheduled"


def test_one_scheduled_job_per_pacific_date(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(cron=LIVE_CRON))
    first = scheduled(broker)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=first, state="held", fields={"reason": "scope_empty: x"})
    out = scheduled(broker)
    assert out["accepted"] is False and out["job_id"] == first


def test_root_may_run_a_scheduled_job_with_the_row_still_commented(seat) -> None:
    broker, _, _, _, _ = seat
    assert scheduled(broker, pid=1, uid=0)["accepted"] is True


def test_a_scheduled_recipient_who_is_not_an_admin_refuses(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(cron=LIVE_CRON, recipients="paralegal@firm.example"))
    out = scheduled(broker)
    assert out["accepted"] is False and "scheduled_recipients" in out["reason"]


def test_scheduled_initiation_off_refuses(seat) -> None:
    broker, _, yaml_path, _, _ = seat
    yaml_path.write_text(_yaml(cron=LIVE_CRON, scheduled="false"))
    out = scheduled(broker, pid=1, uid=0)
    assert out["accepted"] is False and "on a schedule" in out["reason"]


def test_a_scheduled_envelope_names_nothing_else(seat) -> None:
    broker, _, _, _, _ = seat
    out = call(broker, "litigation_job_submit", pid=1, uid=0, envelope={"trigger": "scheduled", "requester": ADMIN})
    assert out["accepted"] is False


# -- status, record, resume ---------------------------------------------------


def test_status_is_counts_only_and_root_sees_the_month(seat) -> None:
    broker, _, _, _, _ = seat
    job = submit(broker)["job_id"]
    root = {"pid": 1, "uid": 0}
    call(broker, "litigation_job_record", **root, job_id=job, state="running", fields={})
    files = [{"role": "workbook", "name": "status.xlsx", "size": 1234, "sha256": "a" * 64, "file_id": "f1"}]
    call(
        broker,
        "litigation_job_record",
        **root,
        job_id=job,
        state="delivered",
        fields={
            "cents": 4200,
            "stage": "report",
            "matters_total": 60,
            "matters_reread": 7,
            "flags_new": 3,
            "files": files,
            "folder_id": "fold",
        },
    )
    got = call(broker, "litigation_job_status", job_id=job)["job"]
    assert got["state"] == "delivered" and got["matters_total"] == 60 and got["flags_new"] == 3
    assert got["file"] == {"name": "status.xlsx", "size": 1234, "sha256": "a" * 64}
    assert "row" not in got and "request_ref" not in got
    listing = call(broker, "litigation_job_status")
    assert "month_cents" not in listing
    root_listing = call(broker, "litigation_job_status", **root)
    assert root_listing["month_cents"] == 4200 and root_listing["monthly_budget_cents"] == 75000


def test_the_record_audit_carries_counts_and_never_a_file_list(seat) -> None:
    broker, db, _, _, _ = seat
    job = submit(broker)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    call(
        broker,
        "litigation_job_record",
        pid=1,
        uid=0,
        job_id=job,
        state="held",
        fields={"reason": "gate_refused: a value cites no document", "matters_total": 4},
    )
    kinds = [k for k, _ in audit_rows(db)]
    assert kinds == ["LITIGATION_JOB_SUBMITTED", "LITIGATION_JOB_RUNNING", "LITIGATION_JOB_HELD"]
    assert audit_rows(db)[-1][1]["matters_total"] == 4


def test_record_refuses_an_unknown_field_and_an_illegal_move(seat) -> None:
    broker, _, _, _, _ = seat
    job = submit(broker)["job_id"]
    with pytest.raises(ValueError):
        call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={"matter": "x"})
    with pytest.raises(ValueError):
        call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="delivered", fields={})


def test_record_and_resume_are_root_only(seat) -> None:
    broker, _, _, _, _ = seat
    job = submit(broker)["job_id"]
    with pytest.raises(PermissionError):
        call(broker, "litigation_job_record", job_id=job, state="running", fields={})
    with pytest.raises(PermissionError):
        call(broker, "litigation_job_resume", job_id=job, reason="x")


def test_resume_writes_the_marker_for_a_failed_job_only(seat) -> None:
    broker, db, _, queue, _ = seat
    job = submit(broker)["job_id"]
    root = {"pid": 1, "uid": 0}
    with pytest.raises(ValueError):
        call(broker, "litigation_job_resume", **root, job_id=job, reason="fixed")
    call(broker, "litigation_job_record", **root, job_id=job, state="failed", fields={"reason": "unexpected: x"})
    with pytest.raises(ValueError):
        call(broker, "litigation_job_resume", **root, job_id=job, reason="")
    out = call(broker, "litigation_job_resume", **root, job_id=job, reason="the extractor is fixed")
    assert out["queued"] is True and (queue / f".resume-{job}.json").is_file()
    assert audit_rows(db)[-1][0] == "LITIGATION_JOB_RESUME_REQUESTED"
    # failed -> running bumps the attempt (the reply binding's key).
    call(broker, "litigation_job_record", **root, job_id=job, state="running", fields={})
    assert broker.litigation.ledger.read(job)["attempt"] == 2


def test_the_month_window_is_pacific() -> None:
    month, start, end = month_window(datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc))
    assert month == "2026-10"
    assert start == "2026-10-01T07:00:00.000Z" and end == "2026-11-01T07:00:00.000Z"
