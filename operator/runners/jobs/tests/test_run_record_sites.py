"""Every site that ends, stops, restarts or hands over a chronology job leaves
a run block on its audit row (``jobs/run_record.py``), driven through the REAL
chronology lane and the REAL broker on the host matrix's seat
(``lifecycle_matrix.py``), and the rows grade as ``bin/work-health.py --runs``
grades them (``bin/lib/work_health_runs.py``).

THE MUTATION: with one site's block deleted, the run still grades aided,
because a RUNNING row after a HELD or FAILED row is aided whatever its blocks
say (the structural clause). A step that forgets its record is never counted
as a run that needed nobody.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from lifecycle_matrix import AUDIT_HOLD, Life, _marker, _resume, build, delivered, ended, teardown

from bin.lib import work_health_runs as runs_wh


def _rows(life: Life) -> list[dict[str, Any]]:
    conn = sqlite3.connect(life.seat.audit_db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT id, ts, action_type, matter_ref, metadata FROM audit_log")]
    finally:
        conn.close()


def _blocks(life: Life, jid: str) -> list[dict[str, Any]]:
    out = []
    for r in _rows(life):
        md = json.loads(r["metadata"] or "{}")
        if r["action_type"].startswith("MEDCHRON_JOB_") and md.get("job_id") == jid and "run" in md:
            out.append(md["run"])
    return out


def _held(life: Life, reason: str = AUDIT_HOLD, stage: str = "audit") -> str:
    jid = life.submit()
    life.children.append(ended("held", stage, reason))
    life.tick()
    return jid


def _failed(life: Life) -> str:
    jid = life.submit()
    life.children.append(ended("failed", "extract", "extract: a stage failed"))
    life.tick()
    return jid


def _drive(life: Life, kind: str) -> str:
    """One intervention of ``kind`` on one job, then the lane settles."""
    if kind == "resume":
        jid = _failed(life)
        _resume(life, jid)
    elif kind == "redo":
        jid = _failed(life)
        _resume(life, jid, redo=["map"])
    elif kind == "release":
        jid = _held(life, "review_held: the pre-delivery review found a claim to check", "review")
        _resume(life, jid, release_review=True)
    elif kind in ("decide_audit", "decide_coverage", "decide_identity", "decide_update", "redo_doc"):
        jid = _held(life)
        _marker(life, jid, kind, [])
    elif kind == "hand_delivery":
        from lifecycle_matrix import _hand

        jid = _held(life)
        _hand(life, jid)
    elif kind == "infra_requeue":
        jid = life.submit()
        life.children.append(ended("failed", "map", "map: model API error (infrastructure)", exit_code=75))
        life.tick()
    elif kind == "stopped_by_smd":
        from jobs import smd_stop

        jid = life.submit()
        smd_stop.stop_queued(life.lane.queue, life.lane.job_dir(jid), by="smd@example.test", why="matrix", at=1.0)
        life.tick()
    elif kind == "replaces":
        jid = _failed(life)
        from lifecycle_matrix import _resubmit

        _resubmit(life, jid)
    else:  # pragma: no cover - a typo in the table below
        raise ValueError(kind)
    life.settle()
    return jid


#: Each intervention, the event its block carries, and who it names.
SITES = {
    "resume": ("resume", "smd"),
    "redo": ("redo", "smd"),
    "release": ("release", "smd"),
    "decide_audit": ("decide_audit", "smd"),
    "decide_coverage": ("decide_coverage", "smd"),
    "decide_identity": ("decide_identity", "smd"),
    "decide_update": ("decide_update", "smd"),
    "redo_doc": ("redo_doc", "smd"),
    "hand_delivery": ("hand_delivery", "smd"),
    "infra_requeue": ("infra_requeue", "code"),
    "replaces": ("replaces", "firm"),
}


@pytest.mark.parametrize("kind", sorted(SITES))
def test_each_intervention_leaves_its_block_and_grades_aided(tmp_path, monkeypatch, real_lane_gate, kind):
    life, server = build(tmp_path, monkeypatch)
    try:
        with server as port:
            life.run.build(port)
            jid = _drive(life, kind)
            event, by = SITES[kind]
            rows = _rows(life)
            mine = [b for r in rows for b in [json.loads(r["metadata"] or "{}")] if b.get("run")]
            assert any(m["run"]["event"] == event and m["run"]["by"] == by for m in mine), [m["run"] for m in mine]
            runs = runs_wh.runs_from_rows(rows)
            grades = {r.job_id: runs_wh.grade(r, runs) for r in runs}
            if kind == "infra_requeue":
                # The runner's own retry: counted apart, never a step anyone took.
                assert grades[jid]["unaided"] is True, grades[jid]
                assert runs_wh.summarize(rows)["infra_requeues"] == 1
            else:
                assert all(not g["unaided"] for g in grades.values()), grades
    finally:
        teardown(life)


def test_a_run_nobody_touched_grades_unaided(tmp_path, monkeypatch, real_lane_gate):
    """The control: the same seat, nothing planted, grades its run unaided."""
    life, server = build(tmp_path, monkeypatch)
    try:
        with server as port:
            life.run.build(port)
            jid = life.submit()
            life.children.append(delivered())
            life.settle()
            blocks = _blocks(life, jid)
            assert [b["event"] for b in blocks] == ["start", "end"]
            assert all(b["by"] == "code" for b in blocks)
            assert runs_wh.summarize(_rows(life))["unaided"] == 1
    finally:
        teardown(life)


@pytest.mark.parametrize("kind", ["resume", "decide_coverage"])
def test_a_site_that_forgets_its_block_still_grades_aided(tmp_path, monkeypatch, real_lane_gate, kind):
    """MUTATION: the resume's block is deleted (the site writes none); the run
    is still aided, by the RUNNING row after its HELD or FAILED row."""
    from jobs import resume

    real = resume.run_record.block

    def forgetful(event: str, **kw: Any) -> dict[str, Any] | None:
        return None if event == SITES[kind][0] else real(event, **kw)

    monkeypatch.setattr(resume.run_record, "block", forgetful)
    life, server = build(tmp_path, monkeypatch)
    try:
        with server as port:
            life.run.build(port)
            jid = _drive(life, kind)
            assert not any(b["event"] == SITES[kind][0] for b in _blocks(life, jid))
            rows = _rows(life)
            runs = runs_wh.runs_from_rows(rows)
            g = next(runs_wh.grade(r, runs) for r in runs if r.job_id == jid)
            assert g["unaided"] is False and "restarted" in g["aided"], g
    finally:
        teardown(life)


def test_a_stop_by_smd_names_smd(tmp_path, monkeypatch, real_lane_gate):
    life, server = build(tmp_path, monkeypatch)
    try:
        with server as port:
            life.run.build(port)
            jid = _drive(life, "stopped_by_smd")
            assert {"event": "stop", "by": "smd", "code": "stopped_by_smd", "v": 1} in _blocks(life, jid)
    finally:
        teardown(life)
