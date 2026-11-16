"""The page store in SMD's work digest (wave 5): a lane whose job read pages
with the store off, or paid again for a page the store already held, is an
item; ordinary jobs reading new pages are not. Each test fails if the line it
defends is removed."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bin.lib import work_health as wh
from bin.lib.work_health_page_store import page_store_items

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
CFG = wh.load_config()


def _row(
    n: int, store: str, hits: int, misses: int, lane: str = "LITIGATION", state: str = "DELIVERED", repeats: int = 0
) -> dict:
    ts = f"2026-11-16T1{n}:00:00Z"
    counts = {"store": store, "hits": hits, "misses": misses, "repeats": repeats}
    md = {"job_id": f"J{n}", "state": state.lower(), "page_store": counts}
    return {"id": ts, "ts": ts, "action_type": f"{lane}_JOB_{state}", "metadata": json.dumps(md)}


def test_the_contract_names_the_window() -> None:
    assert CFG.page_store_cold_jobs == 3


def test_a_job_that_read_pages_with_the_store_off_is_an_item() -> None:
    items = page_store_items("seat-a", [_row(1, "on", 2, 3), _row(2, "off", 0, 4)], CFG, NOW)
    assert [i.kind for i in items] == ["page_store_off"]
    assert "litigation status list" in items[0].what and items[0].rank == wh.RANK["page_store_off"]
    assert items[0].key.endswith("|litigation:J2:page_store_off")


def test_ordinary_jobs_of_new_pages_raise_nothing_however_many() -> None:
    """The litigation lane serves a repeat file whole before any page is asked
    for, and the chronology seeds an update from the delivered job first, so
    jobs that only ever miss are a healthy seat."""
    rows = [_row(n, "on", 0, 5, "LITIGATION") for n in range(1, 6)]
    rows += [_row(n, "on", 0, 9, "MEDCHRON") for n in range(6, 9)]
    assert page_store_items("seat-a", rows, CFG, NOW) == []


def test_a_stored_page_paid_for_again_is_cold() -> None:
    rows = [
        _row(1, "on", 0, 5, "MEDCHRON"),
        _row(2, "on", 3, 2, "MEDCHRON", repeats=2),
        _row(3, "on", 0, 4, "MEDCHRON"),
    ]
    items = page_store_items("seat-a", rows, CFG, NOW)
    assert [i.kind for i in items] == ["page_store_cold"] and items[0].rank == wh.RANK["page_store_cold"]
    assert "paid again for 2 page(s)" in items[0].what and items[0].key.endswith("|medchron:J2:page_store_cold")


def test_a_repeat_outside_the_lanes_last_three_reading_jobs_is_history() -> None:
    rows = [_row(1, "on", 0, 5, "DEMAND", repeats=1)] + [_row(n, "on", 0, 2, "DEMAND") for n in range(2, 5)]
    assert page_store_items("seat-a", rows, CFG, NOW) == []
    bare = {"id": "x", "ts": "2026-11-16T15:00:00Z", "action_type": "DEMAND_JOB_DELIVERED",
            "metadata": json.dumps({"job_id": "J9", "state": "delivered"})}  # fmt: skip
    assert page_store_items("seat-a", [bare], CFG, NOW) == []


def test_only_ended_rows_count_and_lanes_are_judged_apart() -> None:
    rows = [_row(1, "off", 0, 3, "NEGOTIATION", state="RUNNING"), _row(2, "on", 0, 3, "DEMAND"),
            _row(3, "off", 0, 1, "DRAFTING")]  # fmt: skip
    items = page_store_items("seat-a", rows, CFG, NOW)
    assert [(i.kind, i.key.split("|")[-1].split(":")[0]) for i in items] == [("page_store_off", "drafting")]
