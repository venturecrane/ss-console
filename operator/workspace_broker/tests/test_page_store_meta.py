"""A job's page-store record rides its final record onto the audit row and
never reaches a ledger column (wave 5, ``page_store_meta``): the hourly
work-health digest reads it there, and every lane ledger refuses a field it
does not know, so a record carrying it must not be refused."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker import page_store_meta
from test_negotiation_verbs import call, seat, submit  # noqa: F401 - seat is the module's fixture


def test_take_removes_it_and_keeps_counts_only() -> None:
    fields = {"cents": 1, "page_store": {"store": "on", "hits": 3, "misses": -1, "text": "page words"}}
    assert page_store_meta.take(fields) == {"store": "on", "hits": 3, "misses": 0, "repeats": 0}
    assert fields == {"cents": 1}
    assert page_store_meta.take({"page_store": {"store": "maybe"}}) is None
    assert page_store_meta.take({}) is None
    assert page_store_meta.add({"a": 1}, None) == {"a": 1}


def test_a_negotiation_record_carrying_it_is_accepted_and_audited(seat) -> None:
    broker, db, _y, _q = seat
    job = submit(broker)["job_id"]
    call(broker, "negotiation_job_record", uid=0, job_id=job, state="running", fields={})
    store = {"store": "on", "hits": 2, "misses": 1}
    out = call(
        broker, "negotiation_job_record", uid=0, job_id=job, state="delivered",
        fields={"cents": 10, "notices": [], "page_store": store},
    )  # fmt: skip
    assert out["ok"] is True
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type = 'NEGOTIATION_JOB_DELIVERED'").fetchall()
    finally:
        conn.close()
    assert [json.loads(r[0])["page_store"] for r in rows] == [{**store, "repeats": 0}]
