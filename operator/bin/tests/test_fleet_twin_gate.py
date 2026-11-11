"""The twin gate (ADR 0107): a client seat takes only a release its release twin has proved.

On 2026-10-30 releases 9bd7caa3 and f835d1d9 reached ashton-price with no
smd-staging rollout row. These tests pin the gate on every path a release can
reach a client seat: the controller's tick (``tick.twin_deferrals``, read from
the release's OWN rows, never the newer-release overlay), a laptop full run and
a laptop ``--only`` (``waves.Waves._twin_waiting``), the jobs-bundle rings, and
the rings file the generator writes.

Each test names its falsifier. The KILL TESTS at the bottom remove the gate
(monkeypatch) and assert the same scenario then DOES reach the client seat, so
the scenarios are proven able to fail: a gate test that would pass with no gate
measures nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from bin.lib.fleet import bundles, tick
from bin.lib.fleet.rings import Ring, load_rings
from bin.lib.fleet.rollout import ImagePlan
from bin.lib.fleet.runner import Refusal
from bin.lib.fleet.tick import TICK_PROGRESS, TICK_WAITING, Ticker, overlay_newer, plan_tick
from bin.lib.fleet.waves import Lease, Waves
from bin.tests.fleet_fakes import fresh_status, selftest_json
from bin.tests.test_fleet_release import World

OPERATOR = Path(__file__).resolve().parents[2]
CLIENT, TWIN = "ashton-price", "ap-staging"
NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
DONE = {"state": "done", "finished_at": "2026-10-30T11:00:00Z"}


def _rings() -> list[Ring]:
    return [
        Ring(0, [TWIN], None, 0),
        Ring(1, [CLIENT], 20, 0, {CLIENT: TWIN}),
        Ring(2, ["pilot-smokeball", "scott", "brokery"], 1, 10),
    ]


def _world() -> World:
    w = World()
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json()}
    return w


def _plan(w: World) -> ImagePlan:
    return ImagePlan(w.rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)


def _waves(w: World, only: str | None = None) -> Any:
    plan = _plan(w)
    waves = Waves(w.store, w.logs.append)  # type: ignore[arg-type]
    with Lease(w.store, "tester", w.rel["release_id"]) as lease:  # type: ignore[arg-type]
        return waves.run(_rings(), lambda s, r: w.roll.run(s, r, plan), release=w.rel, only=only,
                         parallel=1, dry_run=False, lease=None if only else lease)  # fmt: skip


def _deployed(w: World) -> list[str]:
    return [app for app, _, _ in w.fly.deploys]


# -- the controller (tick) ---------------------------------------------------------------


def test_the_client_waits_while_its_twin_has_no_row_for_the_release() -> None:
    # The 10-30 shape: the twin is done only on a NEWER release, so the overlay
    # makes ring 0 look done for this one. FALSIFIER: read the twin from the
    # overlaid rows (or drop twin_deferrals) and ring 1 takes the client here.
    merged = overlay_newer({}, [{TWIN: DONE}])
    plan = plan_tick(_rings(), merged, {}, lambda seats: [], NOW, own={})
    assert plan.code == TICK_WAITING, plan.reason
    assert f"its release twin {TWIN} is not done on this release" in plan.reason
    assert "no rollout row" in plan.reason


def test_the_client_takes_the_release_once_its_twin_is_done_on_it() -> None:
    rows = {TWIN: DONE}
    plan = plan_tick(_rings(), rows, {}, lambda seats: [], NOW)
    assert plan.code == TICK_PROGRESS and plan.seats == [CLIENT]


def test_a_twin_that_failed_or_stalled_on_the_release_releases_nothing() -> None:
    for state in ("tooling_error", "waiting_idle", "skipped_held"):
        rows = {TWIN: {"state": state, "finished_at": "2026-10-30T11:00:00Z"}}
        waiting = tick.twin_deferrals(_rings(), rows)
        assert CLIENT in waiting and state in waiting[CLIENT]


def test_a_twin_in_no_ring_keeps_its_client_from_every_release() -> None:
    # Fail closed: a twin that is unprovisioned or dropped from the rings never
    # gets a row, so its client never takes a release. FALSIFIER: skip a twin
    # that is in no ring and the client goes.
    rings = [Ring(1, [CLIENT], 20, 0, {CLIENT: TWIN}), Ring(2, ["scott"], 1, 10)]
    plan = plan_tick(rings, {}, {}, lambda seats: [], NOW)
    assert plan.code == TICK_WAITING and TWIN in plan.reason


def test_a_controller_tick_never_deploys_the_client_before_its_twin_on_that_release() -> None:
    # Older release A; newer release B already done on the twin. B is held back
    # from ring 1 (deferred, as a bundle alignment would), so A is the one that
    # looks ready for the client. FALSIFIER: drop the twin gate and A deploys
    # the client with no twin row for A.
    w = _world()
    older = w.rel
    newer = {**older, "release_id": "img-202610300100-22222222"}
    w.store.insert_release(newer)
    w.store.now_iso = "2026-10-30T11:00:00Z"  # the twin finishes B here...
    w.clock.t = NOW.timestamp()  # ...and the tick runs an hour later, past any bake
    w.roll.run(TWIN, 0, ImagePlan(newer, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False))
    ticker = Ticker(
        w.store, w.logs.append, w.clock,  # type: ignore[arg-type]
        defer=lambda rel: {CLIENT: "bundle not published"} if rel["release_id"] == newer["release_id"] else {},
    )  # fmt: skip
    before = len(w.fly.deploys)

    def do_seat_for(rel: dict) -> Any:
        plan = ImagePlan(rel, "3" * 40, verify="selftest", selftest_capable=True, ssh_fallback=False)
        return lambda s, r: w.roll.run(s, r, plan)

    ticker.run_promoting(_rings(), [newer, older], do_seat_for, "h")
    assert "hermes-ashton-price" not in _deployed(w)[before:], w.logs


# -- a laptop run (Waves) ------------------------------------------------------------------


def test_only_the_client_is_refused_until_its_twin_is_done_on_the_release() -> None:
    # The urgent one-seat path. FALSIFIER: drop _twin_waiting and the client deploys.
    w = _world()
    with pytest.raises(Refusal, match=f"its release twin {TWIN} is not done"):
        _waves(w, only=CLIENT)
    assert _deployed(w) == []
    _waves(w, only=TWIN)
    _waves(w, only=CLIENT)
    assert _deployed(w) == ["hermes-ap-staging", "hermes-ashton-price"]


def test_a_full_run_with_the_twin_held_never_reaches_the_client() -> None:
    # A hold skips the twin; before the gate the run went straight on to ring 1.
    w = _world()
    w.store.hold(TWIN, "maintenance", "captain")
    result = _waves(w)
    states = {o.seat: o.state for o in result.outcomes}
    assert states[TWIN] == "skipped_held" and states[CLIENT] == "waiting_twin"
    assert "hermes-ashton-price" not in _deployed(w)
    assert not result.halted


def test_a_full_run_counts_a_twin_that_finished_earlier_in_the_same_run() -> None:
    w = _world()
    result = _waves(w)
    assert _deployed(w)[:2] == ["hermes-ap-staging", "hermes-ashton-price"]
    assert {o.seat: o.state for o in result.outcomes}[CLIENT] == "done"


# -- the rings the tools read --------------------------------------------------------------


def test_the_generated_rings_file_gives_the_client_its_twin() -> None:
    rings = load_rings(OPERATOR / "fleet" / "rings.yaml", OPERATOR / "customers")
    seats = yaml.safe_load((OPERATOR / "fleet" / "seats.yaml").read_text())["seats"]
    twins = {s: e["twin_of"] for s, e in seats.items() if e["purpose"] == "release-twin"}
    assert twins, "no release twin is declared in any seat.yaml"
    for twin, client in twins.items():
        ring_of = {s: r.number for r in rings for s in r.seats}
        holder = next(r for r in rings if client in r.seats)
        assert holder.twins[client] == twin
        assert ring_of[twin] == 0 and ring_of[client] > 0


def test_the_jobs_bundle_rings_keep_the_twin_gate() -> None:
    # FALSIFIER: authoring_rings rebuilds a Ring without its twins and a client's
    # bundle stops waiting for the twin.
    narrowed = bundles.authoring_rings(_rings(), {TWIN, CLIENT})
    assert next(r for r in narrowed if CLIENT in r.seats).twins == {CLIENT: TWIN}


def test_rings_refuse_a_twins_block_that_is_not_a_mapping(tmp_path: Path) -> None:
    (tmp_path / "customers").mkdir()
    rings_file = tmp_path / "rings.yaml"
    rings_file.write_text("rings:\n  - ring: 0\n    seats: []\ntwins: [a, b]\n")
    with pytest.raises(ValueError, match="twins"):
        load_rings(rings_file, tmp_path / "customers")


# -- kill tests: remove the gate and the same scenarios reach the client --------------------


def test_kill_the_tick_gate_and_the_10_30_bypass_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tick, "twin_deferrals", lambda rings, own: {})
    merged = overlay_newer({}, [{TWIN: DONE}])
    plan = plan_tick(_rings(), merged, {}, lambda seats: [], NOW, own={})
    assert plan.code == TICK_PROGRESS and plan.seats == [CLIENT]


def test_kill_the_waves_gate_and_a_held_twin_lets_the_client_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Waves, "_twin_waiting", lambda self, ring, seat, release: None)
    w = _world()
    w.store.hold(TWIN, "maintenance", "captain")
    _waves(w)
    assert "hermes-ashton-price" in _deployed(w)
