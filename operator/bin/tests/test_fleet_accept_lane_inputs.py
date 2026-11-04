"""A person's call that the seats' staged firm inputs fit a release's schema, recorded once on the
release (`fleet accept-lane-inputs`), is what the tick's preflight honors (2026-11-04: ADR 0103 renamed
the runner's package, every lane's digest changed in name only, and the tick halted at staging)."""

from __future__ import annotations

import pytest

from bin.lib.fleet import cli
from bin.lib.fleet.preflight import ACCEPTED_LANE_INPUTS, accepted_lanes, check_lanes
from bin.lib.fleet.r2 import image_key
from bin.tests.fleet_fakes import DIGEST, FakeFly, FakeR2, FakeStore, release
from bin.tests.test_fleet_release import _lane_world


def _ctx(store: FakeStore, r2: FakeR2) -> cli.Ctx:
    return cli.Ctx(cli.REPO, None, store, r2, FakeFly(), log=lambda m: None)  # type: ignore[arg-type]


def test_accepted_lanes_reads_every_lane_a_list_or_nothing() -> None:
    assert accepted_lanes(None) == set()
    assert accepted_lanes({"lanes": "*"}) == {"*"}
    assert accepted_lanes({"lanes": ["records", "demand"]}) == {"records", "demand"}
    assert accepted_lanes({"lanes": 3}) == set()


def test_check_lanes_honors_a_declaration_for_its_lanes_only() -> None:
    decl = {"lanes": ["records"], "by": "scott", "at": "2026-11-04T19:00:00Z", "why": "names only"}
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, decl)
    assert problems == [] and "accepted on the release by scott" in notes[0]
    problems, notes = check_lanes("s", {"demand": "new"}, {"demand": "old"}, ["demand"], False, decl)
    assert notes == [] and "schema changed" in problems[0]
    problems, _ = check_lanes("s", {"demand": "new"}, {"demand": "old"}, ["demand"], False, {**decl, "lanes": "*"})
    assert problems == []
    # FALSIFIER: a declaration is only ever read from the release's own digest key; one for
    # another release, or none, leaves the refusal exactly as it was.
    problems, _ = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, None)
    assert "schema changed" in problems[0]


def test_the_cli_records_the_call_on_the_release_and_the_rollout_then_passes() -> None:
    w = _lane_world()
    ctx = _ctx(w.store, w.r2)
    with pytest.raises(SystemExit):  # --why is the record; without it there is no call to honor
        cli.main(["accept-lane-inputs", w.rel["release_id"]], ctx=ctx)
    assert cli.main(["accept-lane-inputs", "img-doesnotexist", "--why", "x"], ctx=ctx) == 4
    assert (
        cli.main(["accept-lane-inputs", w.rel["release_id"], "--why", "ADR 0103: env names and imports"], ctx=ctx) == 0
    )
    decl = w.r2.get_json(image_key(DIGEST, ACCEPTED_LANE_INPUTS))
    assert decl["lanes"] == "*" and decl["why"] == "ADR 0103: env names and imports" and decl["by"] and decl["at"]
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "done"
    assert w.fly.deploys


def test_a_declaration_for_another_lane_still_refuses() -> None:
    w = _lane_world()
    ctx = _ctx(w.store, w.r2)
    assert (
        cli.main(["accept-lane-inputs", w.rel["release_id"], "--why", "demand only", "--lane", "demand"], ctx=ctx) == 0
    )
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "failed"
    assert w.fly.deploys == []


def test_a_release_with_no_digest_refuses_the_declaration() -> None:
    store, r2 = FakeStore(), FakeR2()
    store.insert_release(release(image_digest=None))
    assert cli.main(["accept-lane-inputs", release()["release_id"], "--why", "x"], ctx=_ctx(store, r2)) == 4
    assert r2.get_json(image_key(DIGEST, ACCEPTED_LANE_INPUTS)) is None
