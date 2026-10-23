"""The read passes run ``read_workers`` matters at once, and one limit stops
them all.

WHY (2026-10-23). A list past 200 matters read three times, one matter at a
time, is hours of waiting on the model. The lever lets several matters read
at once; the risks are the money (a cap crossed by one worker must stop every
worker, and no paid call may start after it) and the shared files (the usage
ledger, the budget's cursor, the progress line, the seat).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from litigation_testkit import STAFF_A, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron import budget as budget_mod
from medchron.ledger import Ledger
from medchron.limits import CAP_SETTING, LimitHold
from medchron.litigation import firm as firm_mod, workers
from medchron.litigation.run import LitigationRun
import datetime as dt

TODAY = dt.date(2026, 10, 7)
WAIT_S = 10.0


def _mid(k: int) -> str:
    return f"{k:08d}-0000-4000-8000-{k:012d}"


def _seat(n: int) -> LitSeat:
    per = {
        _mid(k): [(f"{fid}-{k}", name, blob, when) for fid, name, blob, when in matter_docs()] for k in range(1, n + 1)
    }
    seat = LitSeat(per)
    seat.matters = [
        {"id": _mid(k), "number": f"{100000 + k}", "title": f"Matter {k}", "personResponsibleStaffId": STAFF_A}
        for k in range(1, n + 1)
    ]
    return seat


def _run(tmp_path: Path, client: Any, seat: LitSeat, **firm: Any) -> LitigationRun:
    return LitigationRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs", **firm)),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


class Gated(ScriptedLit):
    """Counts the model calls in flight at once. Until ``target`` are in
    flight together, each call waits (at most ``WAIT_S``, so a serial run
    cannot hang); after that, every call answers at once."""

    def __init__(self, target: int, **kw: Any) -> None:
        super().__init__(**kw)
        self.target, self.inflight, self.peak = target, 0, 0
        self.cond = threading.Condition()
        self.reached = False

    def _msg(self, params: dict[str, Any]) -> Any:
        with self.cond:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            if self.inflight >= self.target:
                self.reached = True
                self.cond.notify_all()
            self.cond.wait_for(lambda: self.reached, timeout=WAIT_S)
        try:
            return super()._msg(params)
        finally:
            with self.cond:
                self.inflight -= 1


def test_the_lever_defaults_to_four_and_is_bounded() -> None:
    assert firm_mod.OPTIONAL["read_workers"][1] == 4
    for ok in (1, 4, firm_mod.MAX_READ_WORKERS):
        assert not firm_mod._check("read_workers", ok, "workers")
    for bad in (0, firm_mod.MAX_READ_WORKERS + 1, "4", True, 2.0):
        assert firm_mod._check("read_workers", bad, "workers"), bad


def test_an_out_of_range_lever_refuses_the_inputs(tmp_path: Path) -> None:
    with pytest.raises(firm_mod.LitigationConfigError, match="read_workers"):
        firm_mod.load(make_inputs(tmp_path / "inputs", read_workers=9))


def test_with_four_workers_four_matters_read_at_once_and_the_list_is_whole(tmp_path: Path) -> None:
    client = Gated(target=4)
    r = _run(tmp_path, client, _seat(5), read_workers=4)
    v = r.run()
    assert v.verdict == "delivered", v.reason
    assert client.peak == 4  # four matters' reads overlapped; never more than the lever
    final = json.loads((r.data / "final.json").read_text())
    assert [m["matter_id"] for m in final] == [_mid(k) for k in range(1, 6)]  # inventory order, not finish order
    for k in range(1, 6):
        for s in ("read1", "read2", "read3"):
            assert (r.data / "m" / _mid(k) / f"{s}.json").is_file()
    st = json.loads((r.data / "state.json").read_text())
    assert all(st[s]["status"] == "done" for s in ("read1", "read2", "read3"))


def test_width_one_reads_one_matter_at_a_time(tmp_path: Path) -> None:
    client = Gated(target=10**6)  # never reached: every call would wait, so do not gate
    client.reached = True
    r = _run(tmp_path, client, _seat(3), read_workers=1)
    v = r.run()
    assert v.verdict == "delivered", v.reason
    assert client.peak == 1


class CapAfter:
    """The job's cap as the doorway hook sees it, crossed after ``k`` calls
    and staying crossed (spend only grows). Counts what it let through."""

    def __init__(self, k: int) -> None:
        self.k, self.checked, self.passed, self.raised = k, 0, 0, 0
        self.lock = threading.Lock()

    def check_each_call(self, spent_usd: float, stage: str) -> None:
        with self.lock:
            self.checked += 1
            if self.passed >= self.k:
                self.raised += 1
                raise LimitHold(
                    CAP_SETTING,
                    f"{CAP_SETTING}: the run's spend reached the job's cost cap during {stage}; the run stopped",
                )
            self.passed += 1


def test_a_cap_in_one_worker_stops_every_worker_and_starts_no_paid_call_after(tmp_path: Path) -> None:
    client = Gated(target=3)
    seat = _seat(8)
    r = _run(tmp_path, client, seat, read_workers=3)
    cap = CapAfter(k=3)  # three first turns in flight together; every later call is past the line
    r.limits = cap
    v = r.run()
    assert v.verdict == "failed" and v.reason.startswith(f"limit: {CAP_SETTING}"), v.reason
    assert v.stage == "read1"
    assert len(client.calls) == cap.passed == 3  # no paid call started after the line
    # siblings stopped at the halt before asking the limits again: the limit
    # was crossed once and at most each in-flight worker looked once more
    assert 1 <= cap.raised <= 3
    started = [k for k in range(1, 9) if (r.data / "m" / _mid(k) / "read1.json").is_file()]
    assert started == []  # every matter needed a second turn; none finished
    assert not list((r.data / "m").glob("*/unread.json"))  # a stop is no matter's failure
    # a resume with the line lifted reads every matter and delivers
    r2 = LitigationRun(
        r.job.job_dir,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
    )
    assert r2.run().verdict == "delivered"


def test_a_tripped_halt_refuses_the_next_call_before_the_limits_are_asked(tmp_path: Path) -> None:
    r = _run(tmp_path, ScriptedLit(), _seat(1))
    cap = CapAfter(k=100)
    r.limits = cap
    r._halt = workers.Halt()
    r._before_call("read1")
    assert cap.checked == 1
    r._halt.trip(LimitHold(CAP_SETTING, f"{CAP_SETTING}: x"))
    with pytest.raises(workers.Halted):
        r._before_call("read1")
    assert cap.checked == 1


def test_a_hold_in_the_hook_trips_the_halt_for_every_sibling(tmp_path: Path) -> None:
    r = _run(tmp_path, ScriptedLit(), _seat(1))
    r.limits = CapAfter(k=0)
    r._halt = workers.Halt()
    with pytest.raises(LimitHold):
        r._before_call("read2")
    assert isinstance(r._halt.first, LimitHold)


def test_in_parallel_raises_the_first_stop_once_and_starts_nothing_after() -> None:
    class R:
        seat = object()
        _seat: Any = None
        _halt: Any = None

    r = R()
    started: list[int] = []
    lock = threading.Lock()
    gate = threading.Event()

    def fn(i: int) -> None:
        with lock:
            started.append(i)
        if i == 0:
            gate.wait(WAIT_S)
            raise LimitHold(CAP_SETTING, f"{CAP_SETTING}: first")
        gate.set()
        while not r._halt.tripped:  # a sibling mid-read: its next call meets the halt
            threading.Event().wait(0.01)
        r._halt.check()

    with pytest.raises(LimitHold, match="first"):
        workers.in_parallel(r, [(i,) for i in range(20)], fn, 2)
    assert sorted(started) == [0, 1]  # the other eighteen never started
    assert r._halt is None  # the run's hook is unbound after the stage


def test_the_budget_never_double_prices_a_row_under_concurrent_refresh(tmp_path: Path, pricing_path: Path) -> None:
    path = tmp_path / "usage-ledger.jsonl"
    led = Ledger(path)
    pricing = budget_mod.Pricing.load(pricing_path)
    price = pricing.price_row

    def slow(row: dict[str, Any]) -> float:  # widen the window two refreshes could share
        threading.Event().wait(0.0005)
        return price(row)

    object.__setattr__(pricing, "price_row", slow)
    b = budget_mod.Budget(pricing, 1e9, [path], 1.0)
    usage = type("U", (), {"input_tokens": 1000, "output_tokens": 200})()
    model = "claude-sonnet-5"

    def work() -> None:
        for _ in range(50):
            led.record("read1", model, usage, effort=None, cache=False, batch=False)
            b.refresh()

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(WAIT_S)
    rows = path.read_text().splitlines()
    assert len(rows) == 400 and all(json.loads(x)["stage"] == "read1" for x in rows)
    fresh = budget_mod.Budget(pricing, 1e9, [path], 1.0)
    assert b.refresh() == pytest.approx(fresh.refresh())
    assert sum(c.rows for c in b._cursors.values()) == 400


def test_a_reads_document_fetch_takes_turns_at_the_seat() -> None:
    seen: list[int] = []
    live = {"n": 0}
    lock = threading.Lock()

    class Seat:
        name = "seat"

        def mint(self, *_a: Any) -> list[int]:
            with lock:
                live["n"] += 1
                seen.append(live["n"])
            threading.Event().wait(0.01)
            with lock:
                live["n"] -= 1
            return []

    s = workers.SerialSeat(Seat())
    assert s.name == "seat"
    ts = [threading.Thread(target=s.mint) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(WAIT_S)
    assert max(seen) == 1
