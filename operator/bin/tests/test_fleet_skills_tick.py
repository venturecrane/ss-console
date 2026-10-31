"""The release controller walks skills releases too (2026-10-28).

Before this, CI published a candidate skills release on every merge touching
operator/skills/** and nothing rolled it out: five candidates sat unreleased
behind skl-202610272001-04e1b619 while `fleet status` showed drift [skills].
A `latest` tick now steps the skills releases after the image releases, with
the image's discipline (rings, bake, holds, standing halt, lease) plus one rule
of its own: a seat takes skills only once its image has the skills' commit.

Each test names its falsifier: the one change to the code that turns it red.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from bin.lib.fleet import cli
from bin.lib.fleet.r2 import image_key, pointer_key, skills_manifest_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.runner import Result
from bin.lib.fleet.skills import SkillsRollout, deferrals
from bin.lib.fleet.tick import (
    TICK_HALT,
    TICK_HALTED_STANDING,
    TICK_PROGRESS,
    TICK_WAITING,
    Ticker,
    combine,
    plan_tick,
)
from bin.tests.fleet_fakes import DIGEST, FakeFly, FakeR2, FakeStore, fresh_status, release

RINGS = [
    Ring(0, ["smd-staging"], None, 30),
    Ring(1, ["pilot-smokeball"], None, 30),
    Ring(2, ["scott", "brokery"], None, 30),
    Ring(3, ["ashton-price"], 1, 30),
]
SEATS = [s for ring in RINGS for s in ring.seats]
T0 = datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()

# main, oldest first. The image every seat runs (fleet_fakes.release) is at "1"*40.
IMAGE_SHA = "1" * 40
OLD_SKILLS_SHA = "0" * 40  # before the image: may go
NEW_SKILLS_SHA = "2" * 40  # after the image: waits for it
MAIN = [OLD_SKILLS_SHA, IMAGE_SHA, NEW_SKILLS_SHA, "3" * 40]

OLD = "202610280100-00000000"
NEW = "202610280200-22222222"
CAPS = {"schema_version": 1, "paths": [], "tools": ["email_search"], "verbs": [], "action_classes": ["read"]}


def skills_rel(version: str, sha: str) -> dict[str, Any]:
    return {"release_id": f"skl-{version}", "kind": "skills", "skills_version": version, "ss_console_sha": sha}


def ancestor(older: str, newer: str) -> bool:
    return MAIN.index(older) <= MAIN.index(newer)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class World:
    """Skills releases against fakes. A seat's heartbeat reports whatever its pointer says."""

    def __init__(self) -> None:
        self.store, self.r2, self.clock = FakeStore(), FakeR2(), Clock()
        self.logs: list[str] = []
        self.store.insert_release(release())  # the image every seat runs
        self.r2.put_json(image_key(DIGEST, "image-capabilities.json"), CAPS)
        for seat in SEATS:
            self.store.status[seat] = fresh_status()
        self.needs: dict[str, Any] = {"smd-note": {"tools": ["email_search"]}}
        self.roll = SkillsRollout(self.store, self.r2, self.logs.append, self.clock.sleep, self.clock)  # type: ignore[arg-type]
        real = self.store.seat_status

        def seat_status(seat: str) -> dict[str, Any] | None:
            row = real(seat)
            if row is not None:
                row["skills_version"] = (self.r2.get_json(pointer_key(seat)) or {}).get("version")
            return row

        self.store.seat_status = seat_status  # type: ignore[method-assign]

    def publish(self, version: str, sha: str) -> dict[str, Any]:
        manifest = {"version": version, "source_sha": sha, "files": {}, "retired": [], "needs": self.needs}
        self.r2.put_bytes(skills_manifest_key(version), json.dumps(manifest).encode())
        rel = skills_rel(version, sha)
        self.store.insert_release(rel)
        return rel

    def defer(self, rel: dict[str, Any]) -> dict[str, str]:
        status = {s: self.store.seat_status(s) or {} for s in SEATS}
        return deferrals(rel, SEATS, status, lambda ref: IMAGE_SHA if ref else None, ancestor)

    def tick(self, minutes: float, candidates: list[dict[str, Any]]) -> int:
        self.clock.t = T0 + minutes * 60
        self.store.now_iso = datetime.fromtimestamp(self.clock.t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def do_seat_for(rel: dict[str, Any]):
            manifest, key, sha = self.roll.load_manifest(rel["skills_version"])
            return lambda seat, ring: self.roll.run(seat, ring, rel, manifest, key, sha, False)

        ticker = Ticker(self.store, self.logs.append, self.clock, defer=self.defer)  # type: ignore[arg-type]
        return ticker.run_promoting(RINGS, candidates, do_seat_for, "tick-holder")

    def pointer(self, seat: str) -> str | None:
        return (self.r2.get_json(pointer_key(seat)) or {}).get("version")


# -- the ordering rule (pure) -----------------------------------------------------------------


def test_a_skills_release_waits_for_the_seat_image_and_never_halts() -> None:
    status = {s: fresh_status() for s in SEATS}
    image = lambda ref: IMAGE_SHA  # noqa: E731 - a one-line stand-in for the image's commit
    # FALSIFIER: drop the at_or_after check and every seat is pending here.
    waiting = deferrals(skills_rel(NEW, NEW_SKILLS_SHA), SEATS, status, image, ancestor)
    assert sorted(waiting) == sorted(SEATS)
    assert "waiting for the image" in waiting["smd-staging"]
    plan = plan_tick(RINGS, {}, {}, lambda seats: [], datetime.fromtimestamp(T0, timezone.utc), waiting)
    assert plan.code == TICK_WAITING and plan.seats is None and "no seat may take" in plan.reason
    # Skills written at the image's own commit, or before it, go.
    assert deferrals(skills_rel(NEW, IMAGE_SHA), SEATS, status, image, ancestor) == {}
    assert deferrals(skills_rel(OLD, OLD_SKILLS_SHA), SEATS, status, image, ancestor) == {}


def test_a_seat_on_an_image_not_from_main_waits_rather_than_being_assumed_current() -> None:
    status = {s: fresh_status() for s in SEATS}
    # FALSIFIER: read an unknown image as current and the hotfix-lane seat is pending.
    waiting = deferrals(skills_rel(OLD, OLD_SKILLS_SHA), SEATS, status, lambda ref: None, ancestor)
    assert "not a recorded release whose commit this controller holds" in waiting["scott"]


def test_a_deferred_seat_does_not_stop_its_ring_once_another_seat_there_is_done() -> None:
    now = datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    latest = {s: {"state": "done", "finished_at": "2026-10-28T00:00:00Z"} for s in ("smd-staging", "pilot-smokeball")}
    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now, {"scott": "waiting for the image"})
    assert plan.code == TICK_PROGRESS and plan.seats == ["brokery"]  # scott is not rolled
    latest["brokery"] = {"state": "done", "finished_at": "2026-10-28T00:00:00Z"}
    latest["ashton-price"] = {"state": "done", "finished_at": "2026-10-28T01:00:00Z"}
    plan = plan_tick(RINGS, latest, {}, lambda seats: [], now, {"scott": "waiting for the image"})
    assert plan.code == TICK_WAITING and "scott: waiting for the image" in plan.reason


def test_combine_reports_a_halt_first_then_a_standing_halt_then_progress() -> None:
    assert combine(TICK_WAITING, TICK_HALT) == TICK_HALT
    assert combine(TICK_PROGRESS, TICK_HALTED_STANDING) == TICK_HALTED_STANDING
    assert combine(TICK_WAITING, TICK_PROGRESS) == TICK_PROGRESS
    assert combine(TICK_WAITING, TICK_WAITING) == TICK_WAITING


# -- the tick, against fakes -------------------------------------------------------------------


def test_a_skills_release_walks_the_rings_one_bake_at_a_time_and_completes() -> None:
    w = World()
    rel = w.publish(OLD, OLD_SKILLS_SHA)
    assert w.tick(0, [rel]) == TICK_PROGRESS
    assert [w.pointer(s) for s in SEATS] == [OLD, None, None, None, None]
    # FALSIFIER: skip the bake for skills and the pilot is pointed here.
    assert w.tick(10, [rel]) == TICK_WAITING
    assert w.pointer("pilot-smokeball") is None
    for minute in (31, 62, 93):
        assert w.tick(minute, [rel]) == TICK_PROGRESS
    assert all(w.pointer(s) == OLD for s in SEATS)
    assert w.store.completed == [rel["release_id"]]
    assert w.tick(200, [rel]) == TICK_WAITING


def test_a_skills_release_follows_the_image_and_goes_once_the_image_has_its_commit() -> None:
    w = World()
    rel = w.publish(NEW, NEW_SKILLS_SHA)
    assert w.tick(0, [rel]) == TICK_WAITING
    assert w.r2.writes.count(pointer_key("smd-staging")) == 0  # nothing written, nothing halted
    assert any("waiting for the image" in line for line in w.logs)
    # The image release at the skills' commit reaches staging: staging takes the skills next tick.
    newer_image = release(release_id="img-202610280300-33333333", image_digest="sha256:" + "c" * 64,
                          ss_console_sha=NEW_SKILLS_SHA)  # fmt: skip
    w.store.insert_release(newer_image)
    w.r2.put_json(image_key(newer_image["image_digest"], "image-capabilities.json"), CAPS)
    w.store.status["smd-staging"] = fresh_status(image=f"{newer_image['image_ref']}@{newer_image['image_digest']}")
    shas = {f"{newer_image['image_ref']}@{newer_image['image_digest']}": NEW_SKILLS_SHA}

    def defer(rel: dict[str, Any]) -> dict[str, str]:
        status = {s: w.store.seat_status(s) or {} for s in SEATS}
        return deferrals(rel, SEATS, status, lambda ref: shas.get(ref or "", IMAGE_SHA), ancestor)

    w.defer = defer  # type: ignore[method-assign]
    assert w.tick(15, [rel]) == TICK_PROGRESS
    assert w.pointer("smd-staging") == NEW
    # The pilot still runs the older image: past the bake, it waits for its image, it does not halt.
    assert w.tick(60, [rel]) == TICK_WAITING
    assert w.pointer("pilot-smokeball") is None
    assert "failed" not in w.store.states().values()


def test_an_older_skills_release_never_overwrites_a_newer_one() -> None:
    w = World()
    older = w.publish(OLD, OLD_SKILLS_SHA)
    newer = w.publish(NEW, IMAGE_SHA)
    assert w.tick(0, [older]) == TICK_PROGRESS  # older on staging
    # The pilot was pointed at the newer release by hand (an urgent `--skills --only`).
    manifest, key, sha = w.roll.load_manifest(NEW)
    w.roll.run("pilot-smokeball", 1, newer, manifest, key, sha, False)
    assert w.pointer("pilot-smokeball") == NEW
    writes = w.r2.writes.count(pointer_key("pilot-smokeball"))
    assert w.tick(25, [newer, older]) == TICK_PROGRESS  # newer takes staging
    # Older baked on staging long ago and looks at ring 1, where the pilot is done on newer;
    # by minute 75 it has been eligible there for more than a bake, so the give-way rule
    # no longer holds it. Two guards keep the pilot on newer: overlay_newer (it is done on a
    # newer release) and the newer-version deferral (its heartbeat reports newer skills).
    # FALSIFIER: drop both and older repoints the pilot backwards (each alone is enough).
    for minute in (40, 75):
        w.tick(minute, [newer, older])
    assert w.pointer("pilot-smokeball") == NEW
    assert w.r2.writes.count(pointer_key("pilot-smokeball")) == writes


def test_a_seat_reporting_newer_skills_than_any_release_weighed_is_never_moved_back() -> None:
    w = World()
    older = w.publish(OLD, OLD_SKILLS_SHA)
    w.r2.put_json(pointer_key("smd-staging"), {"version": NEW, "manifest_key": "k", "manifest_sha256": "0" * 64})
    writes = len(w.r2.writes)
    # FALSIFIER: drop the newer-version deferral and staging is repointed to OLD.
    assert w.tick(0, [older]) == TICK_WAITING
    assert w.pointer("smd-staging") == NEW and len(w.r2.writes) == writes
    assert any("already runs newer skills" in line for line in w.logs)


def test_a_skills_capability_refusal_is_a_standing_halt() -> None:
    w = World()
    w.needs = {"smd-note": {"tools": ["a_tool_no_image_has"]}}
    rel = w.publish(OLD, OLD_SKILLS_SHA)
    assert w.tick(0, [rel]) == TICK_HALT
    assert w.store.states()["smd-staging"] == "failed" and w.pointer("smd-staging") is None
    # FALSIFIER: ignore halting rows and the next tick retries staging.
    assert w.tick(120, [rel]) == TICK_HALTED_STANDING
    assert w.pointer("pilot-smokeball") is None


def test_a_held_seat_takes_no_skills() -> None:
    w = World()
    w.store.hold("smd-staging", "drill", "test")
    rel = w.publish(OLD, OLD_SKILLS_SHA)
    assert w.tick(0, [rel]) == TICK_WAITING
    assert w.pointer("smd-staging") is None


# -- the CLI: `fleet release latest --tick` steps skills too ------------------------------------


class LinearMain:
    """git, as a clone of MAIN answers: a commit is present when it is on MAIN, and
    `merge-base --is-ancestor a b` is MAIN order (cli.MAIN_REF is MAIN's last commit)."""

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        assert argv[:2] == ["git", "-C"]
        if "cat-file" in argv:
            return Result(0 if argv[-1].split("^")[0] in MAIN else 128, "", "")
        older, newer = argv[-2], argv[-1]
        newer = MAIN[-1] if newer == cli.MAIN_REF else newer
        if older not in MAIN or newer not in MAIN:
            return Result(128, "", "")
        return Result(0 if ancestor(older, newer) else 1, "", "")


def _cli_world() -> tuple[World, cli.Ctx]:
    w = World()
    w.store.now_iso = "2026-10-28T00:00:00Z"  # a rollout finishes at the tick's own time (D1's NOW_SQL)
    ctx = cli.Ctx(cli.REPO, LinearMain(), w.store, w.r2, FakeFly(), w.clock.sleep, w.clock, w.logs.append)  # type: ignore[arg-type]
    ctx.rings = lambda: RINGS  # type: ignore[method-assign]
    return w, ctx


def test_a_latest_tick_points_ring_0_at_the_newest_skills_release() -> None:
    w, ctx = _cli_world()
    w.publish(OLD, OLD_SKILLS_SHA)
    w.store.releases["img-202610190000-11111111"]["completed_last_ring_at"] = "2026-10-27T00:00:00Z"
    # FALSIFIER: tick image releases only (the defect) and no pointer is ever written.
    assert cli.main(["release", "latest", "--tick"], ctx=ctx) == TICK_PROGRESS
    assert w.pointer("smd-staging") == OLD and w.pointer("pilot-smokeball") is None


def test_an_image_step_refusal_does_not_starve_the_skills_step() -> None:
    w, ctx = _cli_world()
    w.publish(OLD, OLD_SKILLS_SHA)
    # The newest image carries no self-test: the image step refuses (exit 4)...
    assert cli.main(["release", "latest", "--tick"], ctx=ctx) == 4
    # ...and the skills step ran anyway. FALSIFIER: run skills only after a clean image step.
    assert w.pointer("smd-staging") == OLD


def test_a_skills_release_not_published_from_main_is_never_weighed() -> None:
    w, ctx = _cli_world()
    w.publish(OLD, "9" * 40)  # a commit the clone has never seen
    w.store.releases["img-202610190000-11111111"]["completed_last_ring_at"] = "2026-10-27T00:00:00Z"
    assert cli.main(["release", "latest", "--tick"], ctx=ctx) == TICK_WAITING
    assert w.pointer("smd-staging") is None


def test_tick_still_refuses_skills_flag_force_and_lane_acceptance() -> None:
    w, ctx = _cli_world()
    assert cli.main(["release", "--skills", OLD, "--tick"], ctx=ctx) == 2
    assert cli.main(["release", "latest", "--tick", "--force-mid-job"], ctx=ctx) == 2
    assert cli.main(["release", "latest", "--tick", "--accept-lane-inputs"], ctx=ctx) == 2
    assert not any(k.endswith("skills-pointer.json") for k in w.r2.writes)
