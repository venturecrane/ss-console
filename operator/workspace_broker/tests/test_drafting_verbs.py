"""The drafting job's broker verbs (drafting_verbs.py): each submit check in
order, the peer gates, the runner's record, the cents, and the resume marker.
Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.drafting_verbs import DraftingVerbs
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
OTHER_MATTER = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
ADMIN = "admin@firm.example"
ALL_CLASSES = "'mediation_brief, discovery_set, discovery_response, memo, depo_outline'"

YAML = """
scope:
  admins:
    - Admin@Firm.example
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
      - name: document-drafter
        enabled: {enabled}
        settings:
          drafting_allowance_per_cycle: {allowance}
          enabled_classes: {classes}
          rehearsal_matter_id: '1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23'
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-OPERATOR-LIBRARY'
"""
NO_SKILL_YAML = """
scope:
  admins:
    - admin@firm.example
personas:
  - slug: operator
    skills:
      - name: demand-letter-drafter
        enabled: true
        settings:
          demand_allowance_per_cycle: 25
"""


def _yaml(allowance=25, classes=ALL_CLASSES, enabled="true") -> str:
    return YAML.format(allowance=allowance, classes=classes, enabled=enabled)


def _envelope(**over):
    env = {
        "matter": {"id": MATTER, "number": "900201"},
        "file_to": None,
        "requested_by": ADMIN,
        "request_ref": "<CA1x2y3z@mail.firm.example>",
        "request_text": "Please draft the mediation brief.",
        "document_class": "mediation_brief",
    }
    env.update(over)
    return env


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml())
    queue = tmp_path / "drafting-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.drafting = DraftingVerbs(DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append)
    return broker, db, yaml_path, queue


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def submit(broker, **over):
    if "request_ref" not in over and "matter" in over:
        over["request_ref"] = f"<{over['matter']['number']}@mail.firm.example>"
    return call(broker, "drafting_job_submit", envelope=_envelope(**over))


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


def queued(queue: Path) -> list[Path]:
    return sorted(queue.glob("*.json")) if queue.exists() else []


# -- submit -----------------------------------------------------------------


def test_an_admin_request_is_queued_in_the_runners_shape_and_audited(seat) -> None:
    broker, db, _, queue = seat
    out = submit(broker)
    assert out["accepted"] is True and out["document_class"] == "mediation_brief"
    job = json.loads((queue / f"{out['job_id']}.json").read_text())
    assert job == {
        "kind": "drafting",
        "job_id": out["job_id"],
        "matter_id": MATTER,
        "matter_number": "900201",
        "document_class": "mediation_brief",
        "requester": ADMIN,
        "message_ref": "<CA1x2y3z@mail.firm.example>",
        "request_text": "Please draft the mediation brief.",
        "file_to_matter_id": None,
        "file_to_matter_number": None,
    }
    assert audit_types(db) == ["DRAFTING_JOB_SUBMITTED"]
    conn = sqlite3.connect(db)
    meta = conn.execute("SELECT metadata FROM audit_log").fetchone()[0]
    conn.close()
    assert "Please draft the mediation brief." not in meta
    assert json.loads(meta)["document_class"] == "mediation_brief"


def test_a_seat_without_the_skill_queues_nothing(seat) -> None:
    """FALSIFIER: drop the listed-and-enabled check and a seat that never
    switched the lane on queues paid work."""
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(NO_SKILL_YAML)
    out = submit(broker)
    assert out["accepted"] is False and "not enabled" in out["reason"]
    assert queued(queue) == []


def test_a_disabled_skill_queues_nothing(seat) -> None:
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(enabled="false"))
    out = submit(broker)
    assert out["accepted"] is False and "not enabled" in out["reason"]
    assert queued(queue) == []


def test_a_class_not_switched_on_is_refused_with_a_relayable_reason(seat) -> None:
    """FALSIFIER: drop the enabled_classes check and every class is on."""
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(classes="'mediation_brief,memo'"))
    out = submit(broker, document_class="depo_outline")
    assert out["accepted"] is False
    assert "A deposition outline isn't switched on for your firm" in out["reason"]
    assert submit(broker, document_class="memo")["accepted"] is True
    assert len(queued(queue)) == 1
    # A YAML list reads the same; an unknown name switches nothing on.
    yaml_path.write_text(_yaml(classes="[depo_outline, settlement_statement]"))
    assert submit(broker, document_class="depo_outline")["accepted"] is True
    assert submit(broker, document_class="mediation_brief", request_ref="<x2@m.firm.example>")["accepted"] is False


def test_unauthored_classes_switch_nothing_on(seat) -> None:
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(_yaml().replace(f"          enabled_classes: {ALL_CLASSES}\n", ""))
    out = submit(broker)
    assert out["accepted"] is False and "isn't switched on" in out["reason"]


def test_an_unauthored_allowance_refuses(seat) -> None:
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(_yaml().replace("          drafting_allowance_per_cycle: 25\n", ""))
    out = submit(broker)
    assert out["accepted"] is False and "drafting_allowance_per_cycle" in out["reason"]


def test_a_non_admin_requester_refuses(seat) -> None:
    """FALSIFIER: drop the scope.admins check and any staff member spends the allowance."""
    broker, _db, _y, queue = seat
    out = submit(broker, requested_by="staff@firm.example")
    assert out["accepted"] is False and "Named Administrators" in out["reason"]
    assert queued(queue) == []


def test_a_second_draft_of_the_same_class_on_a_matter_refuses(seat) -> None:
    """FALSIFIER: drop the matter+class duplicate check and the same brief runs twice."""
    broker, *_ = seat
    first = submit(broker)
    out = submit(broker, request_ref="<another@mail.firm.example>")
    assert out["accepted"] is False and out["job_id"] == first["job_id"]
    assert "already underway" in out["reason"]
    again = submit(broker)  # the same email, the same class
    assert again["accepted"] is False and "request email" in again["reason"]
    # A different class on the same matter, even from the same email, is its own job.
    assert submit(broker, document_class="memo")["accepted"] is True
    assert submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})["accepted"] is True


def test_a_spent_allowance_refuses(seat) -> None:
    """FALSIFIER: drop the remaining check and the cycle's count is unbounded."""
    broker, _db, yaml_path, _q = seat
    yaml_path.write_text(_yaml(allowance=1))
    first = submit(broker)
    call(broker, "drafting_job_record", uid=0, job_id=first["job_id"], state="running", fields={"cents": 500})
    out = submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})
    assert out["accepted"] is False and "spent" in out["reason"]


def test_a_malformed_envelope_refuses_by_field(seat) -> None:
    broker, *_ = seat
    out = submit(broker, request_ref="not a message id")
    assert out["accepted"] is False and "request_ref" in out["reason"]
    out = submit(broker, document_class="settlement_statement")
    assert out["accepted"] is False and "document_class" in out["reason"]


def test_file_to_is_the_matter_or_the_authored_library(seat) -> None:
    """FALSIFIER: drop the file_to check and a draft files on any matter."""
    broker, *_ = seat
    out = submit(broker, file_to={"id": OTHER_MATTER, "number": "900202"})
    assert out["accepted"] is False and "filing target" in out["reason"]
    library = {"id": OTHER_MATTER, "number": "OPS-OPERATOR-LIBRARY"}
    rehearsal = submit(
        broker, matter={"id": "3dad2f6b-7c5b-4cee-a06d-aab9e1e91a25", "number": "900204"}, file_to=library
    )
    assert rehearsal["accepted"] is True
    wrong_id = {"id": "9dad2f6b-7c5b-4cee-a06d-aab9e1e91a99", "number": "OPS-OPERATOR-LIBRARY"}
    out = submit(broker, matter={"id": "4dad2f6b-7c5b-4cee-a06d-aab9e1e91a26", "number": "900205"}, file_to=wrong_id)
    assert out["accepted"] is False and "filing target" in out["reason"]


def test_an_unconfigured_broker_refuses(seat) -> None:
    broker, *_ = seat
    broker.drafting = None
    with pytest.raises(ValueError):
        submit(broker)


# -- gates --------------------------------------------------------------------


@pytest.mark.parametrize("action", ["drafting_job_record", "drafting_job_resume"])
def test_the_runner_verbs_are_root_only(seat, action) -> None:
    broker, *_ = seat
    with pytest.raises(PermissionError):
        call(broker, action, job_id="x", state="running", fields={}, reason="r")


def test_submit_is_not_reachable_from_an_agent_uid_child(seat) -> None:
    broker, *_ = seat
    with pytest.raises(PermissionError):
        call(broker, "drafting_job_submit", pid=9999, envelope=_envelope())


def test_the_agent_sees_the_projection_and_root_the_row(seat) -> None:
    broker, *_ = seat
    job = submit(broker)["job_id"]
    agent = call(broker, "drafting_job_status", pid=9999, job_id=job)["job"]
    root = call(broker, "drafting_job_status", pid=9999, uid=0, job_id=job)["job"]
    assert "request_ref" not in agent and agent["document_class"] == "mediation_brief"
    assert root["request_ref"] == "<CA1x2y3z@mail.firm.example>"
    listed = call(broker, "drafting_job_status")["jobs"]
    assert [j["id"] for j in listed] == [job]


# -- record, allowance cents, resume -------------------------------------------


def test_record_moves_the_row_and_the_projection_carries_the_report(seat) -> None:
    broker, db, *_ = seat
    job = submit(broker)["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=job, state="running", fields={})
    out = call(
        broker,
        "drafting_job_record",
        uid=0,
        job_id=job,
        state="delivered",
        fields={
            "cents": 712,
            "folder_id": "F1",
            "delivery": {
                "files": [{"name": "Mediation Brief.docx", "size": 10, "role": "draft"}],
                "markers": [{"kind": "ATTORNEY", "where": "Settlement Demand"}],
            },
            # The runner sends this one top-level; it lands in the same report.
            "caption_discrepancies": [{"field": "case_number"}],
        },
    )
    proj = out["job"]
    assert proj["state"] == "delivered" and proj["files"][0]["name"] == "Mediation Brief.docx"
    assert proj["markers"][0]["kind"] == "ATTORNEY" and proj["caption_discrepancies"] == [{"field": "case_number"}]
    assert audit_types(db)[-2:] == ["DRAFTING_JOB_RUNNING", "DRAFTING_JOB_DELIVERED"]
    conn = sqlite3.connect(db)
    meta = json.loads(conn.execute("SELECT metadata FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    conn.close()
    # Counts only in the audit row.
    assert meta["markers_count"] == 1 and "markers" not in meta
    assert meta["caption_discrepancies_count"] == 1
    # A later same-state note never erases the stored report.
    note = call(broker, "drafting_job_record", uid=0, job_id=job, state="delivered", fields={"reason": "n"})
    assert note["job"]["caption_discrepancies"] == [{"field": "case_number"}]
    with pytest.raises(ValueError):
        call(broker, "drafting_job_record", uid=0, job_id=job, state="running", fields={})


def test_allowance_reports_cents_classes_and_excludes_the_asking_job(seat) -> None:
    broker, _db, yaml_path, _q = seat
    a = submit(broker)["job_id"]
    b = submit(broker, matter={"id": OTHER_MATTER, "number": "900202"})["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=a, state="running", fields={"cents": 300})
    call(broker, "drafting_job_record", uid=0, job_id=b, state="running", fields={"cents": 450})
    full = call(broker, "drafting_allowance", uid=0)
    assert full["cents_used"] == 750 and full["used"] == 2 and full["remaining"] == 23
    assert full["unit"] == "drafts" and "mediation_brief" in full["enabled_classes"]
    assert call(broker, "drafting_allowance", uid=0, exclude_job_id=b)["cents_used"] == 300
    yaml_path.write_text(_yaml(allowance=2))
    assert call(broker, "drafting_allowance", uid=0, exclude_job_id=b)["remaining"] == 1


def test_resume_writes_the_marker_for_a_failed_job_only(seat) -> None:
    """Failed is our machinery and resumable; held is the file's or the
    request's, final, and never resumes."""
    broker, db, _y, queue = seat
    job = submit(broker)["job_id"]
    with pytest.raises(ValueError):
        call(broker, "drafting_job_resume", uid=0, job_id=job, reason="fixed")
    call(broker, "drafting_job_record", uid=0, job_id=job, state="failed", fields={"reason": "render"})
    with pytest.raises(ValueError):
        call(broker, "drafting_job_resume", uid=0, job_id=job, reason="")
    assert call(broker, "drafting_job_resume", uid=0, job_id=job, reason="renderer fixed")["queued"] is True
    assert json.loads((queue / f".resume-{job}.json").read_text()) == {"job_id": job, "reason": "renderer fixed"}
    assert audit_types(db)[-1] == "DRAFTING_JOB_RESUME_REQUESTED"
    held = submit(broker, document_class="memo")["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=held, state="held", fields={"reason": "no pleadings"})
    with pytest.raises(ValueError, match="not failed"):
        call(broker, "drafting_job_resume", uid=0, job_id=held, reason="try again")


def test_a_new_request_after_a_held_job_is_accepted(seat) -> None:
    broker, *_ = seat
    job = submit(broker)["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=job, state="held", fields={"reason": "premise"})
    out = submit(broker, request_ref="<followup@mail.firm.example>")
    assert out["accepted"] is True and out["job_id"] != job


def test_a_same_state_note_writes_no_audit_row(seat) -> None:
    broker, db, *_ = seat
    job = submit(broker)["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=job, state="running", fields={})
    before = audit_types(db)
    out = call(broker, "drafting_job_record", uid=0, job_id=job, state="running", fields={"reason": "wake lost"})
    assert out["job"]["reason"] == "wake lost" and audit_types(db) == before


def test_the_demand_lane_is_untouched_by_a_drafting_submit(seat) -> None:
    """The two lanes share the db file, never a table."""
    broker, db, *_ = seat
    submit(broker)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "drafting_jobs" in tables and "demand_jobs" not in tables


def test_a_non_admin_never_learns_which_classes_are_off(seat) -> None:
    """FALSIFIER: check the class before the requester and a non-admin is told
    the firm's switched-off classes."""
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(classes="'memo'"))
    out = submit(broker, requested_by="staff@firm.example", document_class="depo_outline")
    assert out["accepted"] is False and "Named Administrators" in out["reason"]
    assert "switched on" not in out["reason"]
    assert queued(queue) == []


RUNNER_REASON_CODES = (
    "request_incomplete",
    "gate_refused",
    "format_check",
    "record_unreadable",
    "audit_unsettled",
    "destination_mismatch",
    "destination_unauthored",
    "no_readable_documents",
    "filing_refused",
    "stage_unfinished",
    "limit",
    "no_verdict",
    "unexpected",
    "config_missing",
)


@pytest.mark.parametrize("code", RUNNER_REASON_CODES)
def test_every_runner_reason_code_is_recorded_verbatim(seat, code) -> None:
    """The broker validates no reason code: the runner's `<code>: <sentence>`
    is stored and audited as written (the skill relays the sentence only)."""
    broker, db, *_ = seat
    job = submit(broker)["job_id"]
    reason = f"{code}: the sentence the reply relays"
    state = "held" if code in ("request_incomplete", "destination_mismatch") else "failed"
    out = call(broker, "drafting_job_record", uid=0, job_id=job, state=state, fields={"reason": reason})
    assert out["job"]["reason"] == reason
    conn = sqlite3.connect(db)
    meta = json.loads(conn.execute("SELECT metadata FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    conn.close()
    assert meta["reason"] == reason
