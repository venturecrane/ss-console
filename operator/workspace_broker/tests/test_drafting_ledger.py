"""The drafting job ledger: the contract the request edge and the runner share.

Each test defends one property and fails if the line it defends is removed:
the envelope is exact; the queue file is the runner's flat shape and always
has its row; in-flight and paid jobs use the allowance; the duplicate is per
(matter, class) and per (email, class); submit is atomic; transitions are
monotonic with the resume edge; the reply is claimable once per outcome.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.drafting_ledger import (
    DOCUMENT_CLASSES,
    DraftingLedger,
    EnvelopeError,
    SubmitRefused,
    validate_envelope,
)

MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"


def _env(**over):
    env = {
        "matter": {"id": MATTER, "number": "900201"},
        "file_to": None,
        "requested_by": "Admin@Firm.example",
        "request_ref": "<CA1x2y3z@mail.firm.example>",
        "request_text": "Draft our responses to their special interrogatories.",
        "document_class": "discovery_response",
    }
    env.update(over)
    return env


@pytest.fixture
def ledger(tmp_path):
    return DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "queue")


def test_the_classes_are_the_five_the_runner_builds() -> None:
    assert DOCUMENT_CLASSES == ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")


def test_the_envelope_is_exact_and_normalized() -> None:
    env = validate_envelope(_env())
    assert env["requested_by"] == "admin@firm.example"
    for bad in (
        {**_env(), "extra": 1},
        {k: v for k, v in _env().items() if k != "document_class"},
        _env(requested_by="not an email"),
        _env(request_ref="no-at-sign"),
        _env(request_text=""),
        _env(document_class="demand"),
        _env(matter={"id": "x", "number": "900201"}),
        {**{k: v for k, v in _env().items() if k != "document_class"}, "deliverables": ["demand"]},
    ):
        with pytest.raises(EnvelopeError):
            validate_envelope(bad)


def test_submit_writes_the_row_then_the_runners_job_file(ledger, tmp_path) -> None:
    job = ledger.submit(_env(file_to={"id": LIBRARY, "number": "OPS-OPERATOR-LIBRARY"}))
    row = ledger.read(job)
    assert row["state"] == "submitted" and row["file_to_matter_id"] == LIBRARY
    assert row["document_class"] == "discovery_response"
    queued = json.loads((tmp_path / "queue" / f"{job}.json").read_text())
    assert set(queued) == {
        "kind",
        "job_id",
        "matter_id",
        "matter_number",
        "document_class",
        "requester",
        "message_ref",
        "request_text",
        "file_to_matter_id",
        "file_to_matter_number",
    }
    assert queued["kind"] == "drafting" and queued["job_id"] == job
    assert queued["file_to_matter_id"] == LIBRARY and queued["file_to_matter_number"] == "OPS-OPERATOR-LIBRARY"
    assert queued["requester"] == "admin@firm.example"
    assert "request_text" not in json.dumps(ledger.project(row))


def test_in_flight_and_paid_jobs_use_the_allowance(ledger) -> None:
    paid = ledger.submit(_env())
    ledger.record(paid, "running", {})
    ledger.record(paid, "delivered", {"cents": 640})
    running = ledger.submit(_env(document_class="memo", request_ref="<second@x.example>"))
    ledger.record(running, "running", {})
    assert ledger.allowance(25)["used"] == 2
    ledger.record(running, "held", {"reason": "the record lacks the pleadings"})
    assert ledger.allowance(25)["used"] == 1
    assert ledger.allowance(None)["remaining"] == 0


def test_the_duplicate_is_per_matter_and_class(ledger) -> None:
    """FALSIFIER: key the twin check on the matter alone and a memo blocks the brief."""
    job = ledger.submit(_env())
    assert ledger.active_on_matter(MATTER, "discovery_response") == job
    assert ledger.active_on_matter(MATTER, "memo") is None
    with pytest.raises(SubmitRefused) as twin:
        ledger.submit(_env(request_ref="<other@x.example>"))
    assert twin.value.job_id == job
    with pytest.raises(SubmitRefused) as dup:
        ledger.submit(_env(matter={"id": LIBRARY, "number": "OPS-OPERATOR-LIBRARY"}))
    assert "request email" in str(dup.value)
    assert ledger.submit(_env(document_class="memo")) != job
    ledger.record(job, "failed", {"reason": "render"})
    assert ledger.active_on_matter(MATTER, "discovery_response") is None


def test_concurrent_submits_take_one_slot(ledger) -> None:
    import threading
    import uuid

    results: list[str] = []
    barrier = threading.Barrier(8)

    def race(i: int) -> None:
        env = _env(matter={"id": str(uuid.UUID(int=i + 1)), "number": f"9000{i}"}, request_ref=f"<race{i}@x.example>")
        barrier.wait()
        try:
            results.append(ledger.submit(env, allowance=1))
        except SubmitRefused:
            results.append("refused")

    threads = [threading.Thread(target=race, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r != "refused"]) == 1


def test_spend_is_dated_when_written(ledger) -> None:
    job = ledger.submit(_env())
    ledger.record(job, "running", {"cents": 100})
    ledger.record(job, "running", {"cents": 250})
    ledger.record(job, "running", {"cents": 200})
    assert ledger.spend_between("2000-01-01T00:00:00.000Z", "2999-01-01T00:00:00.000Z") == 250
    assert ledger.spend_between("2000-01-01T00:00:00.000Z", "2999-01-01T00:00:00.000Z", exclude=job) == 0


def test_transitions_are_monotonic_with_the_resume_edge(ledger) -> None:
    job = ledger.submit(_env())
    ledger.record(job, "running", {})
    ledger.record(job, "failed", {"reason": "truncated compose"})
    row = ledger.record(job, "running", {})
    assert row["attempt"] == 2
    ledger.record(job, "delivered", {"folder_id": "f", "delivery": {"files": [{"name": "a.docx", "size": 9}]}})
    with pytest.raises(ValueError):
        ledger.record(job, "running", {})
    held = ledger.submit(_env(document_class="memo"))
    ledger.record(held, "held", {"reason": "premise"})
    with pytest.raises(ValueError):
        ledger.record(held, "running", {})  # held is final; only failed resumes


def test_the_completion_reply_is_claimed_once_per_outcome(ledger) -> None:
    job = ledger.submit(_env())
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    assert ledger.mark_replied(job, "1:delivered") is True
    assert ledger.mark_replied(job, "1:delivered") is False
    ledger.unmark_replied(job, "1:delivered")
    assert ledger.mark_replied(job, "1:delivered") is True
    assert ledger.mark_replied("no-such-job") is False


def test_a_note_with_only_markers_keeps_the_stored_files(ledger) -> None:
    """FALSIFIER: write the incoming report over the stored one and a later
    markers-only note erases delivery.files."""
    job = ledger.submit(_env())
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {"delivery": {"files": [{"name": "a.docx", "size": 9, "role": "draft"}]}})
    ledger.record(job, "delivered", {"markers": [{"kind": "ATTORNEY", "text": "target figure"}]})
    proj = DraftingLedger.project(ledger.read(job))
    assert proj["files"] == [{"name": "a.docx", "size": 9, "role": "draft"}]
    assert proj["markers"] == [{"kind": "ATTORNEY", "text": "target figure"}]
    # Incoming keys win over the stored ones.
    ledger.record(job, "delivered", {"delivery": {"files": [{"name": "b.docx", "size": 3}]}})
    proj = DraftingLedger.project(ledger.read(job))
    assert proj["files"] == [{"name": "b.docx", "size": 3}] and proj["markers"][0]["kind"] == "ATTORNEY"
