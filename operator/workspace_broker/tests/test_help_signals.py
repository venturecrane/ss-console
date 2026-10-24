"""help_job_signals: the help review's job outcomes (ss-console#3423, increment 3).

What these pin, each with the harm it prevents:

* only held, failed and lane-unproven jobs inside the window come back, so a
  delivered job is never counted as a struggle;
* a hold carries its firm-safe class and never the reason's text, so no gate
  name or document word reaches the review;
* a lane whose table is absent is skipped, a ledger that cannot be opened
  RAISES (the review then says "could not look", never "nothing held");
* the verb is open to the agent uid and root, and to nobody else.

Run::

    cd operator && python3 -m pytest workspace_broker/tests/test_help_signals.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import help_signals, verbs

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _iso(delta_days: float) -> str:
    return (NOW - timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


_COLS = "(id TEXT, state TEXT, reason TEXT, request_ref TEXT, updated_at TEXT, matter_number TEXT)"
_INS = "(id, state, reason, request_ref, updated_at, matter_number) VALUES (?,?,?,?,?,?)"
#: Fixed statements per table (no table name is composed into SQL).
_CREATE = {
    "demand_jobs": "CREATE TABLE demand_jobs " + _COLS,
    "medchron_jobs": "CREATE TABLE medchron_jobs " + _COLS,
    "litigation_jobs": "CREATE TABLE litigation_jobs " + _COLS,
}
_INSERT = {
    "demand_jobs": "INSERT INTO demand_jobs " + _INS,
    "medchron_jobs": "INSERT INTO medchron_jobs " + _INS,
    "litigation_jobs": "INSERT INTO litigation_jobs " + _INS,
}


def _ledger(tmp_path: Path, *, tables=("demand_jobs", "medchron_jobs", "litigation_jobs")) -> Path:
    path = tmp_path / "audit.db"
    conn = sqlite3.connect(path)
    for table in tables:
        conn.execute(_CREATE[table])
    conn.commit()
    conn.close()
    return path


def _job(path: Path, table: str, state: str, reason: str | None, ref: str | None, days_ago: float) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_INSERT[table], ("J", state, reason, ref, _iso(days_ago), "123456"))
    conn.commit()
    conn.close()


def test_only_held_failed_and_lane_unproven_jobs_in_the_window_are_returned(tmp_path):
    """FALSIFIER: drop the state filter and a delivered job reads as a struggle."""
    path = _ledger(tmp_path)
    _job(path, "demand_jobs", "delivered", None, "<a@x>", 1)
    _job(path, "demand_jobs", "failed", "runner exited 1", "<b@x>", 1)
    _job(path, "demand_jobs", "submitted", "lane_unproven:demand", "<c@x>", 2)
    _job(path, "medchron_jobs", "held", "identity: a record names someone else", "<d@x>", 3)
    _job(path, "medchron_jobs", "held", "identity: old", "<e@x>", 9)  # outside the window
    _job(path, "litigation_jobs", "held", "scope_empty: nothing open", "<f@x>", 1)

    out = help_signals.read_rows(str(path), days=7, now=NOW)
    rows = sorted(out["rows"], key=lambda r: r["request_ref"])
    assert [r["request_ref"] for r in rows] == ["<b@x>", "<c@x>", "<d@x>", "<f@x>"]
    by_ref = {r["request_ref"]: r for r in rows}
    assert by_ref["<b@x>"] == {"lane": "demand", "state": "failed", "hold_class": None, "request_ref": "<b@x>"}
    assert by_ref["<c@x>"]["hold_class"] == "lane_unproven"
    assert by_ref["<d@x>"]["hold_class"] == "another_person"
    assert by_ref["<f@x>"]["hold_class"] == "nothing_in_scope"
    assert set(out["lanes_read"]) == {"demand", "medchron", "litigation"}


def test_no_reason_text_or_matter_leaves_the_verb(tmp_path):
    path = _ledger(tmp_path)
    _job(path, "medchron_jobs", "held", "identity: a record names Dana Example", "<d@x>", 1)
    blob = json.dumps(help_signals.read_rows(str(path), days=7, now=NOW))
    assert "Dana" not in blob
    assert "123456" not in blob
    assert "identity:" not in blob


def test_an_absent_lane_is_skipped_and_an_unreadable_ledger_raises(tmp_path):
    path = _ledger(tmp_path, tables=("demand_jobs",))
    assert help_signals.read_rows(str(path), days=7, now=NOW)["lanes_read"] == ["demand"]
    with pytest.raises(Exception):
        help_signals.read_rows(str(tmp_path / "missing.db"), days=7, now=NOW)
    with pytest.raises(ValueError):
        help_signals.read_rows(None, days=7, now=NOW)


def test_the_window_is_clamped():
    assert help_signals._window({}) == 7
    assert help_signals._window({"days": 0}) == 1
    assert help_signals._window({"days": 400}) == 30
    assert help_signals._window({"days": True}) == 7
    assert help_signals._window({"days": "7"}) == 7


def test_the_verb_is_the_agent_uid_and_root_only():
    """FALSIFIER: open it to ANY and an unauthenticated peer reads job outcomes."""
    verb = verbs.TABLE[help_signals.VERB]
    assert verb.auth == frozenset({verbs.GATEWAY, verbs.ROOT, verbs.AGENT})


def test_help_job_signals_dispatches_for_the_agent_uid(tmp_path):
    """Driven through the table, the way the initiation plugin's review calls it."""
    from workspace_broker.server import Broker

    path = _ledger(tmp_path)
    _job(path, "demand_jobs", "failed", "runner exited 1", "<b@x>", 0.5)
    broker = Broker.__new__(Broker)
    broker.gateway_pid = 4242
    broker.agent_uid = 1001
    broker.audit_db_path = str(path)
    reply = verbs.dispatch(broker, {"action": "help_job_signals", "days": 7}, 9999, 1001)
    assert reply["ok"] is True and reply["window_days"] == 7
    assert [r["lane"] for r in reply["rows"]] == ["demand"]
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, {"action": "help_job_signals"}, 9999, 2002)
