"""The demand job ledger: the contract the request edge and the runner share.

Each test defends one property, and fails if the line it defends is removed:
the envelope is exact (no extra or missing field, a requester and a message id
of the right shape); the queue file always has its row; a job that never paid
does not use the allowance; an unfinished job on a matter is visible as a
duplicate; transitions are monotonic with the resume edge; and the completion
reply is claimable exactly once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.demand_ledger import DemandLedger, EnvelopeError, validate_envelope

MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"


def _env(**over):
    env = {
        "matter": {"id": MATTER, "number": "900201"},
        "file_to": None,
        "requested_by": "Admin@Firm.example",
        "request_ref": "<CA1x2y3z@mail.firm.example>",
        "request_text": "Gap audit and draft demand, please. Cite every fact.",
        "deliverables": ["demand", "gap_audit"],
    }
    env.update(over)
    return env


@pytest.fixture
def ledger(tmp_path):
    return DemandLedger(str(tmp_path / "audit.db"), tmp_path / "queue")


def test_the_envelope_is_exact_and_normalized() -> None:
    env = validate_envelope(_env())
    assert env["requested_by"] == "admin@firm.example"
    assert env["deliverables"] == ["gap_audit", "demand"]
    for bad in (
        {**_env(), "extra": 1},
        _env(requested_by="not an email"),
        _env(request_ref="no-at-sign"),
        _env(request_text=""),
        _env(deliverables=["settlement_statement"]),
        _env(matter={"id": "x", "number": "900201"}),
    ):
        with pytest.raises(EnvelopeError):
            validate_envelope(bad)


def test_submit_writes_the_row_then_the_queue_file(ledger, tmp_path) -> None:
    job = ledger.submit(_env(file_to={"id": LIBRARY, "number": "OPS-OPERATOR-LIBRARY"}))
    row = ledger.read(job)
    assert row["state"] == "submitted" and row["file_to_matter_id"] == LIBRARY
    queued = json.loads((tmp_path / "queue" / f"{job}.json").read_text())
    assert queued["kind"] == "demand" and queued["job_id"] == job
    assert queued["request_text"].startswith("Gap audit")
    assert "request_text" not in json.dumps(ledger.project(row))  # the projection carries no brief


def test_only_a_job_that_paid_uses_the_allowance(ledger) -> None:
    paid = ledger.submit(_env())
    ledger.record(paid, "running", {})
    ledger.record(paid, "delivered", {"cents": 640})
    free = ledger.submit(_env(matter={"id": LIBRARY, "number": "OPS-OPERATOR-LIBRARY"}))
    ledger.record(free, "held", {"reason": "premise: carrier not identified", "cents": 0})
    state = ledger.allowance(25)
    assert state["used"] == 1 and state["remaining"] == 24 and state["authored"] is True
    assert ledger.allowance(None)["remaining"] == 0  # unauthored: nothing may be submitted


def test_an_unfinished_job_on_the_matter_is_a_duplicate(ledger) -> None:
    job = ledger.submit(_env())
    assert ledger.active_on_matter(MATTER) == job
    ledger.record(job, "failed", {"reason": "cap"})
    assert ledger.active_on_matter(MATTER) is None


def test_transitions_are_monotonic_with_the_resume_edge(ledger) -> None:
    job = ledger.submit(_env())
    ledger.record(job, "running", {})
    ledger.record(job, "failed", {"reason": "over the cap estimate", "cents": 0})
    ledger.record(job, "running", {})  # a resume
    ledger.record(job, "delivered", {"folder_id": "fold-1", "delivery": {"files": [{"name": "a.docx", "size": 9}]}})
    with pytest.raises(ValueError):
        ledger.record(job, "running", {})
    assert DemandLedger.project(ledger.read(job))["files"] == [{"name": "a.docx", "size": 9}]


def test_the_completion_reply_is_claimed_exactly_once(ledger) -> None:
    job = ledger.submit(_env())
    assert ledger.mark_replied(job) is True
    assert ledger.mark_replied(job) is False
    assert ledger.mark_replied("no-such-job") is False
