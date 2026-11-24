"""Units draft at once (2026-11-23: a 12-set request ran 12 rounds one after another).

The stand-in run keeps the real state writes (``DraftingRun._put``/``_done``/
``_is_done``) and the real ``Finish._draft_units``; only a unit's own work is a
stub that records itself in state.json and sleeps. Invented keys throughout.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from jobs.drafting import finish as finish_mod
from jobs.drafting.finish import Finish, Unit
from jobs.drafting.outcome import DraftingFailed, DraftingHold
from jobs.drafting.run import DraftingRun


class Run(Finish):
    _state = DraftingRun._state
    _put = DraftingRun._put
    _done = DraftingRun._done
    _is_done = DraftingRun._is_done

    def __init__(self, data: Path, n: int, parties: list[str | None] | None = None, fail: dict | None = None):
        self.data = data
        self._units = [
            Unit(
                f"set-{i}",
                f"Set {i}",
                "special_interrogatories",
                None,
                data / f"set-{i}",
                (parties or [None] * n)[i - 1],
            )
            for i in range(1, n + 1)
        ]
        self._corpus = {}
        self.fail = fail or {}
        self.lock = threading.Lock()
        self.flight = self.peak = 0
        self.order: list[str] = []
        self.workers: list[int] = []

    def _one(self, u: Unit, digest: str) -> None:
        if self._is_done(u.stage("rendered")):
            return
        with self.lock:
            self.flight += 1
            self.peak = max(self.peak, self.flight)
            self.order.append(u.key)
            self.workers.append(getattr(self, "_unit_workers", finish_mod.CONCURRENCY))
        try:
            time.sleep(0.05)
            if u.key in self.fail:
                raise self.fail[u.key]
            self._done(u.stage("rendered"))
        finally:
            with self.lock:
                self.flight -= 1


def test_six_units_draft_at_most_four_at_once_after_the_first_each_recorded(tmp_path):
    run = Run(tmp_path, 6)
    run._draft_units("")
    assert run.order[0] == "set-1" and sorted(run.order) == sorted(f"set-{i}" for i in range(1, 7))
    assert 1 < run.peak <= finish_mod.UNITS_AT_ONCE
    assert run.workers[0] == finish_mod.CONCURRENCY and set(run.workers[1:]) == {2}  # 8 calls over 4 units
    assert all(run._is_done(f"set-{i}:rendered") for i in range(1, 7))
    assert run._unit_workers == finish_mod.CONCURRENCY


def test_the_first_unit_of_each_party_goes_first_alone(tmp_path):
    run = Run(tmp_path, 4, parties=["City", "City", "District", "District"])
    run._draft_units("")
    assert run.order[:2] == ["set-1", "set-3"]


def test_failures_end_the_job_with_the_earliest_failed_unit_in_plan_order(tmp_path):
    fail = {"set-5": DraftingHold("request_incomplete: invented"), "set-3": DraftingFailed("format_check: invented")}
    run = Run(tmp_path, 6, fail=fail)
    with pytest.raises(DraftingFailed, match="^format_check: invented"):
        run._draft_units("")
    started = set(run.order)
    # Every unit that started and did not fail recorded its stage; none was left half-written.
    assert all(run._is_done(f"{k}:rendered") for k in started - set(fail))


def test_a_resume_skips_the_units_a_partial_run_finished(tmp_path):
    first = Run(tmp_path, 6, fail={"set-4": DraftingFailed("format_check: invented")})
    with pytest.raises(DraftingFailed):
        first._draft_units("")
    done = {u.key for u in first.units() if first._is_done(u.stage("rendered"))}
    again = Run(tmp_path, 6)
    again._draft_units("")
    assert set(again.order) == {f"set-{i}" for i in range(1, 7)} - done
    assert all(again._is_done(f"set-{i}:rendered") for i in range(1, 7))


def test_concurrent_state_writes_lose_nothing(tmp_path):
    run = Run(tmp_path, 1)
    threads = [threading.Thread(target=run._done, args=(f"k{i}",)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(run._is_done(f"k{i}") for i in range(40))


def test_each_set_is_metered_by_its_own_calls_never_by_the_others(tmp_path: Path):
    """Review 11-23: a whole-ledger meter counts the other sets' compose and audit as repair."""
    from jobs.drafting import repair_budget as rb

    own = {"set-1": 1.0, "set-2": 5.0}  # set-2 composes and audits while set-1 repairs
    plans = [rb.Plan(8, 10.0, lambda k=k: own[k], tmp_path, tmp_path, k, k, "job", meter=own.__getitem__)
             for k in own]  # fmt: skip
    for p in plans:
        p.begin()
    own["set-1"] = 2.5  # set-1's repair: 1.5
    assert plans[0].repair_usd() == pytest.approx(1.5)
    own["set-2"] = 6.0  # set-2's repair: 1.0
    assert plans[1].repair_usd() == pytest.approx(2.5)


def test_the_set_doorway_tags_each_call_and_the_ledger_meter_reads_only_that_set(tmp_path: Path):
    from types import SimpleNamespace

    from jobs.drafting.finish import SetDoorway

    seen: list[str] = []
    inner = SimpleNamespace(call=lambda stage, **kw: seen.append(kw["custom_id"]), ledger="L")
    SetDoorway(inner, "set-1").call("repair", custom_id="repair-2-ab")
    assert seen == ["set-1-repair-2-ab"] and SetDoorway(inner, "set-1").ledger == "L"
    led = tmp_path / "ledger.jsonl"
    led.write_text("\n".join(json.dumps({"custom_id": c, "usd": u}) for c, u in
                             [("set-1-a", 1.0), ("set-12-a", 7.0), ("set-1-b", 2.0), ("draudit-x", 9.0)]))  # fmt: skip
    run = Run(tmp_path, 1)
    run.budget = SimpleNamespace(ledgers=[led], pricing=SimpleNamespace(price_row=lambda r: r["usd"]))
    assert run.unit_spent("set-1") == pytest.approx(3.0)
