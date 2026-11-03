"""The matter records lane's broker verbs (records_verbs.py): the request's
gates (the recipients rule, the budget, the skill), one email answered by one
job ever, the acknowledgement composed in code, the runner's record moving a
job into and out of SMD review, and the spool opened to root. Each test fails
if the line it defends is removed."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import verbs
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.records_store import HOLD_SQL, RecordsLedger
from workspace_broker.records_verbs import RecordsVerbs, acknowledgement, staff_list
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
STAFF = "chris@firm.example"
REF = "AAMkAGI2TG93AAA=" + "x" * 40
REVIEW_ID = "0123456789abcdef01234567"
DIGEST = "d" * 64

YAML = """
matter_file_delivery:
  recipients:
    - '@firm.example'
    - Outside.Paralegal@other.example
personas:
  - slug: operator
    skills:
      - name: matter-records
        initiation: {{manual: {manual}, scheduled: false, webhook: false}}
        enabled: {enabled}
"""


@pytest.fixture
def seat(tmp_path, monkeypatch):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(YAML.format(manual="true", enabled="true"))
    firm = tmp_path / "records-broker-firm.yaml"
    firm.write_text("monthly_budget_usd: 100\nper_job_cap_usd: 5\nusd_per_page: 0.02\n")
    monkeypatch.setenv("SMD_RECORDS_FIRM_CONFIG", str(firm))
    queue = tmp_path / "records-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.audit_db_path = db
    broker.escalation_ledger_path = None
    broker._escalation_lock = threading.Lock()
    broker.records = RecordsVerbs(RecordsLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append)
    broker.records._now = lambda: datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
    # Decisions count when "root" wrote them; in a test, root is the test's own uid.
    broker.records._decision_uid = os.getuid()
    monkeypatch.setenv("SMD_MATTER_FILE_DECISIONS_DIR", str(tmp_path / "decisions"))
    return {"broker": broker, "db": db, "yaml": yaml_path, "queue": queue, "firm": firm, "tmp": tmp_path}


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def root(broker, action, **req):
    return call(broker, action, pid=1, uid=0, **req)


def submit(broker, *, pid=GATEWAY_PID, uid=AGENT_UID, **over):
    env = {
        "trigger": "request",
        "requester": STAFF,
        "message_ref": REF,
        "request_text": "Please send the ER records and the imaging.",
        "matter_id": "100123",
    }
    env.update(over)
    env = {k: v for k, v in env.items() if v is not None}
    return call(broker, "records_job_submit", pid=pid, uid=uid, envelope=env)


def rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM records_jobs ORDER BY created_at")]
    finally:
        conn.close()


def audit_types(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# ---- submit ---------------------------------------------------------------------------------


def test_submit_queues_one_job_and_returns_the_acknowledgement_sentence(seat):
    out = submit(seat["broker"])
    assert out["ok"] and out["accepted"] and out["state"] == "queued"
    assert out["ack"] == (
        "Received. Your request for records on matter 100123 is queued; the files and an answer "
        "for each item will come back in this thread."
    )
    queued = json.loads((seat["queue"] / f"{out['job_id']}.json").read_text())
    assert queued["kind"] == "records" and queued["matter_id"] == "100123" and queued["ack"] == "turn"
    assert queued["request_text"].startswith("Please send")
    assert oct((seat["queue"] / f"{out['job_id']}.json").stat().st_mode & 0o777) == "0o640"
    assert [r["state"] for r in rows(seat["db"])] == ["queued"]
    assert audit_types(seat["db"])[-1] == "RECORDS_JOB_SUBMITTED"


def test_the_acknowledgement_names_no_matter_when_none_was_given(seat):
    out = submit(seat["broker"], matter_id=None)
    assert out["ack"] == acknowledgement(None)
    assert "matter" not in out["ack"] and "hour" not in out["ack"] and "minute" not in out["ack"]


def test_the_envelope_carries_exactly_the_request_keys(seat):
    out = submit(seat["broker"], scope={"matters": ["1"]})
    assert out["accepted"] is False and "exactly" in out["reason"]
    assert rows(seat["db"]) == []


def test_a_live_twin_is_already_queued_and_queues_nothing(seat):
    b = seat["broker"]
    first = submit(b)["job_id"]
    again = submit(b, request_text="Resending: the ER records please.")
    assert again["accepted"] is False and again["duplicate"] == "already_queued" and again["job_id"] == first
    assert f"already queued as records job {first}" in again["reason"]
    assert len(rows(seat["db"])) == 1 and len(list(seat["queue"].glob("*.json"))) == 1


@pytest.mark.parametrize("terminal", ["failed", "delivered"])
def test_a_terminal_twin_is_already_done_and_queues_nothing(seat, terminal):
    """FALSIFIER: make the unique index partial (as casefile's is, for failed
    requests) and a failed job's email could queue a second paid job."""
    b = seat["broker"]
    first = submit(b)["job_id"]
    root(b, "records_job_record", job_id=first, state="running")
    if terminal == "failed":
        root(b, "records_job_record", job_id=first, verdict="failed", reason="finder_failed: timeout")
    else:
        root(b, "records_job_record", job_id=first, verdict="delivered",
             delivery={"review_id": REVIEW_ID, "digest": DIGEST})  # fmt: skip
        root(b, "records_job_record", job_id=first, delivery={"status": "sent"})
    assert rows(seat["db"])[0]["state"] == terminal
    again = submit(b)
    assert again["accepted"] is False and again["duplicate"] == "already_done" and again["job_id"] == first
    assert f"already answered as records job {first}" in again["reason"]
    assert len(rows(seat["db"])) == 1


def test_same_matter_jobs_from_different_emails_both_queue(seat):
    b = seat["broker"]
    assert submit(b)["accepted"] and submit(b, message_ref=REF + "2")["accepted"]
    assert len(rows(seat["db"])) == 2


def test_a_requester_off_the_recipients_list_is_refused(seat):
    out = submit(seat["broker"], requester="someone@elsewhere.example")
    assert out["accepted"] is False and out["reason"].startswith(
        "records requests come from the firm's staff addresses"
    )
    assert rows(seat["db"]) == []


@pytest.mark.parametrize("who", ["Pat@Firm.Example", "outside.paralegal@other.example"])
def test_a_domain_or_exact_recipient_is_accepted(seat, who):
    out = submit(seat["broker"], requester=who)
    assert out["accepted"], out
    assert rows(seat["db"])[0]["requester"] == who.lower()


def test_a_lookalike_domain_is_refused(seat):
    assert submit(seat["broker"], requester="pat@evilfirm.example")["accepted"] is False
    assert submit(seat["broker"], requester="pat@firm.example.evil.example")["accepted"] is False


def test_the_staff_list_reads_as_the_overlays_does(monkeypatch):
    """The broker's copy and the overlay's shared.matter_file_reply.staff_list
    agree on the same block (the broker cannot import the overlay)."""
    overlay = Path(__file__).resolve().parents[2] / "hermes-smd-overlay"
    spec = importlib.util.spec_from_file_location(
        "_overlay_matter_file_reply", overlay / "shared" / "matter_file_reply.py"
    )
    sys.path.insert(0, str(overlay))
    before = set(sys.modules)
    try:
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, mod)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(overlay))
        for name in set(sys.modules) - before - {spec.name}:
            if name == "shared" or name.startswith("shared."):
                monkeypatch.delitem(sys.modules, name)
    doc = yaml.safe_load(YAML.format(manual="true", enabled="true"))
    assert mod.staff_list(doc) == staff_list(doc)
    odd = {"matter_file_delivery": {"recipients": [" @X.Example ", "a@b.example", "@nodot", "plain", None]}}
    assert mod.staff_list(odd) == staff_list(odd)


def test_root_may_submit_with_no_acknowledgement(seat):
    out = submit(seat["broker"], pid=1, uid=0, ack="none")
    assert out["accepted"] and out["ack"] == "none"
    assert rows(seat["db"])[0]["ack"] == "none"


def test_a_turn_may_not_submit_with_no_acknowledgement(seat):
    out = submit(seat["broker"], ack="none")
    assert out["accepted"] is False and "only root" in out["reason"]


def test_the_agent_uid_off_the_gateway_cannot_submit(seat):
    with pytest.raises(PermissionError):
        submit(seat["broker"], pid=7, uid=AGENT_UID)


def test_a_spent_month_is_refused(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running", cents=10_000)
    out = submit(b, message_ref=REF + "2")
    assert out["accepted"] is False and out["reason"].startswith("the firm's records budget for this month is spent")
    assert len(rows(seat["db"])) == 1


def test_an_unauthored_budget_is_refused(seat):
    seat["firm"].write_text("per_job_cap_usd: 5\n")
    assert "not authored" in submit(seat["broker"])["reason"]


@pytest.mark.parametrize(("manual", "enabled"), [("true", "false"), ("false", "true")])
def test_a_disabled_or_unswitched_skill_is_refused(seat, manual, enabled):
    seat["yaml"].write_text(YAML.format(manual=manual, enabled=enabled))
    out = submit(seat["broker"])
    assert out["accepted"] is False and "nothing was queued" in out["reason"]
    assert rows(seat["db"]) == []


def test_an_unlisted_skill_is_refused(seat):
    seat["yaml"].write_text("matter_file_delivery: {recipients: ['@firm.example']}\npersonas: []\n")
    assert "not enabled" in submit(seat["broker"])["reason"]


# ---- record, review, cards ----------------------------------------------------------------------


def test_a_delivered_verdict_lands_in_review_and_opens_a_card_that_closes_on_sent(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    db: RecordsLedger = b.records.ledger
    root(b, "records_job_record", job_id=job, state="running", stage="locate")
    assert db.open_reviews() == []
    out = root(b, "records_job_record", job_id=job, verdict="delivered", cents=212,
               delivery={"review_id": REVIEW_ID, "digest": DIGEST, "decision": "none"},
               items=[{"n": 1, "outcome": "found", "files": 1}, {"n": 2, "outcome": "not_found", "files": 0}])  # fmt: skip
    assert out["job"]["state"] == "review" and out["job"]["review_id"] == REVIEW_ID
    cards = db.open_reviews()
    assert [(c["job_id"], c["reason"], c["review_id"]) for c in cards] == [(job, "matter_files_review", REVIEW_ID)]
    assert [(i["n"], i["outcome"]) for i in db.lines(job)] == [(1, "found"), (2, "not_found")]
    root(b, "records_job_record", job_id=job, delivery={"status": "sent"})
    assert db.open_reviews() == []
    row = db.read(job)
    assert row["state"] == "delivered" and json.loads(row["delivery_json"])["digest"] == DIGEST
    assert "RECORDS_JOB_REVIEW" in audit_types(seat["db"]) and audit_types(seat["db"])[-1] == "RECORDS_JOB_DELIVERED"


def test_a_hold_with_a_review_row_carries_the_hold_reason_on_its_card(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="held", reason="per_job_cap_usd: 5.00",
         delivery={"review_id": REVIEW_ID, "digest": DIGEST})  # fmt: skip
    (card,) = b.records.ledger.open_reviews()
    assert card["reason"] == "per_job_cap_usd: 5.00"


def test_a_rejected_review_fails_the_job_and_closes_its_card(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="delivered", delivery={"review_id": REVIEW_ID})
    root(b, "records_job_record", job_id=job, delivery={"status": "rejected"})
    assert b.records.ledger.read(job)["state"] == "failed" and b.records.ledger.open_reviews() == []


def test_a_held_verdict_with_no_row_is_held_and_needs_a_reason(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    with pytest.raises(ValueError, match="reason"):
        root(b, "records_job_record", job_id=job, verdict="held")
    root(b, "records_job_record", job_id=job, verdict="held", reason="scope_unresolved")
    assert b.records.ledger.read(job)["state"] == "held"
    status = call(b, "records_job_status", job_id=job)
    assert status["job"]["state"] == "held" and "reason" not in status["job"] and status["job"]["hold_class"]


def test_a_delivered_verdict_without_a_row_is_refused(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    with pytest.raises(ValueError, match="review_id"):
        root(b, "records_job_record", job_id=job, verdict="delivered")


def test_record_refuses_an_illegal_transition_and_unknown_fields(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    with pytest.raises(ValueError, match="illegal transition"):
        root(b, "records_job_record", job_id=job, state="delivered")
    with pytest.raises(ValueError, match="carries only"):
        root(b, "records_job_record", job_id=job, state="running", matter_name="Pat Example")


def test_record_resume_and_close_are_root_only(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    for action in ("records_job_record", "records_job_resume", "records_job_close"):
        with pytest.raises(PermissionError):
            call(b, action, job_id=job, state="running")


def test_close_ends_a_job_in_review_and_resume_requeues_a_failed_one(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="failed", reason="finder_failed: x")
    out = root(b, "records_job_resume", job_id=job, reason="transient")
    assert out["queued"] and (seat["queue"] / f".resume-{job}.json").is_file()
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="delivered", delivery={"review_id": REVIEW_ID, "digest": DIGEST})
    decide(seat, "reject")
    closed = root(b, "records_job_close", job_id=job, reason="answered by hand", source_ref="vfy_" + "A" * 26)
    assert closed["job"]["state"] == "failed" and b.records.ledger.open_reviews() == []


def decide(seat, word: str) -> Path:
    """Root's decision file for the review row, as review_row.decision_file names it."""
    base = seat["tmp"] / "decisions"
    base.mkdir(exist_ok=True)
    path = base / f"{REVIEW_ID}.{DIGEST}.{word}"
    path.touch()
    return path


def in_review(seat) -> str:
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="delivered", delivery={"review_id": REVIEW_ID, "digest": DIGEST})
    return job


CLOSE = {"reason": "answered by hand", "source_ref": "vfy_" + "A" * 26}


def test_close_is_refused_while_an_approve_stands(seat):
    """FALSIFIER (PR #3717 review, finding 4): drop the approve check and SMD
    closes a job whose approved reply the gateway sweeper still sends."""
    b = seat["broker"]
    job = in_review(seat)
    decide(seat, "approve")
    decide(seat, "reject")  # approve wins in the sweeper, so a later reject does not help
    for extra in ({}, {"dry_run": True}):
        with pytest.raises(ValueError, match="approved and the gateway will send it"):
            root(b, "records_job_close", job_id=job, **CLOSE, **extra)
    assert b.records.ledger.read(job)["state"] == "review"


def test_close_is_refused_while_the_row_still_waits_for_review(seat):
    b = seat["broker"]
    job = in_review(seat)
    with pytest.raises(ValueError, match="reject its row first"):
        root(b, "records_job_close", job_id=job, **CLOSE)
    assert b.records.ledger.read(job)["state"] == "review"


def test_a_decision_not_written_by_root_does_not_count(seat):
    b = seat["broker"]
    job = in_review(seat)
    decide(seat, "reject")
    b.records._decision_uid = os.getuid() + 1  # the file's owner is no longer "root"
    with pytest.raises(ValueError, match="reject its row first"):
        root(b, "records_job_close", job_id=job, **CLOSE)


def test_a_held_job_closes_with_no_review_row(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running")
    root(b, "records_job_record", job_id=job, verdict="held", reason="scope_unresolved")
    assert root(b, "records_job_close", job_id=job, **CLOSE)["job"]["state"] == "failed"


def test_root_status_reports_the_month(seat):
    b = seat["broker"]
    job = submit(b)["job_id"]
    root(b, "records_job_record", job_id=job, state="running", cents=250)
    out = root(b, "records_job_status")
    assert out["month"] == "2026-11" and out["month_cents"] == 250 and out["monthly_budget_cents"] == 10_000


def test_the_hold_sql_names_a_lane_proof_hold(seat):
    b = seat["broker"]
    job = submit(b, pid=1, uid=0, ack="none")["job_id"]
    root(b, "records_job_record", job_id=job, reason="lane_unproven: matter-records")
    conn = sqlite3.connect(seat["db"])
    try:
        assert [r[0] for r in conn.execute(HOLD_SQL)] == [job]
    finally:
        conn.close()


# ---- the verb table and the spool -----------------------------------------------------------------


def test_the_records_rows_and_their_gates():
    """FALSIFIER: open record/resume/close past root and a turn could move a
    job or close SMD's review; add the agent uid to submit and an execute_code
    child could queue a paid job."""
    gates = {v.name: v.auth for v in verbs.VERBS}
    assert gates["records_job_submit"] == frozenset({verbs.GATEWAY, verbs.ROOT})
    assert gates["records_job_status"] == frozenset({verbs.GATEWAY, verbs.ROOT, verbs.AGENT})
    for name in ("records_job_record", "records_job_resume", "records_job_close"):
        assert gates[name] == frozenset({verbs.ROOT}), name
    from workspace_broker import records_verbs

    assert set(records_verbs.VERBS) == {v.name for v in verbs.VERBS if v.handler is records_verbs.records_dispatch}


def test_the_parity_assert_fires_when_a_row_is_missing(monkeypatch):
    """The import-time parity check exists and fails on a mismatch."""
    from workspace_broker import records_verbs

    monkeypatch.setattr(records_verbs, "VERBS", (*records_verbs.VERBS, "records_job_extra"))
    spec = importlib.util.spec_from_file_location("workspace_broker._verbs_probe", verbs.__file__)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "workspace_broker"
    monkeypatch.setitem(sys.modules, spec.name, mod)
    with pytest.raises(RuntimeError, match="records rows"):
        spec.loader.exec_module(mod)


def _put_request(data: bytes) -> dict:
    return {
        "action": "attachment_put",
        "payload": {
            "offset": 0,
            "chunk_b64": base64.b64encode(data).decode(),
            "final": True,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    }


@pytest.mark.parametrize(("pid", "uid"), [(1, 0), (GATEWAY_PID, AGENT_UID)])
def test_attachment_put_admits_root_and_the_gateway(seat, pid, uid):
    data = b"%PDF-1.4 cut"
    out = verbs.dispatch(seat["broker"], _put_request(data), pid, uid)
    assert out["ok"] and (seat["tmp"] / "attachment-spool" / f"{out['handle']}.bin").read_bytes() == data


def test_attachment_put_refuses_the_agent_uid_off_the_gateway(seat):
    with pytest.raises(PermissionError):
        verbs.dispatch(seat["broker"], _put_request(b"%PDF-1.4 x"), 7, AGENT_UID)
    assert not (seat["tmp"] / "attachment-spool").exists() or not list((seat["tmp"] / "attachment-spool").glob("*"))


# ---- the RFC id is the card and dedupe key; the Graph id the reply anchor ---------------------------

RFC = "<CA1x2y3z@mail.firm.example>"


def test_the_rfc_id_is_the_request_ref_and_the_graph_id_is_kept_for_the_reply(seat):
    out = submit(seat["broker"], internet_message_id=RFC)
    (row,) = rows(seat["db"])
    assert row["request_ref"] == RFC and row["message_ref"] == REF
    queued = json.loads((seat["queue"] / f"{out['job_id']}.json").read_text())
    assert queued["message_ref"] == REF and queued["request_ref"] == RFC


def test_with_no_rfc_id_the_request_ref_is_the_message_id(seat):
    submit(seat["broker"], internet_message_id="")
    assert rows(seat["db"])[0]["request_ref"] == REF


def test_the_same_email_named_by_either_id_is_one_job(seat):
    """FALSIFIER: dedupe on request_ref alone and a root submit naming only the
    Graph id would queue a second paid job for an email a turn already queued."""
    b = seat["broker"]
    first = submit(b, internet_message_id=RFC)["job_id"]
    again = submit(b, pid=1, uid=0, ack="none")
    assert again["duplicate"] == "already_queued" and again["job_id"] == first
    other = submit(b, message_ref=REF + "2", internet_message_id=RFC)
    assert other["duplicate"] == "already_queued" and other["job_id"] == first
    assert len(rows(seat["db"])) == 1


def test_a_malformed_rfc_id_is_refused(seat):
    assert "internet_message_id" in submit(seat["broker"], internet_message_id="not an id")["reason"]


def test_the_acknowledgement_never_shows_a_smokeball_uuid(seat):
    """FALSIFIER: print any matter_id and the requester reads a uuid in the reply."""
    uuid = "1A2B3C4D-1111-4111-8111-111111111111"
    out = submit(seat["broker"], matter_id=uuid)
    assert out["accepted"] and out["ack"] == acknowledgement(None)
    assert "1a2b3c4d" not in out["ack"].lower()
    assert rows(seat["db"])[0]["matter_id"] == uuid.lower()
