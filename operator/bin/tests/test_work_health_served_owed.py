"""The hourly triage lists a delivered response job while SMD still owes a served set.

The drafting lane raises one ``served_owed`` proof alert when a response job
delivers its Operator-drafted sets and lists others as "being prepared by us"
(``runners/jobs/jobs/job_notes.tell_owed``). The broker writes it to the seat's
audit ledger as a DELIVERY_PROOF_FAILED row, which work-health reads. It stays
listed, however old, until SMD records the set (``jobs.drafting.deliver_by_smd
--set``), which writes a fresh DELIVERY_PROVEN row. Rows here are written by
the REAL broker and read back the way the seam serves them. Values invented.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health as wh
from bin.lib import work_health_proof as proof
from workspace_broker import delivery_proof
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

CFG = wh.load_config()
SEAT = "example-seat"
JOB = "01M73RVKPTYZP2A1CF3R5P0ERW"
OWED = "1 set owed by SMD: Responses to Form Interrogatories, Set One"


def _seat(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _rows(tmp_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT id, ts, action_type, skill_name, metadata FROM audit_log")]
    finally:
        conn.close()


def _proven(broker: Broker) -> None:
    files = [{"file_id": "f1", "size": 10, "sha256": "a" * 64}]
    request = {"lane": "drafting", "job_id": JOB, "attempt": 1, "review": "released", "files": files}
    delivery_proof.record_verb(broker, "delivery_proof_record", request, 1, 0)


def test_an_owed_set_is_listed_until_smd_records_it_however_old(tmp_path):
    """MUTATION: drop the served_owed branch and the line says NOT proven delivered,
    or falls off after the ended-job window while the set is still owed."""
    broker = _seat(tmp_path)
    delivery_proof.append_alert(broker, lane="drafting", job_id=JOB, code="served_owed", attempt=1, detail=OWED)
    later = datetime.now(timezone.utc) + timedelta(hours=CFG.ended_job_hours + 48)
    (it,) = proof.delivery_proof_items(SEAT, _rows(tmp_path), CFG, later)
    assert it.kind == "job_hand_step" and it.key.endswith(f"proof:{JOB}:served_owed")
    assert "Responses to Form Interrogatories, Set One" in it.what and "NOT proven delivered" not in it.what
    assert f"deliver_by_smd {JOB}" in it.todo
    _proven(broker)  # SMD recorded it: deliver_by_smd --set writes the job's proof again
    assert proof.delivery_proof_items(SEAT, _rows(tmp_path), CFG, later + timedelta(minutes=1)) == []


def test_a_later_failure_of_the_same_job_never_replaces_the_owed_line(tmp_path):
    """FALSIFIER (final review, 11-19): the ledger kept only the last failure per
    job, so a later code on the same job made the owed set vanish."""
    broker = _seat(tmp_path)
    delivery_proof.append_alert(broker, lane="drafting", job_id=JOB, code="served_owed", attempt=1, detail=OWED)
    delivery_proof.append_alert(broker, lane="drafting", job_id=JOB, code="wake_lost", attempt=1, detail="x")
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    keys = [i.key for i in proof.delivery_proof_items(SEAT, _rows(tmp_path), CFG, now)]
    assert any(k.endswith(f"proof:{JOB}:served_owed") for k in keys), keys


FORM = "Responses to Form Interrogatories, Set One"
REF = "<request@mail.firm.example>"


def _job(*sets: dict) -> dict:
    return {
        "id": JOB,
        "state": "delivered",
        "matter_number": "900301",
        "message_ref": REF,
        "updated_at": "2026-11-01T10:00:00Z",
        "delivery_json": json.dumps({"files": [], "served_sets": list(sets)}),
    }


OPERATOR_SET = {"set": "Responses to Special Interrogatories, Set One", "drafted_by": "operator", "status": "filed"}
PREPARING = {"set": FORM, "drafted_by": "smd", "status": "being prepared by us", "document": None}
FILED = {**PREPARING, "status": "filed", "document": "F.docx", "told": False, "recorded_at": "2026-11-02T10:00:00Z"}


def test_an_owed_set_is_read_from_the_ledger_row_with_no_time_cutoff():
    """No audit row at all, a month later: the delivered row still says it is owed."""
    later = datetime(2026, 12, 15, tzinfo=timezone.utc)
    (it,) = proof.delivery_proof_items(SEAT, [], CFG, later, owed_jobs=[_job(OPERATOR_SET, PREPARING)])
    assert it.key.endswith(f"proof:{JOB}:served_owed:{FORM}") and "being prepared by us" in it.what


def test_a_filed_set_stays_listed_until_the_attorney_is_told():
    now = datetime.now(timezone.utc)
    (it,) = proof.delivery_proof_items(SEAT, [], CFG, now, owed_jobs=[_job(OPERATOR_SET, FILED)])
    assert it.key.endswith(":told") and f'Tell the attorney: {FORM}: filed as "F.docx"' in it.todo
    told = {**FILED, "told": True}
    assert proof.delivery_proof_items(SEAT, [], CFG, now, owed_jobs=[_job(told)]) == []


def _sent(ts: str, ref: str) -> dict:
    return {"id": ts, "ts": ts, "action_type": "REPLY_SENT", "metadata": json.dumps({"in_reply_to": ref})}


def test_a_reply_in_the_request_thread_after_the_record_tells_the_attorney():
    now = datetime.now(timezone.utc)
    before = [_sent("2026-11-02T09:00:00Z", REF)]  # before the set was recorded: not about it
    other = [_sent("2026-11-03T09:00:00Z", "<another@mail.firm.example>")]  # another thread
    after = [_sent("2026-11-03T09:00:00Z", REF)]
    for rows, listed in ((before, 1), (other, 1), (after, 0)):
        got = proof.delivery_proof_items(SEAT, rows, CFG, now, owed_jobs=[_job(FILED)])
        assert len(got) == listed, rows


def test_the_code_is_one_every_vocabulary_accepts():
    assert "served_owed" in delivery_proof.CODES and proof.OWED == "served_owed"
