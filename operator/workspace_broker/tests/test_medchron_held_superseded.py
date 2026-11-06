"""A chronology hold a later delivery replaced is closed by that delivery.

A 09-23 hold on a client seat stayed ``held`` after the matter was delivered
twice, until SMD set it to superseded by hand on 10-13. The delivery that
replaces a hold now moves it ``held -> failed`` with a reason starting
``superseded`` (the word the fixed wording treats as owed no note)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.tests.test_medchron_verbs import ROOT, _held, _set_updated_at, audit_types, call, envelope, verbs  # noqa: F401


def _set_created_at(v, job_id: str, when: str) -> None:
    conn = sqlite3.connect(v._db._db_path)
    conn.execute("UPDATE medchron_jobs SET created_at=? WHERE id=?", (when, job_id))
    conn.commit()
    conn.close()


def _deliver(v, job_id: str) -> None:
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="delivered", fields={})


def test_a_delivery_closes_the_earlier_hold_on_its_matter(verbs):
    v, _, _ = verbs
    held = _held(v)
    # Past the retention window, so the fresh request is not refused as its twin.
    _set_updated_at(v, held, "2000-01-01T00:00:00.000Z")
    fresh = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    other = _held(v, envelope(matter={"id": "m-2", "number": "2026-PI-200", "title": "Other v. Other"}))
    before = audit_types(v._db._db_path).count("MEDCHRON_JOB_FAILED")
    _deliver(v, fresh)
    row = v._db.read(held)
    assert row["state"] == "failed"
    assert row["reason"] == f"superseded by {fresh}"
    assert row["reason"].startswith("superseded")
    assert audit_types(v._db._db_path).count("MEDCHRON_JOB_FAILED") == before + 1
    # Falsifier: a hold on another matter is not touched.
    assert v._db.read(other)["state"] == "held"


def test_a_hold_created_after_the_delivery_stays_held(verbs):
    """Direction, as in ``_ACTIVE_TWIN_SQL``: a hold that postdates the
    delivered job is the live attempt at a newer ask."""
    v, _, _ = verbs
    held = _held(v)
    _set_updated_at(v, held, "2000-01-01T00:00:00.000Z")
    fresh = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _set_created_at(v, held, "2999-01-01T00:00:00.000Z")
    _deliver(v, fresh)
    assert v._db.read(held)["state"] == "held"
