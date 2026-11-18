"""A cut window never grades an aided chronology run unaided
(``bin/lib/work_health_runs.summarize``), proven on rows the REAL chronology lane
and broker wrote (``lifecycle_matrix.py``'s seat), never on a hand-written list.

Case 1: a job held, SMD resumed it, and both the hold and the resume are older
than the window's start; its delivery is inside. Case 2: a run failed just
before the window and an unlinked resubmission of the same matter delivered
inside it, within 7 days. Each MUTATION is the grading the defect had (every
run in the cut rows graded, rows read from the window start only): it counts
the aided run unaided.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from lifecycle_matrix import AUDIT_HOLD, Life, _envelope, _resume, build, delivered, ended, teardown

from bin.lib import work_health_runs as runs_wh

DAY = 86400.0


def _rows(life: Life) -> list[dict[str, Any]]:
    conn = sqlite3.connect(life.seat.audit_db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT id, ts, action_type, matter_ref, metadata FROM audit_log")]
    finally:
        conn.close()


def _read(rows: list[dict[str, Any]], start: datetime) -> list[dict[str, Any]]:
    """What a reader that starts at ``start`` gets (``read_ledger`` pages from a ULID floor)."""
    return [r for r in rows if (runs_wh.wh.parse_ts(r["ts"]) or start) >= start]


def _at(life: Life) -> datetime:
    return datetime.fromtimestamp(life.seat.clock(), timezone.utc)


def _submit(life: Life, **work: Any) -> str:
    """Through the broker's own submit verb, as the Operator's tool sends it (its
    SUBMITTED audit row is the run's start)."""
    reply = life.client()._request(
        {"action": "medchron_job_submit", "envelope": {**_envelope("2026-PI-102", "m-1"), **work}}
    )
    assert reply.get("accepted"), reply
    return str(reply["job_id"])


def _scene(tmp_path: Any, mp: Any, body: Any) -> Any:
    life, server = build(tmp_path, mp)
    try:
        with server as port:
            life.run.build(port)
            return body(life)
    finally:
        teardown(life)


def _case_resumed_before_the_window(life: Life) -> tuple[list[dict[str, Any]], datetime]:
    jid = _submit(life)
    life.children.append(ended("held", "audit", AUDIT_HOLD))
    life.tick()
    _resume(life, jid)
    # The resumed run starts now and delivers ten days later, inside the window.
    life.during.append(lambda: life.seat.now.update(t=life.seat.now["t"] + 10 * DAY))
    life.children.append(delivered())
    life.tick()
    life.settle()
    return _rows(life), _at(life) - timedelta(days=2)


def test_a_resumed_run_whose_hold_and_resume_are_before_the_window_is_never_unaided(
    tmp_path, monkeypatch, real_lane_gate
):
    rows, since = _scene(tmp_path, monkeypatch, _case_resumed_before_the_window)
    kinds = [(r["action_type"], json.loads(r["metadata"] or "{}").get("run", {}).get("by")) for r in _read(rows, since)]
    assert ("MEDCHRON_JOB_DELIVERED", "code") in kinds and all(k != "MEDCHRON_JOB_HELD" for k, _ in kinds), kinds
    # MUTATION: the cut rows, every run graded: the aided run reads unaided.
    assert runs_wh.summarize(_read(rows, since))["unaided"] == 1
    got = runs_wh.summarize(_read(rows, runs_wh.read_from(since)), since=since)
    assert (got["unaided"], got["out_of_window"], got["runs"]) == (0, 1, 0)


def _case_failed_just_before_the_window(life: Life) -> tuple[list[dict[str, Any]], datetime]:
    _submit(life)
    life.children.append(ended("failed", "extract", "extract: a stage failed"))
    life.tick()
    life.seat.now["t"] += 2 * DAY
    since = _at(life)
    life.seat.now["t"] += DAY
    _submit(life, injuries=["neck"])  # the same matter, other work: linked to nothing (no ``replaces``)
    life.children.append(delivered())
    life.settle()
    return _rows(life), since


def test_an_unlinked_replacement_just_inside_the_window_is_never_unaided(tmp_path, monkeypatch, real_lane_gate):
    rows, since = _scene(tmp_path, monkeypatch, _case_failed_just_before_the_window)
    # MUTATION: rows read from the window's start only: the failed job is not
    # seen, the same-matter clause cannot fire, the replacement reads unaided.
    assert runs_wh.summarize(_read(rows, since), since=since)["unaided"] == 1
    got = runs_wh.summarize(_read(rows, runs_wh.read_from(since)), since=since)
    assert (got["runs"], got["unaided"], got["aided"], got["out_of_window"]) == (1, 0, 1, 1)
    assert got["aided_by"] == {"same_matter": 1}
