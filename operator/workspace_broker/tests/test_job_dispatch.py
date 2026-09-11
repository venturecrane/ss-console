"""Broker dispatch tests for the B1 job verbs.

These cover the *glue*: that job verbs are gateway-PID-gated (an execute_code
child with a different peer PID is rejected) and route to the ledger. The
ledger logic itself is covered in test_job_ledger.py.
"""

from __future__ import annotations

import pytest

from workspace_broker.job_ledger import JobLedgerWriter
from workspace_broker.server import Broker

GATEWAY_PID = 4321
CHILD_PID = 9999


def _broker(tmp_path) -> Broker:
    # Build via __new__ to skip env/credential setup; wire only what the job
    # path touches (gateway_pid + job_ledger). The audit ledger stays disabled.
    b = Broker.__new__(Broker)
    b.gateway_pid = GATEWAY_PID
    b.ledger = None
    b.job_ledger = JobLedgerWriter(str(tmp_path / "broker.db"))
    return b


def _create_row() -> dict:
    return {
        "customer_slug": "acme",
        "persona_id": "intake-coordinator",
        "brief": "Long multi-document review.",
        "budget_cents": 500,
    }


def test_job_create_and_read_route_through_broker(tmp_path):
    b = _broker(tmp_path)
    created = b.handle({"action": "job_create", "row": _create_row()}, GATEWAY_PID)
    assert created["ok"] is True
    job_id = created["id"]

    read = b.handle({"action": "job_read", "job_id": job_id}, GATEWAY_PID)
    assert read["job"]["status"] == "queued"
    assert read["job"]["budget_cents"] == 500


def test_job_verbs_reject_non_gateway_peer(tmp_path):
    """The core gating property: an execute_code child (different peer PID)
    cannot drive the job ledger."""
    b = _broker(tmp_path)
    with pytest.raises(PermissionError):
        b.handle({"action": "job_create", "row": _create_row()}, CHILD_PID)
    with pytest.raises(PermissionError):
        b.handle({"action": "job_claim", "job_id": "x", "worker_id": "w"}, CHILD_PID)


def test_claim_record_flow_through_broker(tmp_path):
    b = _broker(tmp_path)
    job_id = b.handle({"action": "job_create", "row": _create_row()}, GATEWAY_PID)["id"]

    claim = b.handle({"action": "job_claim", "job_id": job_id, "worker_id": "w1"}, GATEWAY_PID)
    epoch = claim["lease_epoch"]
    assert epoch == 1

    rec = b.handle(
        {"action": "job_record", "job_id": job_id, "lease_epoch": epoch, "fields": {"spent_cents": 42}},
        GATEWAY_PID,
    )
    assert rec["result"] is True
    # Stale epoch is fenced out at the dispatch boundary too (ok=processed,
    # result=False).
    stale = b.handle(
        {"action": "job_record", "job_id": job_id, "lease_epoch": epoch - 1, "fields": {"spent_cents": 999}},
        GATEWAY_PID,
    )
    assert stale["ok"] is True
    assert stale["result"] is False


def test_idempotency_decision_through_broker(tmp_path):
    b = _broker(tmp_path)
    job_id = b.handle({"action": "job_create", "row": _create_row()}, GATEWAY_PID)["id"]
    epoch = b.handle({"action": "job_claim", "job_id": job_id, "worker_id": "w1"}, GATEWAY_PID)["lease_epoch"]

    begin = b.handle(
        {"action": "job_idem_begin", "job_id": job_id, "lease_epoch": epoch, "step_key": "send:x"},
        GATEWAY_PID,
    )
    assert begin["decision"] == "proceed"
    done = b.handle(
        {"action": "job_idem_complete", "job_id": job_id, "lease_epoch": epoch, "step_key": "send:x"},
        GATEWAY_PID,
    )
    assert done["result"] is True


def test_unconfigured_job_ledger_raises(tmp_path):
    b = _broker(tmp_path)
    b.job_ledger = None
    with pytest.raises(ValueError):
        b.handle({"action": "job_read", "job_id": "x"}, GATEWAY_PID)


def test_missing_required_args_raise(tmp_path):
    b = _broker(tmp_path)
    with pytest.raises(ValueError):
        b.handle({"action": "job_read"}, GATEWAY_PID)  # no job_id
    with pytest.raises(ValueError):
        b.handle({"action": "job_record", "job_id": "x"}, GATEWAY_PID)  # no lease_epoch
