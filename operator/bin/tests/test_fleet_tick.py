"""The release controller (ADR 0096): ticks, the bake, per-app Fly tokens, verify rows.

Each test names its falsifier: the one change to the code that turns it red.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bin.lib.fleet import cli
from bin.lib.fleet import fly as fly_module
from bin.lib.fleet.build import ImageExtractor
from bin.lib.fleet.fly import Fly, FlyTokens, combine_tokens, seat_token_env_name
from bin.lib.fleet.ledger import VerifyLedger, head_tail
from bin.lib.fleet.r2 import image_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.rollout import ImagePlan
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.smoke import FleetSideChecks
from bin.lib.fleet.tick import (
    TICK_HALT,
    TICK_HALTED_STANDING,
    TICK_PROGRESS,
    TICK_WAITING,
    Ticker,
    overlay_newer,
    plan_tick,
)
from bin.tests.fleet_fakes import DIGEST, fresh_status, selftest_json
from bin.tests.test_fleet_release import World

RINGS = [
    Ring(0, ["smd-staging"], None, 30),
    Ring(1, ["pilot-smokeball"], None, 30),
    Ring(2, ["scott", "brokery"], None, 30),
    Ring(3, ["ashton-price"], 1, 30),
]
DONE_AT = "2026-10-23T00:00:00Z"
T0 = datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp()


def _tick_world(tmp_path: Any) -> World:
    w = World()
    w.roll.fleet_side = FleetSideChecks(w.fly, str(tmp_path), w.clock.sleep, w.clock)  # type: ignore[arg-type]
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json()}
    w.store.now_iso = DONE_AT
    return w


def _tick(w: World, minutes_after_done: float) -> int:
    w.clock.t = T0 + minutes_after_done * 60
    # A rollout this tick finishes is stamped with the tick's own time (D1's NOW_SQL).
    w.store.now_iso = datetime.fromtimestamp(w.clock.t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = ImagePlan(w.rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    return ticker.run(RINGS, w.rel, lambda s, r: w.roll.run(s, r, plan), "tick-holder")


def _deployed(w: World) -> list[str]:
    return [app for app, _, _ in w.fly.deploys]


# -- the tick -----------------------------------------------------------------------------


def test_a_tick_advances_one_ring_and_the_bake_holds_the_next(tmp_path) -> None:
    w = _tick_world(tmp_path)
    assert _tick(w, 0) == TICK_PROGRESS
    assert _deployed(w) == ["hermes-smd-staging"]  # ring 0 only, never two rings in one tick
    # Bake not elapsed: FALSIFIER: drop bake_gate and ring 1 deploys here.
    assert _tick(w, 10) == TICK_WAITING
    assert _deployed(w) == ["hermes-smd-staging"]
    assert any("baking" in line for line in w.logs)
    assert _tick(w, 31) == TICK_PROGRESS
    assert _deployed(w) == ["hermes-smd-staging", "hermes-pilot-smokeball"]


def test_an_open_selftest_failing_on_ring_n_blocks_ring_n_plus_1(tmp_path) -> None:
    w = _tick_world(tmp_path)
    _tick(w, 0)
    w.store.alerts.append({"customer_slug": "smd-staging", "condition": "selftest_failing", "status": "open"})
    # FALSIFIER: skip the open-alarm read and the pilot deploys after the bake.
    assert _tick(w, 60) == TICK_WAITING
    assert _deployed(w) == ["hermes-smd-staging"]
    assert any("alarm is open" in line for line in w.logs)
    w.store.alerts.clear()
    assert _tick(w, 61) == TICK_PROGRESS
    assert "hermes-pilot-smokeball" in _deployed(w)


def test_a_full_release_by_ticks_never_redeploys_and_completes(tmp_path) -> None:
    w = _tick_world(tmp_path)
    for minute in (0, 31, 62, 93):
        assert _tick(w, minute) == TICK_PROGRESS
    assert _deployed(w) == [
        "hermes-smd-staging", "hermes-pilot-smokeball", "hermes-scott", "hermes-brokery", "hermes-ashton-price",
    ]  # fmt: skip
    assert w.store.completed == [w.rel["release_id"]]
    assert _tick(w, 200) == TICK_WAITING  # nothing left to do
    assert len(w.fly.deploys) == 5


def test_a_preflight_refusal_the_declarations_now_cover_is_retried_not_halted() -> None:
    """A `failed` row that deployed nothing (a lane-input refusal) stops standing as a halt once
    ``retriable`` says main now declares its lanes: the seat is pending and the front ring rolls.
    FALSIFIER: without the retriable hook the same rows are a standing halt (asserted below)."""
    now = datetime.fromtimestamp(T0, timezone.utc)
    refused = "preflight refused: lane records: the lane's firm-input schema changed; re-stage with operator/bin/reprovision.sh ashton-price"
    latest = {"ashton-price": {"state": "failed", "finished_at": DONE_AT, "detail": refused}}
    asked: list[str] = []

    def retriable(seat: str, row: dict[str, Any]) -> bool:
        asked.append(seat)
        return row.get("detail") == refused

    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now, None, retriable)
    assert plan.code == TICK_PROGRESS and plan.seats == ["smd-staging"] and asked == ["ashton-price"]
    # the hook says no (the declaration does not cover it): the halt stands exactly as before
    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now, None, lambda seat, row: False)
    assert plan.code == TICK_HALTED_STANDING and "ashton-price=failed" in plan.reason
    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now)
    assert plan.code == TICK_HALTED_STANDING
    # a rollback is never retriable by this path, whatever the hook says about a refusal
    rolled = {"ashton-price": {"state": "rolled_back", "finished_at": DONE_AT, "detail": "self-test failed"}}
    plan = plan_tick(RINGS, rolled, {}, lambda seats: [], now, None, retriable)
    assert plan.code == TICK_HALTED_STANDING


def test_a_halt_stands_until_a_person_acts(tmp_path) -> None:
    w = _tick_world(tmp_path)
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json(failed_count=1, failed=["x"])}
    assert _tick(w, 0) == TICK_HALT
    assert w.store.states()["smd-staging"] == "rolled_back"
    # FALSIFIER: ignore halting rows and the next tick retries the seat.
    assert _tick(w, 120) == TICK_HALTED_STANDING
    assert len(w.fly.deploys) == 2  # the release deploy and its rollback, nothing since


def test_a_selftest_timeout_in_a_tick_stalls_the_seat_without_any_boot_smoke(tmp_path) -> None:
    w = _tick_world(tmp_path)
    for seat in w.store.status:
        w.store.status[seat] = fresh_status()  # no self-test row ever arrives
    assert _tick(w, 0) == TICK_PROGRESS
    assert w.store.states()["smd-staging"] == "tooling_error"
    assert w.smoke.calls == []  # FALSIFIER: fall back to bash and the boot smoke is called
    # Ring 0 has no seat done, so it stays the front and the next tick retries it,
    # and nothing past it moves: nothing has proved the release yet.
    assert _tick(w, 20) == TICK_PROGRESS
    assert _deployed(w) == ["hermes-smd-staging", "hermes-smd-staging"]


def test_a_stalled_seat_never_holds_the_client_ring_back(tmp_path) -> None:
    """2026-10-29: brokery's Fly deploy failed with no reason and the client seat waited hours."""
    w = _tick_world(tmp_path)
    for minute in (0, 31):
        assert _tick(w, minute) == TICK_PROGRESS
    w.fly.deploy_results = [Result(0, "deployed", ""), Result(1, "", "Error: x"), Result(1, "", "Error: x")]
    assert _tick(w, 62) == TICK_PROGRESS  # ring 2: scott done, brokery stalled twice over
    assert w.store.states()["brokery"] == "tooling_error"
    # FALSIFIER: count a stalled seat as pending and ring 2 stays the front (brokery
    # retried every tick), so ashton-price never deploys.
    assert _tick(w, 93) == TICK_PROGRESS
    assert "hermes-ashton-price" in _deployed(w)
    # Once the front has nothing to do, the stalled seat is retried and the release completes.
    before = len(w.fly.deploys)
    assert _tick(w, 124) == TICK_PROGRESS
    assert _deployed(w)[before:] == ["hermes-brokery"]
    assert w.store.completed == [w.rel["release_id"]]


def test_a_stalled_seat_is_retried_while_the_front_bakes() -> None:
    latest = {
        "smd-staging": {"state": "done", "finished_at": DONE_AT},
        "pilot-smokeball": {"state": "done", "finished_at": DONE_AT},
        "scott": {"state": "done", "finished_at": "2026-10-23T01:55:00Z"},
        "brokery": {"state": "tooling_error", "finished_at": "2026-10-23T01:55:00Z"},
    }
    now = datetime(2026, 10, 23, 2, tzinfo=timezone.utc)
    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now)
    # ring 3 waits on ring 2's bake; meanwhile brokery is tried again.
    assert plan.code == TICK_PROGRESS and plan.seats == ["brokery"] and "baking" in plan.reason
    assert plan.ring is not None and plan.ring.number == 2


def test_a_busy_seat_waits_and_a_later_tick_picks_it_up(tmp_path) -> None:
    w = _tick_world(tmp_path)
    busy = {"lanes": [], "children": [{"file": "child.pid", "pid": "7", "alive": True}], "crons": [], "errors": []}
    w.fly.probe_facts["smd-staging"] = [busy] * 50
    w.roll.gate.sleep = w.clock.sleep  # the idle wait runs on the fake clock
    plan_wait = 60.0
    w.clock.t = T0
    plan = ImagePlan(w.rel, "3" * 40, max_idle_wait=plan_wait, verify="selftest", selftest_capable=True,
                     ssh_fallback=False)  # fmt: skip
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    assert ticker.run(RINGS, w.rel, lambda s, r: w.roll.run(s, r, plan), "h") == TICK_PROGRESS
    assert w.store.states()["smd-staging"] == "waiting_idle" and w.fly.deploys == []
    w.fly.probe_facts["smd-staging"] = [{"lanes": [], "children": [], "crons": [], "errors": []}]
    assert _tick(w, 20) == TICK_PROGRESS
    assert _deployed(w) == ["hermes-smd-staging"]


def test_held_seats_neither_deploy_nor_block_their_ring_but_a_fully_held_ring_waits() -> None:
    latest = {"smd-staging": {"state": "done", "finished_at": DONE_AT}}
    now = datetime(2026, 10, 23, 2, tzinfo=timezone.utc)
    plan = plan_tick(RINGS, latest, {"pilot-smokeball": {}}, lambda seats: [], now)
    assert plan.code == TICK_WAITING and "every seat is held" in plan.reason
    latest["pilot-smokeball"] = {"state": "done", "finished_at": DONE_AT}
    plan = plan_tick(RINGS, latest, {"scott": {}}, lambda seats: [], now)
    assert plan.code == TICK_PROGRESS and plan.seats == ["brokery"]


def test_a_tick_waits_while_another_rollout_holds_the_fleet_lease(tmp_path) -> None:
    w = _tick_world(tmp_path)
    w.store.lease_row = {"lease_name": "rollout", "holder": "a-laptop", "release_id": "img-x", "expires_at": "2099"}
    assert _tick(w, 0) == TICK_WAITING
    assert w.fly.deploys == [] and w.store.rollouts == {}


# -- promotion across releases in flight (`latest`) -----------------------------------------
# 2026-10-26: five merges in four hours each restarted the controller at ring 0;
# the pilot went three hours without a release and the client seat got nothing
# from the controller. A later ring takes the newest release baked on the ring
# before it.


def _two_releases(w: World) -> tuple[dict, dict]:
    older = w.rel
    newer = {**older, "release_id": "img-202610190100-22222222"}
    w.store.insert_release(newer)
    return older, newer


def _promote(w: World, minutes_after_done: float, candidates: list[dict]) -> int:
    w.clock.t = T0 + minutes_after_done * 60
    w.store.now_iso = datetime.fromtimestamp(w.clock.t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    return ticker.run_promoting(RINGS, candidates, do_seat_for, "h")


def test_with_no_bake_one_tick_takes_staging_then_the_client_seat(tmp_path) -> None:
    """2026-10-30 rings: staging, then ashton-price at once, then SMD's own seats."""
    w = _tick_world(tmp_path)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price"], None, 0),
        Ring(2, ["pilot-smokeball", "scott", "brokery"], 1, 10),
    ]
    w.clock.t = T0
    plan = ImagePlan(w.rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    code = ticker.run_promoting(rings, [w.rel], lambda rel: lambda s, r: w.roll.run(s, r, plan), "h")
    assert code == TICK_PROGRESS
    # FALSIFIER: a bake of even one minute on ring 0 and the client seat waits for the next tick.
    assert _deployed(w)[:2] == ["hermes-smd-staging", "hermes-ashton-price"]


def _done_releases(w: World, seat: str) -> list[str]:
    return [r["release_id"] for r in w.store.rollouts.values() if r["customer_slug"] == seat and r["state"] == "done"]


def _release_n(w: World, n: int) -> dict:
    rel = {**w.rel, "release_id": f"img-20261019{n:02d}00-{str(n) * 8}"}
    w.store.insert_release(rel)
    return rel


def test_a_run_of_merges_faster_than_the_bake_does_not_starve_the_later_rings(tmp_path) -> None:
    # Merges every 20 minutes against a 30-minute bake: each new release takes
    # ring 0 first and is always still baking there.
    w = _tick_world(tmp_path)
    a = w.rel
    b, c, d = _release_n(w, 2), _release_n(w, 3), _release_n(w, 4)
    assert _promote(w, 0, [a]) == TICK_PROGRESS
    assert _promote(w, 20, [b, a]) == TICK_PROGRESS
    assert _promote(w, 40, [c, b, a]) == TICK_PROGRESS
    assert _done_releases(w, "pilot-smokeball") == []
    # At 60, D takes ring 0, and A, eligible for ring 1 for one full bake while
    # every newer release is still on ring 0, takes the pilot in the same tick.
    # FALSIFIER: restart-at-ring-0, or wait for the next newer release with no
    # bound, and the pilot never moves while merges keep landing.
    assert _promote(w, 60, [d, c, b, a]) == TICK_PROGRESS
    assert _done_releases(w, "smd-staging") == [r["release_id"] for r in (a, b, c, d)]
    assert _done_releases(w, "pilot-smokeball") == [a["release_id"]]
    assert any("baked on the earlier rings" in line for line in w.logs)


def test_one_tick_moves_every_ring_that_has_a_release_ready(tmp_path) -> None:
    # 2026-10-26 evening: the client ring sat ready for its release while three
    # ticks in a row went to staging, because each tick moved one ring and the
    # newest release (ready for ring 0 after every merge) always came first.
    w = _tick_world(tmp_path)
    a = w.rel
    for minute in (0, 31, 62):  # A through rings 0, 1, 2
        assert _promote(w, minute, [a]) == TICK_PROGRESS
    b = _release_n(w, 2)
    before = len(w.fly.deploys)
    # FALSIFIER: one ring per tick and B's staging deploy is all this tick does.
    assert _promote(w, 93, [b, a]) == TICK_PROGRESS
    apps = [app for app, _, _ in w.fly.deploys[before:]]
    assert apps == ["hermes-smd-staging", "hermes-ashton-price"], apps
    assert _done_releases(w, "ashton-price") == [a["release_id"]]
    assert _done_releases(w, "smd-staging")[-1] == b["release_id"]


def test_a_seat_done_on_a_newer_release_is_never_pending_for_an_older_one() -> None:
    # FALSIFIER: drop overlay_newer and the older release plans the pilot as
    # pending, which would deploy it backwards onto the older image.
    now = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)
    older_rows = {"smd-staging": {"state": "done", "finished_at": "2026-10-23T00:00:00Z"}}
    newer_rows = {"pilot-smokeball": {"state": "done", "finished_at": "2026-10-23T00:20:00Z"}}
    merged = overlay_newer(older_rows, [newer_rows])
    plan = plan_tick(RINGS, merged, {}, lambda seats: [], now)
    assert plan.code == TICK_PROGRESS and plan.ring is not None and plan.ring.number == 2
    assert "pilot-smokeball" not in (plan.seats or [])
    # A seat done on the older release keeps its own finish time for the bake.
    own = overlay_newer({"smd-staging": {"state": "done", "finished_at": "2026-10-23T00:00:00Z"}},
                        [{"smd-staging": {"state": "done", "finished_at": "2026-10-23T01:59:00Z"}}])  # fmt: skip
    assert own["smd-staging"]["finished_at"] == "2026-10-23T00:00:00Z"


def test_an_older_release_gives_way_to_a_newer_one_about_to_reach_the_ring(tmp_path) -> None:
    # 2026-10-26: the pilot took 1e696594 at 02:36Z and a48c6148 at 02:57Z, two
    # restarts in 21 minutes, because the older release moved while the newer
    # one was already done on staging and minutes from its bake.
    w = _tick_world(tmp_path)
    older, newer = _two_releases(w)
    _promote(w, 0, [older])  # older on staging
    _promote(w, 31, [newer, older])  # newer on staging; older eligible since minute 30
    # FALSIFIER: drop the give-way rule and the pilot takes older here.
    assert _promote(w, 41, [newer, older]) == TICK_WAITING
    assert _done_releases(w, "pilot-smokeball") == []
    assert any("comes next" in line for line in w.logs)
    assert _promote(w, 62, [newer, older]) == TICK_PROGRESS  # newer baked: it goes, once
    assert _done_releases(w, "pilot-smokeball") == [newer["release_id"]]


def test_a_release_published_mid_run_ends_the_run_so_the_next_one_takes_it(tmp_path) -> None:
    """2026-11-05: run 41958879617 rolled a76ce77a through all six seats while 6e384840's
    image waited 33 minutes for the lane. With a refresh after each ring, the run ends
    once a newer release shows up, and the next run starts from ring 0 on it.
    FALSIFIER: drop the refresh and the older release takes the client seat and every
    later seat in this run; the newer one waits for all of them."""
    w = _tick_world(tmp_path)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price"], None, 0),
        Ring(2, ["pilot-smokeball", "scott", "brokery"], 1, 0),
    ]
    older = w.rel
    newer = {**older, "release_id": "img-202610190100-22222222"}
    seen = {"calls": 0}

    def refresh() -> list[dict]:
        # The newer image lands while ring 0 is rolling.
        seen["calls"] += 1
        if newer["release_id"] not in w.store.releases:
            w.store.insert_release(newer)
        return [newer, older]

    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    w.clock.t = T0
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    assert ticker.run_promoting(rings, [older], do_seat_for, "h", refresh=refresh) == TICK_PROGRESS
    assert seen["calls"] == 1
    assert _deployed(w) == ["hermes-smd-staging"]  # the older release stopped after ring 0
    assert any("was published while this run moved ring 0" in line for line in w.logs)
    # The next run: the newer release takes staging, then the client seat, then the rest; the
    # older release is never carried into a ring the newer one is about to reach.
    w.clock.t = T0 + 60
    w.store.now_iso = datetime.fromtimestamp(w.clock.t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        ticker.run_promoting(rings, [newer, older], do_seat_for, "h", refresh=lambda: [newer, older]) == TICK_PROGRESS
    )
    assert _done_releases(w, "ashton-price") == [newer["release_id"]]
    assert _done_releases(w, "pilot-smokeball") == [newer["release_id"]]
    # test_with_no_bake_one_tick_takes_staging_then_the_client_seat covers a run with no newer release.


def test_a_client_ring_rolls_in_waves_and_hands_off_between_them(tmp_path) -> None:
    """Designed for 10 to 20 client seats: a ring with max_parallel rolls a canary alone,
    then that many seats at once, and between waves the run asks whether a newer release
    landed; when it has, the rest of the ring is left for the next run. FALSIFIER: pass
    parallel=1 and the seats deploy one by one; drop the between-wave check and all four
    deploy on the old release."""
    w = _tick_world(tmp_path)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price", "pilot-smokeball", "scott", "brokery"], 2, 0),
    ]
    older = w.rel
    newer = {**older, "release_id": "img-202610190100-22222222"}
    state = {"waves": 0}

    def refresh() -> list[dict]:
        # No newer release until ring 1's canary and first full wave have rolled.
        if len([d for d in w.fly.deploys if d[0] != "hermes-smd-staging"]) >= 3:
            if newer["release_id"] not in w.store.releases:
                w.store.insert_release(newer)
            return [newer, older]
        return [older]

    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    w.clock.t = T0
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    assert ticker.run_promoting(rings, [older], do_seat_for, "h", refresh=refresh) == TICK_PROGRESS
    apps = [app for app, _, _ in w.fly.deploys]
    # staging, the canary, then ONE wave of two client seats, then the hand-off.
    assert apps[0] == "hermes-smd-staging" and len(apps) == 4
    assert apps[1] == "hermes-ashton-price"
    assert set(apps[2:]) == {"hermes-pilot-smokeball", "hermes-scott"}
    assert any("left for the next run" in line for line in w.logs)
    assert state["waves"] == 0  # (the counter exists for readers; the deploy list is the measurement)
    # With no newer release the whole ring rolls, two at a time.
    w2 = _tick_world(tmp_path / "w2")
    w2.clock.t = T0

    def do_seat_for2(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w2.roll.run(s, r, plan)

    ticker2 = Ticker(w2.store, w2.logs.append, w2.clock)  # type: ignore[arg-type]
    assert ticker2.run_promoting(rings, [w2.rel], do_seat_for2, "h", refresh=lambda: [w2.rel]) == TICK_PROGRESS
    assert len(w2.fly.deploys) == 5
    assert _done_releases(w2, "brokery") == [w2.rel["release_id"]]


# -- a client ring: a canary, then the rest at once ----------------------------------------
# A merge reaches every client seat in about two seat restarts after staging, whatever
# the number of client seats; a client-only defect reaches one seat.

CLIENTS = ["ashton-price", "pilot-smokeball", "scott", "brokery"]


def _spy_waves(monkeypatch) -> list[list[str]]:
    """Record the seats of every Waves.run call (one call per wave), delegating to the real one."""
    from bin.lib.fleet import tick as tick_module

    calls: list[list[str]] = []
    real = tick_module.Waves.run

    def run(self, rings, *args, **kwargs):
        calls.append([s for r in rings for s in r.seats])
        return real(self, rings, *args, **kwargs)

    monkeypatch.setattr(tick_module.Waves, "run", run)
    return calls


def _roll(w: World, rings: list[Ring]) -> int:
    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    w.clock.t = T0
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    return ticker.run_promoting(rings, [w.rel], do_seat_for, "h", refresh=lambda: [w.rel])


def test_a_client_ring_rolls_a_canary_alone_then_every_other_seat_at_once(tmp_path, monkeypatch) -> None:
    """FALSIFIER: chunk the ring by max_parallel with no canary and the four seats roll as one wave."""
    w = _tick_world(tmp_path)
    calls = _spy_waves(monkeypatch)
    rings = [Ring(0, ["smd-staging"], None, 0), Ring(1, list(CLIENTS), 20, 0)]
    assert _roll(w, rings) == TICK_PROGRESS
    assert calls == [["smd-staging"], ["ashton-price"], ["pilot-smokeball", "scott", "brokery"]]
    assert any("ring 1 canary ashton-price first, then 3 at once" in line for line in w.logs)
    assert w.store.release_done_seats(w.rel["release_id"]) >= set(CLIENTS)


def test_a_failing_canary_halts_before_any_other_client_seat_deploys(tmp_path, monkeypatch) -> None:
    """Blast radius one seat. FALSIFIER: no canary and all four client seats deploy the bad release."""
    w = _tick_world(tmp_path)
    calls = _spy_waves(monkeypatch)
    w.store.status["ashton-price"] = {**fresh_status(), "selftest_json": selftest_json(failed_count=1, failed=["x"])}
    rings = [Ring(0, ["smd-staging"], None, 0), Ring(1, list(CLIENTS), 20, 0)]
    assert _roll(w, rings) == TICK_HALT
    assert calls == [["smd-staging"], ["ashton-price"]]
    assert set(_deployed(w)) == {"hermes-smd-staging", "hermes-ashton-price"}  # the canary and its rollback
    assert w.store.states()["ashton-price"] == "rolled_back"
    for seat in CLIENTS[1:]:
        assert seat not in w.store.states()


def test_a_ring_with_a_seat_already_done_on_the_release_skips_the_canary(tmp_path, monkeypatch) -> None:
    """An earlier run (e.g. before a hand-off) already proved the release on this ring.
    FALSIFIER: ignore release_done_seats and pilot-smokeball rolls alone as a second canary."""
    w = _tick_world(tmp_path)
    rid = w.rel["release_id"]
    for i, seat in enumerate(["smd-staging", "ashton-price"]):
        w.store.start_rollout({"rollout_id": f"r{i}", "customer_slug": seat, "release_id": rid})
        w.store.update_rollout(f"r{i}", "done")
    calls = _spy_waves(monkeypatch)
    rings = [Ring(0, ["smd-staging"], None, 0), Ring(1, list(CLIENTS), 20, 0)]
    assert _roll(w, rings) == TICK_PROGRESS
    assert calls == [["pilot-smokeball", "scott", "brokery"]]
    assert not any("canary" in line for line in w.logs)


def test_smds_own_last_ring_still_rolls_one_seat_at_a_time(tmp_path, monkeypatch) -> None:
    """Width one: no canary line, one wave per seat. FALSIFIER: drop the width check from the
    canary condition and the last ring logs a canary; ignore max_parallel and both seats roll together."""
    w = _tick_world(tmp_path)
    calls = _spy_waves(monkeypatch)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price", "pilot-smokeball"], 20, 0),
        Ring(2, ["scott", "brokery"], 1, 0),
    ]
    assert _roll(w, rings) == TICK_PROGRESS
    assert calls[-2:] == [["scott"], ["brokery"]]
    assert not any("ring 2 canary" in line for line in w.logs)


def test_a_skills_or_bundle_step_yields_the_lane_when_a_newer_image_lands(tmp_path) -> None:
    """The image step hands off through ``refresh``; the skills and bundle steps hand off through
    ``yield_when``, asked between waves and after each ring. 2026-11-05: run 41982023842's bundle step
    carried its release through every ring (06:57 to 07:22Z) while 6db0f014's image waited.
    FALSIFIER: ignore ``yield_when`` and all four client seats deploy on the old release."""
    from types import SimpleNamespace

    from bin.lib.fleet.artifact_tick import newer_image_since

    w = _tick_world(tmp_path)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price", "pilot-smokeball", "scott", "brokery"], 2, 0),
    ]
    newer = {**w.rel, "release_id": "img-202610190100-22222222"}
    signal = newer_image_since(SimpleNamespace(store=w.store))
    assert signal() is False  # nothing newer yet

    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)

        def seat(s: str, r: int):
            out = w.roll.run(s, r, plan)
            # A newer image lands while the first client wave (the canary) rolls.
            if s != "smd-staging" and newer["release_id"] not in w.store.releases:
                w.store.insert_release(newer)
            return out

        return seat

    w.clock.t = T0
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    assert ticker.run_promoting(rings, [w.rel], do_seat_for, "h", yield_when=signal) == TICK_PROGRESS
    apps = [app for app, _, _ in w.fly.deploys]
    assert apps == ["hermes-smd-staging", "hermes-ashton-price"], apps  # the canary, then the hand-off
    assert any("a newer image was published while this run moved ring 1" in line for line in w.logs)
    assert signal() is True


def test_a_pass_that_holds_the_last_ring_stops_before_it(tmp_path) -> None:
    """``hold_ring`` ends a pass when the front reaches that ring; a pass without it takes the ring.
    FALSIFIER: ignore hold_ring and the first pass deploys SMD's own seats too."""
    w = _tick_world(tmp_path)
    rings = [
        Ring(0, ["smd-staging"], None, 0),
        Ring(1, ["ashton-price"], 5, 0),
        Ring(2, ["scott", "brokery"], 1, 0),
    ]

    def do_seat_for(rel: dict):
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    w.clock.t = T0
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    assert ticker.run_promoting(rings, [w.rel], do_seat_for, "h", hold_ring=2) == TICK_PROGRESS
    assert [app for app, _, _ in w.fly.deploys] == ["hermes-smd-staging", "hermes-ashton-price"]
    assert any("ring 2 waits" in line for line in w.logs)
    # Nothing is left before the held ring: the same pass again waits and deploys nothing.
    assert ticker.run_promoting(rings, [w.rel], do_seat_for, "h", hold_ring=2) == TICK_WAITING
    assert len(w.fly.deploys) == 2 and w.rel["release_id"] not in w.store.completed
    # The pass without the hold takes the last ring and completes the release.
    assert ticker.run_promoting(rings, [w.rel], do_seat_for, "h") == TICK_PROGRESS
    assert [app for app, _, _ in w.fly.deploys][2:] == ["hermes-scott", "hermes-brokery"]
    assert w.rel["release_id"] in w.store.completed


def test_a_latest_tick_gives_client_rings_every_release_before_the_last_ring(monkeypatch) -> None:
    """2026-11-05, run 42041885945: ashton-price took the image at 14:23Z, the run deployed it to SMD's
    own four seats until 14:52Z, and the client's jobs bundle only then started (15:02Z). The order is
    now image, skills, bundle for the rings before the last one, then the three again for the last.
    FALSIFIER: step the image through every ring first and the first call is ("image", None)."""
    from types import SimpleNamespace

    from bin.lib.fleet import artifact_tick

    calls: list[tuple[str, int | None]] = []
    images = [{"release_id": "img-a", "kind": "image"}]
    ctx = SimpleNamespace(
        rings=lambda: [Ring(0, ["smd-staging"], None, 0), Ring(1, ["ashton-price"], 5, 0), Ring(2, ["scott"], 1, 0)],
        log=lambda m: None,
        store=SimpleNamespace(recent_releases=lambda n: list(images)),
    )
    monkeypatch.setattr(
        artifact_tick, "_skills_tick", lambda c, a, hold=None: calls.append(("skills", hold)) or TICK_WAITING
    )
    monkeypatch.setattr(
        artifact_tick, "_bundles_tick", lambda c, a, hold=None: calls.append(("bundle", hold)) or TICK_PROGRESS
    )

    def image(hold: int | None) -> int:
        calls.append(("image", hold))
        return TICK_WAITING

    assert artifact_tick.latest_tick(ctx, None, image) == TICK_PROGRESS  # type: ignore[arg-type]
    client_pass = [("image", 2), ("skills", 2), ("bundle", 2)]
    assert calls == client_pass + [("image", None), ("skills", None), ("bundle", None)]

    # A newer image lands during the client pass: our own seats are left for the run that takes it.
    calls.clear()

    def image_lands(hold: int | None) -> int:
        calls.append(("image", hold))
        images.append({"release_id": "img-b", "kind": "image"})
        return TICK_PROGRESS

    assert artifact_tick.latest_tick(ctx, None, image_lands) == TICK_PROGRESS  # type: ignore[arg-type]
    assert calls == client_pass

    # The image step is refused: skills and the bundle still step the client rings, then it is raised.
    calls.clear()

    def image_refuses(hold: int | None) -> int:
        calls.append(("image", hold))
        raise cli.Refusal("no self-test")

    with pytest.raises(cli.Refusal, match="no self-test"):
        artifact_tick.latest_tick(ctx, None, image_refuses)  # type: ignore[arg-type]
    assert calls == client_pass

    # A fleet of one ring holds nothing.
    calls.clear()
    ctx.rings = lambda: [Ring(0, ["smd-staging"], None, 0)]
    artifact_tick.latest_tick(ctx, None, image)  # type: ignore[arg-type]
    assert calls == [("image", None), ("skills", None), ("bundle", None)]


def test_promotion_never_moves_a_seat_backwards(tmp_path) -> None:
    w = _tick_world(tmp_path)
    older, newer = _two_releases(w)
    _promote(w, 0, [older])  # staging on older
    # The pilot never took older: it was pushed straight to the newer release
    # (an urgent one-seat run).
    w.clock.t = T0 + 20 * 60
    w.store.now_iso = datetime.fromtimestamp(w.clock.t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    w.roll.run("pilot-smokeball", 1, ImagePlan(newer, "3" * 40, verify="selftest", selftest_capable=True,
                                               ssh_fallback=False))  # fmt: skip
    assert _promote(w, 25, [newer, older]) == TICK_PROGRESS  # newer takes staging
    before = len(w.fly.deploys)
    # Newer is baking on staging; older baked there long ago and looks at ring 1.
    # FALSIFIER: drop overlay_newer and older counts the pilot as pending and
    # deploys it backwards onto the older image.
    _promote(w, 40, [newer, older])
    apps = [app for app, _, _ in w.fly.deploys[before:]]
    assert "hermes-pilot-smokeball" not in apps, apps
    assert _done_releases(w, "pilot-smokeball") == [newer["release_id"]]


def test_a_halt_on_the_newest_release_stops_everything(tmp_path) -> None:
    w = _tick_world(tmp_path)
    older, newer = _two_releases(w)
    _promote(w, 0, [older])
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json(failed_count=1, failed=["x"])}
    assert _promote(w, 31, [newer, older]) == TICK_HALT  # newest fails on staging and rolls back
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json()}
    # FALSIFIER: let an older release move while the newest stands halted.
    assert _promote(w, 90, [newer, older]) == TICK_HALTED_STANDING
    assert _done_releases(w, "pilot-smokeball") == []


def test_cli_tick_refuses_force_and_lane_acceptance() -> None:
    w = World()
    ctx = cli.Ctx(cli.REPO, None, w.store, w.r2, w.fly, log=lambda m: None)  # type: ignore[arg-type]
    rid = w.rel["release_id"]
    assert cli.main(["release", rid, "--tick", "--force-mid-job"], ctx=ctx) == 2
    assert cli.main(["release", rid, "--tick", "--accept-lane-inputs"], ctx=ctx) == 2
    assert cli.main(["release", rid, "--tick", "--only", "scott"], ctx=ctx) == 2
    assert w.fly.deploys == []


class AncestorRunner:
    """A clone, as git answers about it. `cat-file -e <sha>^{commit}`: 0 when the
    commit is in the clone. `merge-base --is-ancestor <sha> HEAD`: 0 on main, 1
    present but not on main, 128 when the clone has never seen it (real git)."""

    def __init__(self, on_main: set[str], elsewhere: set[str] | None = None) -> None:
        self.on_main = on_main
        self.present = on_main | (elsewhere or set())
        # Commits merged to origin/main after the clone: they arrive on `git fetch`.
        self.lands_on_fetch: set[str] = set()
        self.fetches = 0
        self.fetch_rc = 0

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        assert argv[:2] == ["git", "-C"]
        if "fetch" in argv:
            assert argv[-1] == f"+refs/heads/main:{cli.MAIN_REF}", "the fetch must move origin/main itself"
            self.fetches += 1
            if self.fetch_rc == 0:
                self.on_main |= self.lands_on_fetch
                self.present |= self.lands_on_fetch
            return Result(self.fetch_rc, "", "")
        if "cat-file" in argv:
            return Result(0 if argv[-1].split("^")[0] in self.present else 128, "", "")
        assert "--is-ancestor" in argv
        assert argv[-1] == cli.MAIN_REF, "main is origin/main, never the checkout's HEAD"
        sha = argv[-2]
        return Result(0 if sha in self.on_main else 1 if sha in self.present else 128, "", "")


def test_the_mid_run_hand_off_sees_an_image_merged_after_the_run_started() -> None:
    """2026-11-05: run 41985907116 (checked out 07:15Z) rolled 6db0f014 through every ring until
    08:03Z while b26226a4, merged 07:26Z with its image recorded 07:34Z, waited: the refresh asked a
    clone that had never seen that commit, so it was never "built from main". The refresh now fetches
    origin/main first. FALSIFIER: drop the fetch and the newer image stays invisible mid-run."""
    w = World()
    newer = dict(w.rel, release_id="img-202610230900-22222222", ss_console_sha="2" * 40,
                 created_at="2026-10-23T09:00:00Z")  # fmt: skip
    w.store.releases = {newer["release_id"]: newer, **w.store.releases}  # newest first
    runner = AncestorRunner({w.rel["ss_console_sha"]})
    runner.lands_on_fetch = {"2" * 40}
    logs: list[str] = []
    ctx = cli.Ctx(cli.REPO, runner, w.store, w.r2, w.fly, log=logs.append)  # type: ignore[arg-type]

    def in_flight() -> list[dict]:
        return cli._in_flight(ctx, cli._image_release(ctx, "latest"))

    # The clone as checked out: the newer commit is unknown, so the older image is "latest".
    assert in_flight()[0]["release_id"] == w.rel["release_id"]
    # A fetch that fails is logged and the stale answer stands.
    runner.fetch_rc = 128
    assert cli.refreshed(ctx, in_flight)[0]["release_id"] == w.rel["release_id"]
    assert any("fetch origin main failed" in line for line in logs)
    # The hand-off question fetches first and sees it.
    runner.fetch_rc = 0
    assert cli.refreshed(ctx, in_flight)[0]["release_id"] == newer["release_id"]
    assert runner.fetches == 2


def test_cli_tick_refuses_an_image_without_the_selftest() -> None:
    w = World()
    runner = AncestorRunner({w.rel["ss_console_sha"]})
    ctx = cli.Ctx(cli.REPO, runner, w.store, w.r2, w.fly, log=lambda m: None)  # type: ignore[arg-type]
    assert cli.main(["release", "latest", "--tick"], ctx=ctx) == 4
    w.r2.put_json(image_key(DIGEST, "image-capabilities.json"), {"selftest": True})
    w.store.release_latest = lambda rid: {}  # type: ignore[method-assign]
    assert cli.main(["release", "latest", "--tick", "--dry-run"], ctx=ctx) == TICK_PROGRESS
    assert w.fly.deploys == [] and w.store.rollouts == {}


def test_latest_is_the_newest_image_built_from_main_never_a_hotfix_lane_image() -> None:
    w = World()
    hotfix = dict(w.rel, release_id="img-202610230900-99999999", ss_console_sha="9" * 40)
    w.store.releases = {hotfix["release_id"]: hotfix, **w.store.releases}  # newest first
    ctx = cli.Ctx(cli.REPO, AncestorRunner({w.rel["ss_console_sha"]}), w.store, w.r2, w.fly)  # type: ignore[arg-type]
    # FALSIFIER: take the newest image row and the hotfix image rolls to every ring.
    assert cli._image_release(ctx, "latest")["release_id"] == w.rel["release_id"]
    ctx.runner = AncestorRunner(set(), {w.rel["ss_console_sha"], "9" * 40})  # type: ignore[assignment]
    with pytest.raises(cli.Refusal, match="built from main"):
        cli._image_release(ctx, "latest")


def test_from_a_hotfix_worktree_a_hotfix_image_is_still_not_built_from_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A laptop run from a hotfix-lane worktree: HEAD is the hotfix branch, so the
    # hotfix commit IS an ancestor of HEAD. Real git, not a fake.
    # FALSIFIER: ask `--is-ancestor <sha> HEAD` (the old check) and the hotfix
    # image counts as main, so `latest` rolls it to every ring.
    import subprocess

    from bin.lib.fleet.runner import CommandRunner

    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": os.environ["PATH"]}  # fmt: skip

    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(tmp_path), *args], env=env, check=True, capture_output=True, text=True)  # noqa: S603 - argv list of fixed git args, never a shell
        return out.stdout.strip()

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "main")
    main_sha = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", main_sha)
    git("checkout", "-q", "-b", "hotfix/x")
    git("commit", "-q", "--allow-empty", "-m", "hotfix")
    hotfix_sha = git("rev-parse", "HEAD")

    ctx = cli.Ctx(tmp_path, CommandRunner(), None, None, None)  # type: ignore[arg-type]
    assert cli._built_from_main(ctx, main_sha) is True
    assert cli._built_from_main(ctx, hotfix_sha) is False


def test_a_hotfix_image_that_carries_the_skills_commit_takes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-31: A&P on a hotfix image waited ~15 h per skills release ("not a
    recorded release built from main") though the hotfix carried the skills' code.
    Real git. FALSIFIER: require the image to be on main and the first assertion fails."""
    import subprocess

    from bin.lib.fleet.runner import CommandRunner

    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": os.environ["PATH"]}  # fmt: skip

    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(tmp_path), *args], env=env, check=True, capture_output=True, text=True)  # noqa: S603 - argv list of fixed git args, never a shell
        return out.stdout.strip()

    # base(S) (ADR 0100 W3) reads the repo's own image path lists, so the clone
    # carries them, and both main commits change the image (an operator/templates
    # file): the skills here are written against code their own commit changed.
    import shutil

    from bin.lib.fleet import ordering

    repo = Path(cli.REPO)
    for rel in (ordering.SUBSTRATE_PATHS, ordering.ARTIFACT_PATHS, ordering.DETECT_SCRIPT):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo / rel, tmp_path / rel)
    (tmp_path / "operator/templates").mkdir(parents=True)

    def image_commit(label: str) -> str:
        (tmp_path / "operator/templates" / label.replace(" ", "-")).write_text(label)
        git("add", "-A")
        git("commit", "-q", "-m", label)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    before = image_commit("before the skills")
    skills_sha = image_commit("skills")
    git("update-ref", "refs/remotes/origin/main", skills_sha)
    git("checkout", "-q", "-b", "hotfix/new")
    git("commit", "-q", "--allow-empty", "-m", "hotfix on top")
    carries = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "hotfix/old", before)
    git("commit", "-q", "--allow-empty", "-m", "hotfix before the skills")
    lacks = git("rev-parse", "HEAD")

    class Store:
        def fleet_status(self) -> dict:
            return {"ashton-price": {"image_ref": "img:carries"}, "scott": {"image_ref": "img:lacks"},
                    "brokery": {"image_ref": "img:unrecorded"}}  # fmt: skip

        def release_for_image(self, ref: str) -> dict | None:
            return {"img:carries": {"ss_console_sha": carries}, "img:lacks": {"ss_console_sha": lacks}}.get(ref)

    ctx = cli.Ctx(tmp_path, CommandRunner(), Store(), None, None)  # type: ignore[arg-type]
    rel = {"ss_console_sha": skills_sha, "skills_version": "202610311200-" + skills_sha[:8]}
    waiting = cli._skills_defer(ctx, ["ashton-price", "scott", "brokery"])(rel)
    assert "ashton-price" not in waiting
    assert "waiting for the image" in waiting["scott"]
    assert "not a recorded release" in waiting["brokery"]


def test_the_in_flight_window_reaches_the_release_ready_for_the_client_ring() -> None:
    # 2026-10-26: the release ready for ring 3 was the 7th newest of those in
    # flight and the window held 6, so a tick skipped the client seat.
    # FALSIFIER: set IN_FLIGHT_MAX back to 6 and the oldest release drops out.
    w = World()
    w.r2.put_json(image_key(DIGEST, "image-capabilities.json"), {"selftest": True})
    rels = [
        dict(w.rel, release_id=f"img-2026102309{n:02d}-{str(n % 10) * 8}", ss_console_sha=f"{n % 10}" * 40,
             created_at=f"2026-10-23T09:{n:02d}:00Z")
        for n in range(1, 9)
    ]  # fmt: skip
    w.store.releases = {r["release_id"]: r for r in reversed(rels)}  # newest first
    on_main = {r["ss_console_sha"] for r in rels}
    ctx = cli.Ctx(cli.REPO, AncestorRunner(on_main), w.store, w.r2, w.fly)  # type: ignore[arg-type]
    in_flight = cli._in_flight(ctx, rels[-1])
    assert [r["release_id"] for r in in_flight] == [r["release_id"] for r in reversed(rels)]


def test_an_image_from_a_commit_the_clone_never_saw_is_not_from_main_and_not_an_error() -> None:
    # 2026-10-26: a drill image built from a local, unpushed commit sat among the
    # recent releases. Weighing releases in flight reached it, merge-base exited
    # 128, and every tick halted as a tooling error for an hour.
    # FALSIFIER: drop the cat-file presence check and this raises ToolingError.
    w = World()
    drill = dict(w.rel, release_id="img-202610230900-88888888", ss_console_sha="8" * 40)
    newest = dict(w.rel, release_id="img-202610231000-77777777", ss_console_sha="7" * 40,
                  created_at="2026-10-23T10:00:00Z")  # fmt: skip
    w.rel["created_at"] = "2026-10-23T08:00:00Z"
    drill["created_at"] = "2026-10-23T09:00:00Z"
    w.store.releases = {r["release_id"]: r for r in (newest, drill, w.rel)}
    w.r2.put_json(image_key(DIGEST, "image-capabilities.json"), {"selftest": True})
    ctx = cli.Ctx(cli.REPO, AncestorRunner({"7" * 40, w.rel["ss_console_sha"]}), w.store, w.r2, w.fly)  # type: ignore[arg-type]
    assert cli._image_release(ctx, "latest")["release_id"] == newest["release_id"]
    in_flight = [r["release_id"] for r in cli._in_flight(ctx, newest)]
    assert in_flight == [newest["release_id"], w.rel["release_id"]]  # the drill image is skipped, quietly


# -- Fly tokens: per app, never printed ------------------------------------------------------

SEAT_TOK = "FlyV1 fm2_seatsecretvalue"
REG_TOK = "FlyV1 fm2_registrysecretvalue"
CI_ENV = {"GITHUB_ACTIONS": "true", "FLY_DEPLOY_TOKEN__SMD_STAGING": SEAT_TOK, "FLY_REGISTRY_TOKEN": REG_TOK}


class RecordingRunner:
    def __init__(self, out: str = "[]") -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self.out = out

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        self.calls.append((list(argv), dict(env) if env else None))
        return Result(0, self.out, "")


def test_token_name_for_a_seat_app() -> None:
    assert seat_token_env_name("hermes-smd-staging") == "FLY_DEPLOY_TOKEN__SMD_STAGING"
    assert seat_token_env_name("hermes-captain-mark") == "FLY_DEPLOY_TOKEN__CAPTAIN_MARK"
    with pytest.raises(ToolingError):
        seat_token_env_name("hermes")


def test_a_seat_call_carries_the_seat_and_registry_macaroons_combined() -> None:
    runner = RecordingRunner()
    fly = Fly(runner, "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    fly.machines("hermes-smd-staging")
    _, env = runner.calls[-1]
    assert env == {"FLY_API_TOKEN": "FlyV1 fm2_seatsecretvalue,fm2_registrysecretvalue"}
    assert combine_tokens(SEAT_TOK, REG_TOK) == "FlyV1 fm2_seatsecretvalue,fm2_registrysecretvalue"


def test_a_registry_call_carries_the_registry_token_alone() -> None:
    runner = RecordingRunner()
    fly = Fly(runner, "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    fly.app_exists("hermes-fleet")
    fly.build_push("/tmp/fly.toml", "rel-x", "1" * 40, ("2" * 40, 7))
    assert [env for _, env in runner.calls] == [{"FLY_API_TOKEN": REG_TOK}] * 2
    extractor = ImageExtractor(RecordingRunner(out="tok"), tokens=FlyTokens(CI_ENV))  # type: ignore[arg-type]
    extractor._auth()
    assert extractor.runner.calls[-1][1] == {"FLY_API_TOKEN": REG_TOK}  # type: ignore[attr-defined]


def test_the_build_push_hands_fly_both_bundle_floor_build_args() -> None:
    """The image bakes its jobs bundle floor (2026-11-07). FALSIFIER: drop either
    --build-arg and the Dockerfile refuses every build."""
    runner = RecordingRunner()
    Fly(runner, "/repo", FlyTokens(CI_ENV)).build_push("/tmp/fly.toml", "rel-x", "1" * 40, ("f" * 40, 2680))  # type: ignore[arg-type]
    argv = runner.calls[-1][0]
    assert f"SMD_BUNDLE_FLOOR_SHA={'f' * 40}" in argv and "SMD_BUNDLE_FLOOR_SEQ=2680" in argv


class ScriptedRunner(RecordingRunner):
    def __init__(self, result: Result) -> None:
        super().__init__()
        self.result = result

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        super().run(argv, input_text=input_text, timeout=timeout, cwd=cwd, env=env)
        return self.result


def test_app_exists_asks_about_the_one_app_and_never_lists_the_org() -> None:
    # FALSIFIER: an org-wide `fly apps list` is refused as unauthorized to the
    # app-scoped token CI holds; every CI image build failed that way (rc 3)
    # from 2026-10-26 16:39Z, run 40360962583 onward.
    runner = RecordingRunner()
    fly = Fly(runner, "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    assert fly.app_exists("hermes-fleet")
    argv, env = runner.calls[-1]
    assert argv == ["fly", "status", "-a", "hermes-fleet", "--json"]
    assert env == {"FLY_API_TOKEN": REG_TOK}


def test_app_exists_reads_not_found_as_no_and_anything_else_as_a_tooling_error() -> None:
    missing = 'Error: failed to get app: failed to run query (...): Could not find App "hermes-zz"'
    fly = Fly(ScriptedRunner(Result(1, "", missing)), "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    assert fly.app_exists("hermes-fleet") is False
    refused = "Error: unauthorized (Request ID: 01M5WVSBZ3EP8ENTTX85MA964K-lax)"
    fly = Fly(ScriptedRunner(Result(1, "", refused)), "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    with pytest.raises(ToolingError, match="unauthorized"):
        fly.app_exists("hermes-fleet")


class MachineRunner(RecordingRunner):
    """Answers `machines list` with one Machine and every other call with ``out``."""

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        super().run(argv, input_text=input_text, timeout=timeout, cwd=cwd, env=env)
        if argv[1:3] == ["machines", "list"]:
            return Result(0, '[{"id": "6e8206d0f36d08", "state": "started"}]', "")
        return Result(0, self.out, "")


def test_a_seat_program_runs_by_machine_exec_with_the_source_intact() -> None:
    # FALSIFIER: ship it over `fly ssh console` and CI needs a fresh WireGuard
    # peer per run, which lost two of three busy probes on 2026-10-26.
    import base64

    runner = MachineRunner(out="SMD_BUSY_PROBE {}")
    fly = Fly(runner, "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    source = "import json\nARGS = json.loads(\"{\\\"a\\\": 'b c'}\")\nprint('x')\n"
    fly.run_python("hermes-smd-staging", source, timeout=60)
    argv, env = runner.calls[-1]
    assert argv[:4] == ["fly", "machine", "exec", "6e8206d0f36d08"]
    assert argv[5:] == ["-a", "hermes-smd-staging", "--timeout", "60"]
    command = argv[4]
    prefix, suffix = "python3 -I -c \"import base64;exec(base64.b64decode('", "'))\""
    assert command.startswith(prefix) and command.endswith(suffix)
    assert base64.b64decode(command[len(prefix) : -len(suffix)]).decode() == source
    assert env == {"FLY_API_TOKEN": "FlyV1 fm2_seatsecretvalue,fm2_registrysecretvalue"}


def test_no_fly_call_in_the_fleet_library_opens_an_ssh_tunnel() -> None:
    src = Path(fly_module.__file__).read_text(encoding="utf-8")
    assert '"ssh", "console"' not in src, "fleet calls run by `fly machine exec`; ssh needs a WireGuard peer"


def test_no_fly_call_in_the_fleet_library_lists_the_whole_org() -> None:
    # CI holds only app-scoped tokens. An org-wide verb compiles, passes every
    # fake, and fails only in CI against Fly; this keeps the class out.
    src = "".join(p.read_text(encoding="utf-8") for p in Path(fly_module.__file__).parent.glob("*.py"))
    for verb in ('"apps", "list"', '"orgs"', '"apps", "ls"'):
        assert verb not in src, f"org-wide fly call {verb} in operator/bin/lib/fleet"


def test_tokens_never_reach_an_argv_and_repr_hides_them() -> None:
    runner = RecordingRunner()
    tokens = FlyTokens(CI_ENV)
    Fly(runner, "/repo", tokens).secret_names("hermes-smd-staging")  # type: ignore[arg-type]
    assert not any("secretvalue" in arg for argv, _ in runner.calls for arg in argv)
    assert "secretvalue" not in repr(tokens)


def test_a_missing_seat_token_in_ci_refuses_that_seat_and_never_uses_another_login() -> None:
    runner = RecordingRunner()
    fly = Fly(runner, "/repo", FlyTokens(CI_ENV))  # type: ignore[arg-type]
    # FALSIFIER: fall back to the ambient login and this call runs with env=None.
    with pytest.raises(ToolingError, match="FLY_DEPLOY_TOKEN__SCOTT") as err:
        fly.machines("hermes-scott")
    assert "secretvalue" not in str(err.value)
    assert runner.calls == []
    with pytest.raises(ToolingError, match="names no app"):
        Fly(runner, "/repo", FlyTokens({"GITHUB_ACTIONS": "true"}))._fly("apps", "list")  # type: ignore[arg-type]


def test_a_missing_seat_token_ends_that_seat_as_a_tooling_error_before_anything_is_touched() -> None:
    w = World()

    class ScopedFly(type(w.fly)):  # type: ignore[misc]
        def secret_names(self, app: str) -> tuple[set[str], set[str]]:
            FlyTokens({"GITHUB_ACTIONS": "true", "FLY_REGISTRY_TOKEN": REG_TOK}).env_for(app)
            return super().secret_names(app)

    w.fly.__class__ = ScopedFly
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "tooling_error"
    assert w.fly.deploys == [] and w.fly.snapshots == []


def test_a_laptop_uses_its_own_login() -> None:
    runner = RecordingRunner()
    Fly(runner, "/repo", FlyTokens({"HOME": "/Users/x"})).machines("hermes-scott")  # type: ignore[arg-type]
    assert runner.calls[-1][1] is None


# -- verify-ledger rows -------------------------------------------------------------------------

VID = "vfy_01M5NGDSEQ5A8SMWC96RSE0Z3C"


def test_each_judged_seat_writes_a_live_state_row_with_the_relay_key() -> None:
    sent: list[tuple[str, dict, dict]] = []

    def post(url: str, body: dict, headers: dict) -> tuple[int, Any]:
        sent.append((url, body, headers))
        return 201, {"verify": {"id": VID}}

    w = World()
    w.roll.ledger = VerifyLedger("relay-secret", post=post)
    w.release_all(only="smd-staging")
    url, body, headers = sent[0]
    assert url.endswith("/verify") and headers == {"X-Relay-Key": "relay-secret"}
    assert body["method"] == "live_state" and body["command"] and len(body["claim"]) <= 300
    assert body["tool_used"] in ("Bash", "wrangler", "other") and body["source"] == "tool"
    assert f"verify row: {VID}" in next(iter(w.store.rollouts.values()))["detail"]
    assert not any("relay-secret" in line for line in w.logs)


def test_a_ledger_failure_is_a_note_and_never_fails_the_seat() -> None:
    w = World()
    w.roll.ledger = VerifyLedger("k", post=lambda u, b, h: (503, None))
    w.release_all(only="smd-staging")
    row = next(iter(w.store.rollouts.values()))
    assert row["state"] == "done" and "verify row: not written (HTTP 503)" in row["detail"]

    def boom(u: str, b: dict, h: dict) -> tuple[int, Any]:
        raise OSError("network down")

    assert VerifyLedger("k", post=boom).record("scott", "img-x", "ro-1", "done", "d") == (
        "verify row: not written (OSError)"
    )
    assert (
        VerifyLedger(None, required=True)
        .record("scott", "img-x", "ro-1", "done", "d")
        .startswith("verify row: not written (no CRANE_RELAY_KEY")
    )
    assert VerifyLedger(None).record("scott", "img-x", "ro-1", "done", "d") == ""


def test_ledger_output_is_kept_under_the_route_limit() -> None:
    text, mode = head_tail("x" * 20_000)
    assert mode == "head_tail" and len(text.encode()) <= 8 * 1024
    assert head_tail("short") == ("short", "none")
