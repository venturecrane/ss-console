"""The page store in SMD's work digest (wave 5): a lane whose jobs read pages
with the store off, or paid for pages three jobs running and were never served
one, is an item. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bin.lib import work_health as wh
from bin.lib.work_health_page_store import page_store_items

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
CFG = wh.load_config()


def _row(n: int, store: str, hits: int, misses: int, lane: str = "LITIGATION", state: str = "DELIVERED") -> dict:
    ts = f"2026-11-16T1{n}:00:00Z"
    md = {"job_id": f"J{n}", "state": state.lower(), "page_store": {"store": store, "hits": hits, "misses": misses}}
    return {"id": ts, "ts": ts, "action_type": f"{lane}_JOB_{state}", "metadata": json.dumps(md)}


def test_the_contract_names_the_cold_run_length() -> None:
    assert CFG.page_store_cold_jobs == 3


def test_a_job_that_read_pages_with_the_store_off_is_an_item() -> None:
    items = page_store_items("seat-a", [_row(1, "on", 2, 3), _row(2, "off", 0, 4)], CFG, NOW)
    assert [i.kind for i in items] == ["page_store_off"]
    assert "litigation status list" in items[0].what and items[0].rank == wh.RANK["page_store_off"]
    assert items[0].key.endswith("|litigation:J2:page_store_off")


def test_three_paying_jobs_with_no_hit_are_cold_and_one_hit_clears_it() -> None:
    cold = [_row(1, "on", 0, 5, "MEDCHRON"), _row(2, "on", 0, 0, "MEDCHRON"), _row(3, "on", 0, 2, "MEDCHRON"),
            _row(4, "on", 0, 1, "MEDCHRON")]  # fmt: skip
    items = page_store_items("seat-a", cold, CFG, NOW)
    assert [i.kind for i in items] == ["page_store_cold"]
    assert "8 page(s)" in items[0].what  # the job that paid nothing is not one of the three
    warm = [*cold[:3], _row(4, "on", 1, 1, "MEDCHRON")]
    assert page_store_items("seat-a", warm, CFG, NOW) == []


def test_two_paying_jobs_are_not_yet_a_pattern_and_a_job_without_the_record_is_silent() -> None:
    rows = [_row(1, "on", 0, 5, "DEMAND"), _row(2, "on", 0, 2, "DEMAND")]
    assert page_store_items("seat-a", rows, CFG, NOW) == []
    bare = {"id": "x", "ts": "2026-11-16T15:00:00Z", "action_type": "DEMAND_JOB_DELIVERED",
            "metadata": json.dumps({"job_id": "J9", "state": "delivered"})}  # fmt: skip
    assert page_store_items("seat-a", [bare], CFG, NOW) == []


def test_only_ended_rows_count_and_lanes_are_judged_apart() -> None:
    rows = [_row(1, "off", 0, 3, "NEGOTIATION", state="RUNNING"), _row(2, "on", 0, 3, "DEMAND"),
            _row(3, "off", 0, 1, "DRAFTING")]  # fmt: skip
    items = page_store_items("seat-a", rows, CFG, NOW)
    assert [(i.kind, i.key.split("|")[-1].split(":")[0]) for i in items] == [("page_store_off", "drafting")]
