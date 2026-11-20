"""Work SMD owes a firm person, recorded by the broker in the step it arose
(owner's ruling, 2026-11-19; ``workspace_broker/smd_owed.py``).

Every value is invented.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from workspace_broker import smd_owed
from workspace_broker.tests.test_drafting_verbs import (  # noqa: F401 - the fixture
    AGENT_UID,
    _envelope,
    call,
    seat,
    submit,
)

THIRD_MATTER = "5d7c1c55-0e0b-4a55-9a3e-0c4a5d1e2f30"
ASKING = "<CAhelp9q@mail.firm.example>"
REQUEST = "<CA1x2y3z@mail.firm.example>"


@pytest.fixture
def owed_seat(seat):
    broker, db, yaml_path, queue = seat
    broker.audit_db_path = db
    return broker, db, yaml_path, queue


def _alerts(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type = 'DELIVERY_PROOF_FAILED'").fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows if json.loads(r[0]).get("code") == smd_owed.CODE]


def _open(db: str) -> list[dict]:
    return smd_owed.open_items(db)["owed"]


def _refuse_on_our_side(broker):
    return submit(broker, file_to={"id": THIRD_MATTER, "number": "900203"})


def test_an_our_side_refusal_is_recorded_in_the_same_step_and_says_so(owed_seat) -> None:
    """FALSIFIER: drop the record, and the overlay would tell the attorney it is
    with us while nothing tells SMD."""
    broker, db, _yaml, _q = owed_seat
    out = _refuse_on_our_side(broker)
    assert out["code"] == "filing_target" and out["smd_told"] is True
    (alert,) = _alerts(db)
    (row,) = _open(db)
    assert alert["job_id"] == row["id"] and alert["kind"] == row["kind"] == smd_owed.KIND_DRAFTING
    assert row["request_ref"] == REQUEST and row["refusal_code"] == "filing_target"
    assert "envelope_json" not in row  # the open read carries no request text


def test_a_firm_side_refusal_records_nothing(owed_seat) -> None:
    broker, db, _yaml, _q = owed_seat
    out = submit(broker, requested_by="staff@firm.example")
    assert out["code"] == "not_admin" and "smd_told" not in out
    assert _alerts(db) == [] and _open(db) == []


def test_a_record_that_cannot_be_written_says_smd_was_not_told(owed_seat) -> None:
    broker, _db, _yaml, _q = owed_seat

    def dead(_row):
        raise sqlite3.OperationalError("disk I/O error")

    broker.drafting._audit_append = dead
    assert _refuse_on_our_side(broker)["smd_told"] is False


def test_a_malformed_call_is_the_models_to_correct_once_then_owed(owed_seat) -> None:
    """Review 2026-11-19: envelope_invalid was owed on the first try, so the
    firm was acknowledged, the model corrected its call, a job ran, and the
    owed item said to draft it by hand. FALSIFIER: owe the first attempt."""
    broker, db, _yaml, _q = owed_seat
    bad = {**_envelope(), "document_class": "sonnet"}
    first = call(broker, "drafting_job_submit", envelope=bad)
    assert first["code"] == "envelope_invalid" and first["correct_call"] is True
    assert "smd_told" not in first and _open(db) == []
    again = call(broker, "drafting_job_submit", envelope=bad)
    assert again["smd_told"] is True and len(_open(db)) == 1


def test_a_job_accepted_later_for_the_same_request_closes_the_owed_item(owed_seat, tmp_path) -> None:
    """FALSIFIER: leave it open and the triage says "draft it by hand" while
    the job drafts it: two drafts."""
    broker, db, _yaml, _q = owed_seat
    _refuse_on_our_side(broker)
    assert len(_open(db)) == 1
    assert submit(broker)["accepted"] is True  # the same email and class, corrected
    assert _open(db) == []


def test_the_agent_records_a_help_question_keyed_on_its_email(owed_seat) -> None:
    broker, db, _yaml, _q = owed_seat
    out = call(broker, "smd_owed_record", pid=1, uid=AGENT_UID, kind="help_question", request_ref=ASKING)
    assert out["ok"] and out["smd_told"] is True
    (alert,) = _alerts(db)
    assert (alert["kind"], alert["request_ref"], alert["lane"]) == ("help_question", ASKING, "operator")
    assert [r["kind"] for r in _open(db)] == ["help_question"]
    with pytest.raises(Exception):
        smd_owed.record_verb(broker, "smd_owed_record", {"kind": "drafting_refused", "request_ref": ASKING}, 1, 0)
    broker.ledger = None
    assert smd_owed.record_verb(broker, "x", {"kind": "rule_not_recorded", "request_ref": ASKING}, 1, 0) == {
        "ok": True,
        "smd_told": False,
    }


def test_the_open_read_has_no_lookback_and_lists_owed_served_sets(owed_seat) -> None:
    """Review 2026-11-19: owed work fell off the triage after the 192-hour
    ledger window. FALSIFIER: read the audit window instead of the table."""
    broker, db, _yaml, _q = owed_seat
    _refuse_on_our_side(broker)
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute("UPDATE smd_owed SET created_at='2026-01-02T00:00:00.000Z'")
            conn.execute(
                "INSERT INTO drafting_jobs (id, created_at, updated_at, state, matter_id, matter_number, "
                "file_to_matter_id, document_class, requester, request_ref, envelope_digest, delivery_json) "
                "VALUES ('01JSERVEDAAAAAAAAAAAAAAAAA','t','t','delivered','m','900201','m','discovery_response',"
                "'admin@firm.example','<CAsrv@mail.firm.example>','d',?)",
                (json.dumps({"served_sets": [{"set": "RFP 1", "status": "owed", "told": True}]}),),
            )
    finally:
        conn.close()
    out = call(broker, "smd_owed_open", pid=1, uid=AGENT_UID)
    assert [r["created_at"] for r in out["owed"]] == ["2026-01-02T00:00:00.000Z"]
    (job,) = out["owed_jobs"]
    assert job["id"] == "01JSERVEDAAAAAAAAAAAAAAAAA" and job["message_ref"] == "<CAsrv@mail.firm.example>"


def test_staff_open_a_refused_request_as_a_failed_job_and_a_rerun_finishes_it(owed_seat, tmp_path) -> None:
    """Nothing else could record a hand delivery for a request that never became
    a job: deliver_by_smd needs a ledger row and a job dir."""
    broker, db, _yaml, _q = owed_seat
    _refuse_on_our_side(broker)
    (row,) = _open(db)
    jobs = tmp_path / "jobs"
    owed_id = smd_owed.open_job(db, str(jobs), row["id"])
    status = broker.drafting.ledger.read(owed_id)
    assert status["state"] == "failed" and status["request_ref"] == REQUEST
    env = json.loads((jobs / owed_id / "envelope.json").read_text())
    assert (env["job_id"], env["requester"], env["message_ref"]) == (owed_id, "admin@firm.example", REQUEST)
    # A part-way failure (the daemon state lost) is finished by running again.
    (jobs / owed_id / "daemon.json").unlink()
    assert smd_owed.open_job(db, str(jobs), owed_id) == owed_id
    assert json.loads((jobs / owed_id / "daemon.json").read_text())["state"] == "failed"
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute("UPDATE drafting_jobs SET state='delivered' WHERE id=?", (owed_id,))
    finally:
        conn.close()
    with pytest.raises(ValueError):
        smd_owed.open_job(db, str(jobs), owed_id)
    assert _open(db) == []  # delivered: no longer owed
    assert smd_owed.main(["open-job", "01JNOTRECORDEDXXXXXXXXXXXX", "--db", db, "--jobs-dir", str(jobs)]) == 2
