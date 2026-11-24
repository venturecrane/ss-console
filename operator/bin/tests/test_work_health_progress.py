"""A long pull that keeps moving is not stuck; one whose count froze is (2026-11-24).

A drafting job pulling about 500 files read as frozen for 26 minutes: the
triage judged a running job from its RUNNING row alone. Its runner now notes
where it is, and the broker writes ``DRAFTING_JOB_PROGRESS`` only when the
stage or the count MOVED. Times and ids are invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bin.lib import work_health as wh

UTC = timezone.utc
NOW = datetime(2026, 11, 24, 20, 0, tzinfo=UTC)
CFG = wh.load_config()
SEAT = "a-seat"
JOB = "01JOBPROGRESSAAAAAAAAAAAAA"


def _row(action: str, at: datetime, **md) -> dict:
    return {
        "id": f"{action}-{at.isoformat()}",
        "ts": at.isoformat().replace("+00:00", "Z"),
        "action_type": action,
        "skill_name": None,
        "metadata": json.dumps({"job_id": JOB, **md}),
    }


def _started(hours: float) -> list[dict]:
    at = NOW - timedelta(hours=hours)
    return [_row("DRAFTING_JOB_SUBMITTED", at), _row("DRAFTING_JOB_RUNNING", at + timedelta(seconds=5))]


def test_a_running_job_that_moved_recently_is_not_stuck() -> None:
    """FALSIFIER: drop ``Job.moved`` from the running clock and this long pull,
    still pulling, is listed stuck."""
    limit = CFG.lanes["drafting"].run_minutes
    rows = _started(limit / 60 + 1) + [
        _row("DRAFTING_JOB_PROGRESS", NOW - timedelta(minutes=3), stage="pull", progress="480/502")
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_running_job_whose_count_froze_is_stuck_from_its_last_move() -> None:
    limit = CFG.lanes["drafting"].run_minutes
    last = NOW - timedelta(minutes=limit + 5)
    rows = _started(limit / 60 + 3) + [_row("DRAFTING_JOB_PROGRESS", last, stage="pull", progress="120/502")]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert [i.kind for i in items] == ["job_stuck"]
    assert "last moved" in items[0].what


def test_a_progress_row_is_not_a_state() -> None:
    """It never ends or starts a job: a job with only progress rows is not read."""
    jobs = wh.jobs_from_rows([_row("DRAFTING_JOB_PROGRESS", NOW, stage="pull", progress="1/2")])
    assert jobs == []
