"""A running drafting job's row says where it is (2026-11-24).

A job pulling about 500 files showed cents 0 and an ``updated_at`` frozen for
26 minutes: ops-requests and the hourly triage could not tell a long pull from
a hung one, and its expected finish and stage timings went with the run dir.
Each test fails if the line it defends is removed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_drafting_verbs import call, seat, submit  # noqa: F401 - seat is the fixture these tests take


def _progress_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type='DRAFTING_JOB_PROGRESS'").fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


def _running(broker) -> str:
    jid = submit(broker)["job_id"]
    call(broker, "drafting_job_record", uid=0, job_id=jid, state="running", fields={})
    return jid


def _note(broker, jid, **fields):
    return call(broker, "drafting_job_record", uid=0, job_id=jid, state="running", fields=fields)


def test_a_note_puts_the_stage_and_the_pull_count_on_the_row_and_moves_updated_at(seat) -> None:
    """FALSIFIER: drop the note's UPDATE (or its columns) and the row reads as it did at start."""
    broker, db, _yaml, _queue = seat
    jid = _running(broker)
    before = broker.drafting.ledger.read(jid)["updated_at"]
    _note(broker, jid, stage="pull", progress="120/502", expected_finish="2026-11-24T21:40:00Z")
    row = broker.drafting.ledger.read(jid)
    assert (row["stage"], row["progress"], row["expected_finish"]) == ("pull", "120/502", "2026-11-24T21:40:00Z")
    assert row["state"] == "running" and row["updated_at"] >= before


def test_only_a_moved_count_writes_an_audit_row(seat) -> None:
    """A refresh bumps the row but is not work: the triage reads a frozen count as
    a stall. FALSIFIER: audit every note and a hung pull looks alive forever."""
    broker, db, _yaml, _queue = seat
    jid = _running(broker)
    _note(broker, jid, stage="pull", progress="8/502")
    _note(broker, jid, stage="pull", progress="8/502")
    _note(broker, jid, stage="pull", progress="16/502")
    assert [r["progress"] for r in _progress_rows(db)] == ["8/502", "16/502"]
    assert all(set(r) == {"job_id", "stage", "progress"} for r in _progress_rows(db))


def test_a_note_never_moves_a_finished_row(seat) -> None:
    """One in flight as the job ends must not bring it back to running."""
    broker, _db, _yaml, _queue = seat
    jid = _running(broker)
    call(broker, "drafting_job_record", uid=0, job_id=jid, state="failed", fields={"cents": 0, "reason": "x"})
    with pytest.raises(ValueError):
        _note(broker, jid, stage="pull", progress="1/2")
    assert broker.drafting.ledger.read(jid)["state"] == "failed"


def test_an_ending_keeps_the_stage_timings_after_the_run_dir_is_gone(seat) -> None:
    """FALSIFIER: drop ``drafting_progress.keep`` from the ledger's apply and the
    timings are lost with the run dir."""
    broker, _db, _yaml, _queue = seat
    jid = _running(broker)
    fields = {
        "cents": 120,
        "reason": "x",
        "stage_seconds": {"pull": 1560.4, "preflight": 3.0},
        "expected_finish": "2026-11-24T21:40:00Z",
    }
    call(broker, "drafting_job_record", uid=0, job_id=jid, state="failed", fields=fields)
    row = broker.drafting.ledger.read(jid)
    assert json.loads(row["stage_seconds_json"]) == {"pull": 1560.4, "preflight": 3.0}
    assert row["expected_finish"] == "2026-11-24T21:40:00Z"


@pytest.mark.parametrize(
    "fields",
    [
        {"stage": "Pull files"},
        {"stage": "pull", "progress": "a lot"},
        {"stage": "pull", "stage_seconds": {"pull": "long"}},
        {"stage": "pull", "expected_finish": "tonight"},
    ],
)
def test_a_note_is_parsed_never_cast(seat, fields) -> None:
    broker, _db, _yaml, _queue = seat
    jid = _running(broker)
    with pytest.raises(ValueError):
        _note(broker, jid, **fields)
