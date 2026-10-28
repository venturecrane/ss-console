"""The Case File Manager's paused schedule is one digest line a day, never a
page: the broker paged once when the episode began; the cron's own row keeps
it in front of SMD after that. Each test fails if the line it defends is
removed."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bin.lib import work_health as wh
from bin.lib.work_health_casefile import casefile_schedule_items

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)


def _row(ts: str, status: str, skill: str = "case-file-manager") -> dict:
    return {
        "id": ts,
        "ts": ts,
        "action_type": "SUPPRESSED_WAKE",
        "skill_name": skill,
        "metadata": json.dumps({"decision_basis": f"casefile_scheduled:{status}"}),
    }


def test_a_paused_schedule_is_one_line_and_never_a_page() -> None:
    rows = [_row("2026-11-04T14:17:00Z", "token_gate"), _row("2026-11-04T15:17:00Z", "token_gate")]
    items = casefile_schedule_items("seat-a", rows, NOW)
    assert len(items) == 1 and items[0].page is None
    assert items[0].kind == "schedule_paused" and "Smokeball connection needs renewing" in items[0].what
    assert items[0].rank == wh.RANK["schedule_paused"]


def test_a_working_schedule_says_nothing() -> None:
    rows = [_row("2026-11-04T14:17:00Z", "token_gate"), _row("2026-11-04T15:17:00Z", "queued")]
    assert casefile_schedule_items("seat-a", rows, NOW) == []
    assert casefile_schedule_items("seat-a", [_row("2026-11-04T15:17:00Z", "not_business_day")], NOW) == []


def test_a_missed_day_is_a_line_and_an_old_row_is_not() -> None:
    assert casefile_schedule_items("seat-a", [_row("2026-11-04T22:17:00Z", "missed")], NOW)[0].kind == (
        "schedule_paused"
    )
    assert casefile_schedule_items("seat-a", [_row("2026-11-02T15:17:00Z", "missed")], NOW) == []


def test_another_skills_row_is_not_this_lines() -> None:
    assert (
        casefile_schedule_items("seat-a", [_row("2026-11-04T15:17:00Z", "token_gate", "negotiation-watch")], NOW) == []
    )
