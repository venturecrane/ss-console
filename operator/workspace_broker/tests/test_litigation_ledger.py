"""The litigation status ledger (litigation_ledger.py): the envelope's exact
shape, the queue file, the transitions, spend, the projection's counts-only
view and the once-per-outcome reply mark."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.litigation_ledger import (
    AUDIT_TYPE,
    EnvelopeError,
    LitigationLedger,
    SubmitRefused,
    scope_key,
    validate_envelope,
)

LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
STAFF = "11111111-2222-3333-4444-555555555555"


def _env(**over):
    env = {
        "trigger": "request",
        "requester": "Admin@Firm.example",
        "message_ref": "<abc1@mail.firm.example>",
        "request_text": "status list please",
        "scope": {"all": True},
        "file_to_matter_id": LIBRARY,
        "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
        "folder_name": "Litigation Status",
    }
    env.update(over)
    return env


@pytest.fixture
def ledger(tmp_path):
    return LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "queue")


def test_the_audit_vocabulary_is_the_frozen_set() -> None:
    assert sorted(AUDIT_TYPE.values()) == sorted(
        f"LITIGATION_JOB_{s}" for s in ("SUBMITTED", "RUNNING", "HELD", "DELIVERED", "FAILED")
    )


def test_the_envelope_normalizes_and_refuses_anything_else() -> None:
    env = validate_envelope(_env())
    assert env["requester"] == "admin@firm.example"
    with pytest.raises(EnvelopeError):
        validate_envelope({**_env(), "extra": 1})
    with pytest.raises(EnvelopeError):
        validate_envelope(_env(trigger="webhook"))
    with pytest.raises(EnvelopeError):
        validate_envelope(_env(trigger="scheduled"))  # a scheduled ref is scheduled:<date>
    assert validate_envelope(_env(trigger="scheduled", message_ref="scheduled:2026-10-08"))
    with pytest.raises(EnvelopeError):
        validate_envelope(_env(scope={"attorney_staff_ids": []}))
    with pytest.raises(EnvelopeError):
        validate_envelope(_env(scope={"attorneys": ["Name"]}))  # names are resolved before the ledger


def test_scope_keys_are_order_free() -> None:
    a = validate_envelope(_env(scope={"attorney_staff_ids": [STAFF, LIBRARY]}))["scope"]
    b = validate_envelope(_env(scope={"attorney_staff_ids": [LIBRARY, STAFF]}))["scope"]
    assert scope_key(a) == scope_key(b) != "all"


def test_submit_writes_the_row_then_the_queue_file(ledger) -> None:
    job = ledger.submit(_env())
    record = json.loads((ledger.queue_dir / f"{job}.json").read_text())
    assert record["kind"] == "litigation" and record["job_id"] == job
    assert ledger.read(job)["state"] == "queued"


def test_a_held_job_frees_its_scope(ledger) -> None:
    job = ledger.submit(_env())
    with pytest.raises(SubmitRefused):
        ledger.submit(_env(message_ref="<abc2@mail.firm.example>"))
    ledger.record(job, "held", {"reason": "scope_empty: nothing open"})
    assert ledger.submit(_env(message_ref="<abc2@mail.firm.example>"))


def test_spend_is_the_delta_and_files_are_metadata_only(ledger) -> None:
    job = ledger.submit(_env())
    ledger.record(job, "running", {"cents": 100})
    ledger.record(job, "running", {"cents": 250})
    assert ledger.spend_between("2000-01-01", "2999-01-01") == 250
    files = [{"role": "workbook", "name": "x.xlsx", "size": 9, "sha256": "b" * 64, "file_id": "f", "rows": [1]}]
    row = ledger.record(job, "delivered", {"files": files})
    assert json.loads(row["delivery_json"])["files"][0] == {
        "role": "workbook",
        "name": "x.xlsx",
        "size": 9,
        "sha256": "b" * 64,
        "file_id": "f",
    }
    with pytest.raises(ValueError):
        ledger.record(job, "delivered", {"files": [{"name": "x", "size": -1, "sha256": "b" * 64}]})


def test_the_projection_omits_the_ref_and_scope(ledger) -> None:
    job = ledger.submit(_env())
    out = LitigationLedger.project(ledger.read(job))
    assert out["job_id"] == job and out["file"] is None
    assert "request_ref" not in out and "scope_json" not in out


def test_the_reply_mark_is_once_per_key(ledger) -> None:
    job = ledger.submit(_env())
    assert ledger.mark_replied(job, "1:delivered") is True
    assert ledger.mark_replied(job, "1:delivered") is False
    ledger.unmark_replied(job, "1:delivered")
    assert ledger.mark_replied(job, "1:delivered") is True
