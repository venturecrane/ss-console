"""A re-run of a finished demand job (demand_job_rerun, root only; Martello, 2026-10-06).

A NEW job with the same envelope (digest-checked against the old job), naming
the job it supersedes, so its completion reply binds afresh in the requester's
thread. Every submit check still applies.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_demand_verbs import call, seat, submit  # noqa: F401 - the shared fixture

REF = "<CA1x2y3z@mail.firm.example>"


def _queued_envelope(queue: Path, job_id: str) -> dict:
    """What root reads back (the queued file; the runner's envelope.json)."""
    return json.loads((queue / f"{job_id}.json").read_text())


def _rerun(broker, job_id, envelope, **kw):
    return call(broker, "demand_job_rerun", job_id=job_id, envelope=envelope, **kw)


def test_a_delivered_job_reruns_as_a_new_job(seat) -> None:
    broker, db, _y, queue = seat
    old = submit(broker)["job_id"]
    call(broker, "demand_job_record", uid=0, job_id=old, state="running", fields={})
    call(broker, "demand_job_record", uid=0, job_id=old, state="delivered", fields={"cents": 500})
    out = _rerun(broker, old, _queued_envelope(queue, old), uid=0)
    assert out["accepted"] is True and out["job_id"] != old
    conn = sqlite3.connect(db)
    supersedes, ref = conn.execute(
        "SELECT supersedes, request_ref FROM demand_jobs WHERE id=?", (out["job_id"],)
    ).fetchone()
    conn.close()
    assert supersedes == old and ref == REF
    assert _queued_envelope(queue, out["job_id"])["supersedes"] == old
    # Once per superseded job; and the same email still cannot submit afresh.
    assert _rerun(broker, old, _queued_envelope(queue, old), uid=0)["accepted"] is False
    assert submit(broker)["accepted"] is False


def test_a_rerun_is_root_only(seat) -> None:
    broker, *_ = seat
    with pytest.raises(PermissionError):
        _rerun(broker, "x", {})


def test_a_rerun_while_the_matter_has_an_active_job_is_refused(seat) -> None:
    broker, _db, _y, queue = seat
    old = submit(broker)["job_id"]
    call(broker, "demand_job_record", uid=0, job_id=old, state="failed", fields={"reason": "x"})
    other = submit(broker, request_ref="<another@mail.firm.example>")["job_id"]
    out = _rerun(broker, old, _queued_envelope(queue, old), uid=0)
    assert out["accepted"] is False and other in out["reason"]


def test_a_rerun_must_carry_the_original_envelope(seat) -> None:
    """FALSIFIER: drop the digest check and root can queue any text under an old job."""
    broker, _db, _y, queue = seat
    old = submit(broker)["job_id"]
    call(broker, "demand_job_record", uid=0, job_id=old, state="failed", fields={"reason": "x"})
    env = _queued_envelope(queue, old)
    env["request_text"] = "something else entirely"
    out = _rerun(broker, old, env, uid=0)
    assert out["accepted"] is False and "original envelope" in out["reason"]


def test_an_unfinished_job_does_not_rerun(seat) -> None:
    broker, _db, _y, queue = seat
    old = submit(broker)["job_id"]
    out = _rerun(broker, old, _queued_envelope(queue, old), uid=0)
    assert out["accepted"] is False and "finished" in out["reason"]


def test_a_seat_db_with_the_old_unique_index_upgrades(tmp_path) -> None:
    """Live seats hold uq_demand_jobs_request_ref on request_ref alone, which
    would refuse every re-run. FALSIFIER: drop the DROP INDEX on upgrade."""
    from workspace_broker.demand_ledger import DemandLedger

    db = str(tmp_path / "audit.db")
    DemandLedger(db, tmp_path / "q")
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX IF EXISTS uq_demand_jobs_request_ref_rerun")
    conn.execute("CREATE UNIQUE INDEX uq_demand_jobs_request_ref ON demand_jobs(request_ref)")
    conn.commit()
    conn.close()
    ledger = DemandLedger(db, tmp_path / "q")
    env = {
        "matter": {"id": "b041dd06-30a4-4c1f-912b-27724bd77a64", "number": "900201"},
        "file_to": None,
        "requested_by": "admin@firm.example",
        "request_ref": REF,
        "request_text": "Gap audit and draft demand.",
        "deliverables": ["gap_audit", "demand"],
    }
    old = ledger.submit(env)
    ledger.record(old, "failed", {"reason": "x"})
    assert ledger.submit(env, supersedes=old) != old
