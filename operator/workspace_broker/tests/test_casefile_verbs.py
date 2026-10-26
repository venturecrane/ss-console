"""The Case File Manager's broker verbs (casefile_verbs.py): the request's
gates, the lines a delivered job records, the broker raising them on the reply
thread, and the apply: only lines that job issued, on that thread, approved by
a Named Administrator, each taken once; and the apply job's outcomes closing
their casework lines. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import casework_ledger
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.casefile_store import CasefileLedger
from workspace_broker import bound_replies
from workspace_broker.casefile_casework import raise_lines
from workspace_broker.casefile_verbs import CasefileVerbs
from workspace_broker.recipient_policy import sender_key
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
MATTER = "11111111-1111-4111-8111-111111111111"
TASK = "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1"
ADMIN = "admin@firm.example"
OTHER = "staff@firm.example"
SECOND = "second@firm.example"
REF = "<CA1x2y3z@mail.firm.example>"
THREAD = "AAQkADconversation-1"
SHA_A, SHA_B = "a" * 64, "b" * 64

YAML = """
scope:
  admins:
    - Admin@Firm.example
    - second@firm.example
personas:
  - slug: operator
    skills:
      - name: case-file-manager
        initiation: {{manual: {manual}, scheduled: false, webhook: false}}
        enabled: {enabled}
        settings:
          folder_name: 'Case File Manager'
          file_to_matter_id: '{library}'
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-LIBRARY'
"""


@pytest.fixture
def seat(tmp_path, monkeypatch):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(YAML.format(manual="true", enabled="true", library=LIBRARY))
    firm = tmp_path / "casefile-broker-firm.yaml"
    firm.write_text("monthly_budget_usd: 200\nper_job_cap_usd: 50\n")
    monkeypatch.setenv("SMD_CASEFILE_FIRM_CONFIG", str(firm))
    monkeypatch.setenv(casework_ledger.LEDGER_PATH_ENV, str(tmp_path / "casework.jsonl"))
    queue = tmp_path / "casefile-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.audit_db_path = db
    broker.escalation_ledger_path = None
    broker._escalation_lock = threading.Lock()
    broker.casefile = CasefileVerbs(
        CasefileLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, broker=broker
    )
    broker.casefile._now = lambda: datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
    return {"broker": broker, "db": db, "yaml": yaml_path, "queue": queue, "casework": tmp_path / "casework.jsonl"}


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def submit(broker, **over):
    env = {
        "trigger": "request",
        "requester": ADMIN,
        "message_ref": REF,
        "request_text": "Please review Pat's files.",
        "scope": {"assisting": ["Pat Example"]},
    }
    env.update(over)
    return call(broker, "casefile_job_submit", envelope=env)


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


def deliver_with_lines(seat) -> str:
    b = seat["broker"]
    job = submit(b)["job_id"]
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    lines = [
        {"n": 1, "kind": "task", "matter_id": MATTER, "source_id": TASK, "sha256": SHA_A},
        {"n": 2, "kind": "medicals", "matter_id": MATTER, "source_id": "med-0123456789abcdef01234567", "sha256": SHA_B},
    ]
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="delivered",
         fields={"cents": 120, "lines": lines, "lines_proposed": 2, "files": []})  # fmt: skip
    return job


def key_of(job: str) -> str:
    return f"casefile_job:{job}:1:delivered"


def reply_went(seat, job: str) -> None:
    """The delivered job's one bound reply, claimed and settled sent: the raise witness."""
    bound_replies.claim(seat["db"], key_of(job), "g" * 20, REF, "sess-1")
    bound_replies.settle(seat["db"], key_of(job), "sent")


def raised(seat) -> str:
    job = deliver_with_lines(seat)
    reply_went(seat, job)
    assert raise_lines(seat["broker"], job, THREAD, "sess-1", key_of(job)) == 2
    return job


def approve(seat, n: int, who: str = ADMIN, thread: str = THREAD) -> None:
    rows = casework_ledger.read_ledger(str(seat["casework"]))
    raise_row = next(r for r in rows if r["event"] == "proposed" and r["n"] == n)
    ev = {k: raise_row[k] for k in ("skill", "kind", "matter_id", "source_id", "item_key")}
    ev.update(event="approved", n=n, thread_ref=thread, decided_by={"name": "Someone", "key": sender_key(who)})
    casework_ledger.append_line(str(seat["casework"]), casework_ledger.stamp_event(ev))


def apply(seat, who: str = ADMIN, numbers=(1, 2), ref: str = "<approve-1@mail.example>", thread: str = THREAD):
    return call(
        seat["broker"], "casefile_apply_submit", thread_ref=thread, approver=who, message_ref=ref, numbers=list(numbers)
    )


def record(seat, job: str, state: str, **fields) -> None:
    call(seat["broker"], "casefile_job_record", pid=1, uid=0, job_id=job, state=state, fields=fields)


def test_a_request_is_refused_unless_the_skill_is_enabled_and_the_asker_an_admin(seat) -> None:
    b = seat["broker"]
    assert submit(b, requester=OTHER)["accepted"] is False
    seat["yaml"].write_text(YAML.format(manual="true", enabled="false", library=LIBRARY))
    assert "not enabled" in submit(b)["reason"]
    seat["yaml"].write_text(YAML.format(manual="false", enabled="true", library=LIBRARY))
    assert "not switched on" in submit(b)["reason"]


def test_a_request_from_the_agent_uid_off_the_gateway_is_refused(seat) -> None:
    with pytest.raises(PermissionError):
        call(seat["broker"], "casefile_job_submit", pid=999, envelope={})


def test_a_request_queues_the_resolved_envelope_and_audits_it(seat) -> None:
    got = submit(seat["broker"])
    assert got["accepted"] is True
    doc = json.loads((seat["queue"] / f"{got['job_id']}.json").read_text())
    assert doc["mode"] == "full" and doc["scope"] == {"assisting": {"names": ["Pat Example"]}}
    assert doc["file_to_matter_id"] == LIBRARY and doc["folder_name"] == "Case File Manager"
    assert "CASEFILE_JOB_SUBMITTED" in audit_types(seat["db"])
    again = submit(seat["broker"])
    assert again["accepted"] is False and again["job_id"] == got["job_id"]


def test_lines_are_raised_only_after_the_bound_reply_was_sent_and_only_once(seat) -> None:
    job = deliver_with_lines(seat)
    assert raise_lines(seat["broker"], job, THREAD, "sess-1", key_of(job)) == 0  # no reply went: no witness
    reply_went(seat, job)
    assert raise_lines(seat["broker"], job, THREAD, "sess-1", key_of(job)) == 2
    rows = casework_ledger.read_ledger(str(seat["casework"]))
    assert [(r["event"], r["n"], r["thread_ref"], r["payload"]["action"]) for r in rows] == [
        ("proposed", 1, THREAD, "close"),
        ("proposed", 2, THREAD, "add_row"),
    ]
    assert raise_lines(seat["broker"], job, THREAD, "sess-1", key_of(job)) == 0  # raised once


def test_only_lines_this_reply_named_and_this_admin_approved_on_that_thread_are_applied(seat) -> None:
    raised(seat)
    approve(seat, 1)
    approve(seat, 2, who=OTHER)  # not a Named Administrator
    assert apply(seat, thread="other-thread")["accepted"] is False
    assert apply(seat, who=OTHER)["accepted"] is False
    assert apply(seat, numbers=[2])["accepted"] is False  # named, but not this admin's approval
    got = apply(seat)
    assert got["accepted"] is True and got["lines"] == [1], got
    env = json.loads((seat["queue"] / f"{got['job_id']}.json").read_text())
    assert env["apply"]["lines"] == [{"n": 1, "kind": "task", "matter_id": MATTER, "source_id": TASK, "sha256": SHA_A}]
    again = apply(seat, ref="<approve-2@mail.example>")
    assert again["accepted"] is False and again["reason"].startswith("nothing_approved")


def test_each_admin_applies_only_their_own_approvals_and_a_second_apply_may_queue(seat) -> None:
    raised(seat)
    approve(seat, 1)
    approve(seat, 2, who=SECOND)
    first = apply(seat)
    assert first["lines"] == [1] and first["approved_by_another"] == [2]
    second = apply(seat, who=SECOND, ref="<approve-2@mail.example>")  # while the first is still queued
    assert second["accepted"] is True and second["lines"] == [2], second
    env = json.loads((seat["queue"] / f"{second['job_id']}.json").read_text())
    assert env["apply"]["approver"] == SECOND


def test_a_refused_apply_pages_smd_and_says_so(seat, monkeypatch) -> None:
    from workspace_broker import casefile_store

    raised(seat)
    approve(seat, 1)

    def refuse(self, envelope):
        raise casefile_store.SubmitRefused("change 1 is not open to apply")

    monkeypatch.setattr(casefile_store.CasefileLedger, "submit", refuse)
    got = apply(seat)
    assert got["accepted"] is False and got["paged"] is True
    assert "DELIVERY_PROOF_FAILED" in audit_types(seat["db"])


def test_a_delivered_apply_closes_every_line_it_made_or_found_made_with_a_real_witness(seat) -> None:
    job = raised(seat)
    approve(seat, 1)
    approve(seat, 2)
    apply_id = apply(seat)["job_id"]
    record(seat, apply_id, "running")
    record(seat, apply_id, "delivered", line_outcomes=[
        {"n": 1, "kind": "task", "outcome": "already_complete"},
        {"n": 2, "kind": "medicals", "outcome": "written"},
    ])  # fmt: skip
    assert [ln["state"] for ln in seat["broker"].casefile.ledger.lines(job)] == ["applied", "applied"]
    done = [r for r in casework_ledger.read_ledger(str(seat["casework"])) if r["event"] == "completed"]
    assert [r["tool_call_id"] for r in done] == [f"casefile:{apply_id}:1", f"casefile:{apply_id}:2"]


def test_a_completed_row_with_no_delivered_apply_behind_it_is_refused(seat) -> None:
    from workspace_broker import casefile_casework

    job = raised(seat)
    approve(seat, 1)
    line = seat["broker"].casefile.ledger.lines(job)[0]
    ev = casefile_casework.casework_event(line, "completed", tool_call_id="casefile:01KTJ0BX0000000000000000ZZ:1")
    assert casefile_casework.applied_witness(seat["broker"].casefile.ledger, ev) is False


def test_a_failed_apply_frees_only_lines_it_never_began_writing(seat) -> None:
    job = raised(seat)
    approve(seat, 1)
    approve(seat, 2)
    apply_id = apply(seat)["job_id"]
    record(seat, apply_id, "running")
    record(seat, apply_id, "failed", reason="unexpected: x", intents=[1])
    states = {ln["n"]: (ln["state"], ln["apply_job_id"]) for ln in seat["broker"].casefile.ledger.lines(job)}
    assert states == {1: ("applying", apply_id), 2: ("raised", None)}
    # A newer apply takes line 2; the resumed first apply gets back only line 1.
    newer = apply(seat, ref="<approve-3@mail.example>", numbers=[2])
    assert newer["lines"] == [2]
    call(seat["broker"], "casefile_job_resume", pid=1, uid=0, job_id=apply_id, reason="fixed")
    got = call(seat["broker"], "casefile_job_status", pid=1, uid=0, job_id=apply_id)
    assert got["job"]["owned_lines"] == [1]


def test_root_status_carries_the_month_spend(seat) -> None:
    deliver_with_lines(seat)
    got = call(seat["broker"], "casefile_job_status", pid=1, uid=0)
    assert got["month_cents"] == 120 and got["monthly_budget_cents"] == 20000


def test_the_bound_reply_of_a_delivered_job_raises_its_lines_once_and_audits_it(seat) -> None:
    from workspace_broker import reply_binding

    job = deliver_with_lines(seat)
    row = seat["broker"].casefile.ledger.read(job)
    v = reply_binding.Verified(
        kind="casefile_job", key=key_of(job), job_id=job, internet_message_id=REF,
        graph_message_id="g" * 20, sender=ADMIN, conversation_id=THREAD, row=row,
    )  # fmt: skip
    reply_went(seat, job)
    reply_binding.after_bound_reply(seat["broker"], v, "sess-1")
    reply_binding.after_bound_reply(seat["broker"], v, "sess-1")
    assert [ln["state"] for ln in seat["broker"].casefile.ledger.lines(job)] == ["raised", "raised"]
    assert audit_types(seat["db"]).count("CASEFILE_LINES_PROPOSED") == 1


def test_a_binding_names_the_casefile_kind_and_reads_its_ledger(seat, monkeypatch) -> None:
    from workspace_broker import reply_binding

    monkeypatch.setenv("SMD_CASEFILE_QUEUE_DIR", str(seat["queue"]))
    assert "casefile_job" in reply_binding.JOB_KINDS
    job = deliver_with_lines(seat)
    assert reply_binding.job_ledger(seat["broker"], "casefile_job").read(job)["state"] == "delivered"


def test_casefile_job_resume_writes_the_marker_for_a_failed_job_only(seat) -> None:
    b = seat["broker"]
    job = submit(b)["job_id"]
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    with pytest.raises(ValueError):
        call(b, "casefile_job_resume", pid=1, uid=0, job_id=job, reason="fixed")
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="failed", fields={"reason": "unexpected: x"})
    assert call(b, "casefile_job_resume", pid=1, uid=0, job_id=job, reason="fixed the fetch")["queued"] is True
    assert (seat["queue"] / f".resume-{job}.json").is_file()
    assert "CASEFILE_JOB_RESUME_REQUESTED" in audit_types(seat["db"])


def test_casefile_job_close_ends_a_held_job_once(seat) -> None:
    b = seat["broker"]
    job = submit(b)["job_id"]
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    call(b, "casefile_job_record", pid=1, uid=0, job_id=job, state="held", fields={"reason": "filing_refused: x"})
    ref = "01KTJ0BX0000000000000000ZZ"
    assert call(b, "casefile_job_close", pid=1, uid=0, job_id=job, reason="SMD filed it", source_ref=ref, dry_run=True)[
        "dry_run"
    ]
    got = call(b, "casefile_job_close", pid=1, uid=0, job_id=job, reason="SMD filed it", source_ref=ref)
    assert got["job"]["state"] == "failed"
    with pytest.raises(ValueError):
        call(b, "casefile_job_close", pid=1, uid=0, job_id=job, reason="again", source_ref=ref)


def test_a_library_named_by_number_queues_with_no_id_for_the_runner_to_resolve(seat) -> None:
    seat["yaml"].write_text(
        YAML.format(manual="true", enabled="true", library=LIBRARY).replace(
            f"          file_to_matter_id: '{LIBRARY}'\n", "          library_matter_number: '2026-OPS-LIB'\n"
        )
    )
    got = submit(seat["broker"])
    assert got["accepted"] is True, got
    doc = json.loads((seat["queue"] / f"{got['job_id']}.json").read_text())
    assert doc["file_to_matter_id"] == "" and doc["file_to_matter_number"] == "2026-OPS-LIB"
