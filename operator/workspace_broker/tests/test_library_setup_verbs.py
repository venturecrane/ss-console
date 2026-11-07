"""'Set up our ...' at the broker (library_setup_verbs.py, library_setup_ledger.py):
each submit check, both switch states, the drafting verbs serving a setup job's
status and record, and its reply in the wording file's words. Made-up firm."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import fixed_wording, library_setup_wording as W, reply_binding
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.drafting_verbs import DraftingVerbs
from workspace_broker.library_setup_ledger import DraftingOrSetup, SetupLedger
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
ADMIN = "admin@firm.example"

YAML = """
scope:
  admins:
    - Admin@Firm.example
personas:
  - slug: operator
    skills:
      - name: document-drafter
        enabled: true
      - name: document-library-establishment
        enabled: {skill}
self_initiation:
  document_library:
    operator_matter:
      number: '{library}'
{switch}
"""


def _yaml(skill="true", library="OPS-LIBRARY", switch="") -> str:
    return YAML.format(skill=skill, library=library, switch=switch)


ON = "logic_in_code:\n  library_setup: enforce"
OFF = "logic_in_code:\n  library_setup: observe"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml())
    queue = tmp_path / "drafting-queue"
    monkeypatch.setenv("SMD_DRAFTING_QUEUE_DIR", str(queue))
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.audit_db_path = db
    broker.customer_path = yaml_path
    broker.drafting = DraftingVerbs(DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append)
    return broker, db, yaml_path, queue


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def envelope(**over):
    env = {
        "kind": "memo",
        "attorney": None,
        "requested_by": ADMIN,
        "request_ref": "<setup1@mail.firm.example>",
        "request_text": "Please set up our memos.",
    }
    return {**env, **over}


def submit(broker, **over):
    return call(broker, "library_setup_submit", envelope=envelope(**over))


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# ---- the submit, both switch states -------------------------------------------------


@pytest.mark.parametrize("switch", ["", OFF])
def test_dark_the_job_is_queued_to_count_and_the_turn_answers_as_today(seat, switch):
    broker, db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(switch=switch))
    out = submit(broker)
    assert out["accepted"] is True and out["live"] is False and "say" not in out
    record = json.loads((queue / f"{out['job_id']}.json").read_text())
    assert record["kind"] == "library_setup" and record["live"] is False
    assert record["library_matter_number"] == "OPS-LIBRARY" and record["setup_kind"] == "memo"
    assert "request_text" not in record
    assert audit_types(db) == []  # no drafting row's activity for a job that drafts nothing


def test_live_the_answer_carries_the_wording_files_acknowledgement(seat):
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(switch=ON))
    out = submit(broker)
    assert out["live"] is True and out["say"] == W.ack("memo")
    assert json.loads((queue / f"{out['job_id']}.json").read_text())["live"] is True
    named = submit(broker, attorney="Blair  Sample", request_ref="<setup2@mail.firm.example>")
    assert named["say"] == W.ack("memo", "Blair Sample")


def test_an_unreadable_switch_is_never_live(seat):
    broker, _db, yaml_path, _queue = seat
    yaml_path.write_text(_yaml(switch="logic_in_code:\n  library_setup: loud"))
    assert submit(broker)["live"] is False


@pytest.mark.parametrize(
    "over,yaml_over,key",
    [
        ({"kind": "letter"}, {}, "refused_kind"),
        ({"requested_by": "staff@firm.example"}, {}, "refused_admin"),
        ({}, {"skill": "false"}, "refused_seat"),
        ({}, {"library": ""}, "refused_seat"),
        ({"attorney": "<script>"}, {}, "refused_attorney"),
    ],
)
def test_each_refusal_is_a_sentence_from_the_wording_file(seat, over, yaml_over, key):
    broker, _db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(**yaml_over))
    out = submit(broker, **over)
    assert out["accepted"] is False and out["reason_key"] == key and out["reason"] in W.SENTENCES.values()
    assert not queue.exists() or not list(queue.glob("*.json"))


def test_a_second_setup_of_the_same_kind_waits_for_the_first(seat):
    broker, *_ = seat
    first = submit(broker)
    again = submit(broker, request_ref="<setup2@mail.firm.example>")
    assert again["accepted"] is False and again["job_id"] == first["job_id"]
    assert again["reason"] == W.say("refused_underway", **W.names("memo"))
    other = submit(broker, attorney="Blair Sample", request_ref="<setup3@mail.firm.example>")
    assert other["accepted"] is True


def test_only_the_gateway_or_root_may_submit(seat):
    broker, *_ = seat
    with pytest.raises(Exception):
        call(broker, "library_setup_submit", pid=1, uid=AGENT_UID, envelope=envelope())


# ---- through the drafting verbs ----------------------------------------------------------

REPORT = {
    "setup_outcome": "candidates",
    "counts": {"matters_listed": 2},
    "candidates": [
        {"file_id": "f-old", "matter_number": "1002", "saved_on": "2023-11-07", "score": 90},
        {"file_id": "f-new", "matter_number": "", "saved_on": "2026-05-08", "score": 70},
    ],
}


def _ran(broker, state="delivered", report=REPORT, **over):
    jid = submit(broker, **over)["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state=state, fields={"cents": 0, "report": report})
    return jid


def test_the_lane_reads_and_records_a_setup_job_through_the_drafting_verbs(seat):
    broker, *_ = seat
    jid = _ran(broker, state="observed")
    job = call(broker, "drafting_job_status", job_id=jid)["job"]
    assert job["job_kind"] == "library_setup" and job["state"] == "observed"
    assert job["report"]["candidates"][0]["file_id"] == "f-old"
    with pytest.raises(Exception):
        call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields={})


def test_a_delivered_setup_replies_in_the_wording_files_words(seat):
    broker, *_ = seat
    jid = _ran(broker)
    row = reply_binding.job_ledger(broker, "drafting_job").read(jid)
    (text,) = fixed_wording.render("drafting_job", row)
    assert text.splitlines()[0] == W.say("lead_firm", **W.names("memo"))
    assert "1. Matter 1002, saved November 7, 2023, file f-old" in text
    assert "2. Saved May 8, 2026, file f-new" in text
    assert W.say("question") in text and text.endswith(W.say("outside"))


def test_an_observed_setup_is_never_replied_on(seat):
    broker, *_ = seat
    jid = _ran(broker, state="observed")
    row = reply_binding.job_ledger(broker, "drafting_job").read(jid)
    assert row["state"] not in reply_binding.replyable_states("drafting_job")


def test_a_held_setup_is_smds_and_silent(seat):
    from workspace_broker import stop_note

    broker, *_ = seat
    jid = _ran(broker, state="held", report={"hold": "could_not_look"})
    row = reply_binding.job_ledger(broker, "drafting_job").read(jid)
    assert stop_note.held_silent("drafting_job", row)


def test_a_drafting_jobs_id_still_reads_the_drafting_row(seat, tmp_path):
    broker, db, _y, queue = seat
    both = DraftingOrSetup(DraftingLedger(db, queue), SetupLedger(db, queue))
    assert both.read("01ZZZZZZZZZZZZZZZZZZZZZZZZ") is None
    assert both.recent(1) == []  # everything else is the drafting ledger


# ---- the words ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "report,named,key",
    [
        ({"setup_outcome": "none_found"}, None, "none_firm"),
        ({"setup_outcome": "none_found", "attorney_name": "Blair Sample"}, "Blair", "none_attorney"),
        ({"setup_outcome": "attorney_not_rostered"}, "Casey", "not_rostered"),
        ({"setup_outcome": "attorney_ambiguous"}, "Blair", "ambiguous"),
        ({"setup_outcome": "not_a_form"}, None, "not_a_form"),
        ({"setup_outcome": "unusable"}, None, "unusable"),
    ],
)
def test_each_outcome_renders_its_one_sentence(report, named, key):
    row = {"document_class": "memo", "report_json": json.dumps(report), "attorney_named": named}
    values = {"named": named, "attorney": report.get("attorney_name"), **W.names("memo")}
    assert W.render(row) == f"{W.say(key, **values)}\n\n{W.say('outside')}"


def test_a_row_with_nothing_to_say_renders_nothing():
    assert W.render({"document_class": "memo", "report_json": json.dumps({"setup_outcome": "candidates"})}) == ""
    assert W.render({"document_class": "memo", "report_json": "not json"}) == ""


def test_an_attorneys_candidates_name_the_attorney():
    report = {**REPORT, "attorney_name": "Blair Sample"}
    text = W.render({"document_class": "mediation_brief", "report_json": json.dumps(report)})
    assert text.startswith(W.say("lead_attorney", attorney="Blair Sample", **W.names("mediation_brief")))
