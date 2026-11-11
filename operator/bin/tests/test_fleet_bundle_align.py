"""The image step points a lane seat at its jobs bundle BEFORE the image boots (bundle_align.py).

2026-11-07: a client seat ran rel-b4d97f03 with the previous jobs bundle from
08:33Z to 08:43Z, because the image step rebooted it before the bundle step
wrote its pointer, and a seat reads the pointer only at boot. Driven here on
fakes, each test naming its falsifier:

  the pointer is written and read back before ``fly deploy``
  a pointer that cannot be written stops that seat's deploy (tooling_error)
  a rollback restores the previous pointer before it redeploys the from-image
  which bundle: the newest qualifying one; one that lags because no bundle path
    changed is chosen; one built for a newer image is not; none published defers
  a seat that authors no lane is never touched
"""

from __future__ import annotations

import json
from typing import Any

from bin.lib.fleet.bundle_align import BundleAlign
from bin.lib.fleet.r2 import bundle_manifest_key, bundle_pointer_key, bundle_tarball_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.runner import ToolingError
from bin.lib.fleet.smoke import Check
from bin.tests.fleet_fakes import FakeFly, FakeR2
from bin.tests.test_fleet_release import World

IMAGE = "1" * 40
OLD = "a" * 40  # a bundle change before the image's floor
FLOOR = "f" * 40  # the image's last bundle change; the image commit itself changed none
AFTER = "e" * 40  # a runner-only merge after the image (needs no newer image)
NEEDS_NEWER = "d" * 40  # a bundle after a later image-path change
#: Main's one line, oldest first; ``at_or_after`` reads positions off it.
LINE = [OLD, FLOOR, IMAGE, AFTER, "c" * 40, NEEDS_NEWER]
BASES = {OLD: OLD, FLOOR: OLD, AFTER: IMAGE, NEEDS_NEWER: "c" * 40}
VERSIONS = {OLD: "202611060000-aaaaaaaa", FLOOR: "202611070000-ffffffff", AFTER: "202611070100-eeeeeeee",
            NEEDS_NEWER: "202611070200-dddddddd"}  # fmt: skip
SEAT = "ap-staging"


def _rel(sha: str) -> dict[str, Any]:
    v = VERSIONS[sha]
    return {"release_id": f"bnd-{v}", "kind": "bundle", "artifact_name": "jobs", "artifact_version": v,
            "ss_console_sha": sha}  # fmt: skip


def _publish(r2: FakeR2, sha: str) -> None:
    v = VERSIONS[sha]
    r2.put_json(bundle_manifest_key("jobs", v), {"name": "jobs", "version": v, "sha256": "0" * 64})
    r2.put_bytes(bundle_tarball_key("jobs", v), b"tar")


def _align(r2: FakeR2, published: list[str], authoring: set[str] | None = None) -> BundleAlign:
    for sha in published:
        _publish(r2, sha)
    newest_first = [_rel(s) for s in sorted(published, key=LINE.index, reverse=True)]
    return BundleAlign(
        r2, lambda: newest_first, {SEAT} if authoring is None else authoring, lambda sha: FLOOR if sha == IMAGE else None,
        lambda a, b: LINE.index(a) <= LINE.index(b), BASES.get,
    )  # fmt: skip


# -- which bundle ----------------------------------------------------------------------
def test_the_newest_bundle_the_image_qualifies_for_is_chosen() -> None:
    """FALSIFIER: drop the base(S) check and the bundle that needs a newer image is chosen."""
    rel, _why = _align(FakeR2(), [OLD, FLOOR, AFTER, NEEDS_NEWER]).choose(IMAGE)
    assert rel and rel["ss_console_sha"] == AFTER


def test_a_bundle_that_lags_because_no_bundle_path_changed_is_chosen() -> None:
    """The image's commit changed no bundle path, so no bundle was built for it.

    FALSIFIER: require a bundle at the image's own commit and nothing qualifies.
    """
    rel, _why = _align(FakeR2(), [OLD, FLOOR]).choose(IMAGE)
    assert rel and rel["ss_console_sha"] == FLOOR


def test_no_bundle_at_or_after_the_floor_defers_the_lane_seat_and_only_it() -> None:
    """The image published before its bundle: the seat waits instead of booting the old one.

    FALSIFIER: accept a bundle older than the floor and the seat deploys onto it.
    """
    align = _align(FakeR2(), [OLD, NEEDS_NEWER])
    assert align.choose(IMAGE)[0] is None
    deferred = align.deferrals({"ss_console_sha": IMAGE}, [SEAT, "scott"])
    assert list(deferred) == [SEAT] and FLOOR[:8] in deferred[SEAT]


# -- one seat, on the image rollout ----------------------------------------------------------
class _Events:
    def __init__(self) -> None:
        self.log: list[str] = []


class _R2(FakeR2):
    def __init__(self, ev: _Events) -> None:
        super().__init__()
        # lie: once a pointer was written, read it back as something else.
        self.ev, self.fail_put, self.lie, self.written = ev, False, False, False

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        if key.endswith("jobs-pointer.json"):
            if self.fail_put:
                raise ToolingError("R2 put refused")
            self.ev.log.append(f"put {json.loads(data).get('version')}")
            self.written = True
        super().put_bytes(key, data, content_type)

    def get_bytes(self, key: str) -> bytes | None:
        got = super().get_bytes(key)
        if key.endswith("jobs-pointer.json") and self.lie and self.written:
            return b'{"version": "something else"}'
        return got

    def delete(self, key: str) -> None:
        self.ev.log.append("delete pointer")
        super().delete(key)


class _Fly(FakeFly):
    def __init__(self, ev: _Events) -> None:
        super().__init__()
        self.ev = ev

    def deploy(self, app: str, image: str, config_path: str) -> Any:
        self.ev.log.append(f"deploy {image.rsplit(':', 1)[-1]}")
        return super().deploy(app, image, config_path)


def _world(*checks: Check, previous: str | None = OLD) -> tuple[World, _Events, _R2]:
    ev = _Events()
    w = World(*checks, rel={**World().rel, "ss_console_sha": IMAGE})
    r2 = _R2(ev)
    r2.objects.update(w.r2.objects)
    fly = _Fly(ev)
    w.r2, w.fly = r2, fly
    w.roll.fly = w.gate.fly = fly
    w.roll.preflight.r2, w.roll.preflight.fly = r2, fly  # type: ignore[assignment]
    if previous:
        _publish(r2, previous)
        r2.objects[bundle_pointer_key(SEAT, "jobs")] = json.dumps({"version": VERSIONS[previous]}).encode()
    w.roll.align = _align(r2, [OLD, FLOOR])
    ev.log.clear()
    return w, ev, r2


def _one(w: World):
    from bin.lib.fleet.rollout import ImagePlan

    return w.roll.run(SEAT, 0, ImagePlan(w.rel, "3" * 40))


def test_the_pointer_is_written_and_read_back_before_the_image_deploys() -> None:
    """FALSIFIER: move the pointer write after _deploy_saying (or drop it) and the order flips."""
    w, ev, r2 = _world()
    out = _one(w)
    assert out.state == "done", out.detail
    assert ev.log == [f"put {VERSIONS[FLOOR]}", "deploy rel-11111111-202610190000"]
    assert r2.get_json(bundle_pointer_key(SEAT, "jobs"))["version"] == VERSIONS[FLOOR]


def test_a_pointer_that_cannot_be_written_stops_that_seats_deploy() -> None:
    """FALSIFIER: swallow the write failure and deploy anyway, and a deploy is logged."""
    w, ev, r2 = _world()
    r2.fail_put = True
    out = _one(w)
    assert out.state == "tooling_error" and "not deployed" in out.detail
    assert not [e for e in ev.log if e.startswith("deploy")]
    assert not out.halts  # the seats beside it go on


def test_a_pointer_that_reads_back_wrong_stops_the_deploy_and_is_restored() -> None:
    w, ev, r2 = _world()
    r2.lie = True
    out = _one(w)
    assert out.state == "tooling_error" and "read back" in out.detail
    assert not [e for e in ev.log if e.startswith("deploy")]
    assert ev.log[-1] == f"put {VERSIONS[OLD]}"


def test_a_rollback_restores_the_previous_pointer_before_it_redeploys_the_from_image() -> None:
    """FALSIFIER: restore after the rollback deploy (or not at all) and the from-image boots the new bundle."""
    w, ev, r2 = _world(Check("fail", "FAILED: jobs-bundle-built-for-image"), Check("pass", "re-smoke green"))
    out = _one(w)
    assert out.state == "rolled_back", out.detail
    assert ev.log == [
        f"put {VERSIONS[FLOOR]}", "deploy rel-11111111-202610190000", f"put {VERSIONS[OLD]}", "deploy deployment-OLD",
    ]  # fmt: skip


def test_a_seat_with_no_previous_pointer_has_the_written_one_removed_on_rollback() -> None:
    w, ev, _r2 = _world(Check("fail", "FAILED: x"), Check("pass", "green"), previous=None)
    assert _one(w).state == "rolled_back"
    assert ev.log[2] == "delete pointer"


def test_no_published_bundle_stops_a_one_seat_release_before_the_deploy() -> None:
    w, ev, r2 = _world()
    w.roll.align = _align(r2, [OLD])
    out = _one(w)
    assert out.state == "tooling_error" and "nothing was deployed" in out.detail and ev.log == []


def test_a_seat_that_authors_no_lane_is_deployed_with_its_pointer_untouched() -> None:
    w, ev, r2 = _world()
    w.roll.align = _align(r2, [OLD, FLOOR], authoring=set())
    assert _one(w).state == "done"
    assert ev.log == ["deploy rel-11111111-202610190000"]


class _TickCtx:
    def __init__(self, w: World) -> None:
        self.store, self.r2, self.clock, self.logs = w.store, w.r2, w.clock, w.logs

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def rings(self) -> list[Ring]:
        return [Ring(0, [SEAT]), Ring(1, ["scott"])]


def _image_tick(monkeypatch: Any, published: list[str]) -> tuple[World, _Events, int]:
    import argparse

    from bin.lib.fleet import cli

    w, ev, r2 = _world()
    w.roll.align = _align(r2, published)
    w.store.releases[w.rel["release_id"]] = w.rel
    monkeypatch.setattr(cli, "_image_rollout", lambda _ctx, _holder: w.roll)
    monkeypatch.setattr(cli, "_image_has_selftest", lambda _ctx, _rel: True)
    a = argparse.Namespace(release_id=w.rel["release_id"], verify="selftest", max_idle_wait=60.0, dry_run=False)
    rc = cli._image_tick(_TickCtx(w), a)  # type: ignore[arg-type]
    return w, ev, rc


def test_the_image_tick_defers_a_lane_seat_whose_bundle_is_not_published(monkeypatch: Any) -> None:
    """Through cli._image_tick, so the defer is proven wired.

    FALSIFIER: drop defer= from the image Ticker and the tick reaches the seat
    (which then stops itself before the deploy, a tooling_error row it should never have).
    """
    w, ev, _rc = _image_tick(monkeypatch, [OLD])
    assert ev.log == [] and SEAT not in w.store.states()
    assert any(FLOOR[:8] in m for m in w.logs), w.logs


def test_the_image_tick_points_then_deploys_once_the_bundle_is_published(monkeypatch: Any) -> None:
    w, ev, _rc = _image_tick(monkeypatch, [OLD, FLOOR])
    assert ev.log[:2] == [f"put {VERSIONS[FLOOR]}", "deploy rel-11111111-202610190000"]
    assert w.store.states()[SEAT] == "done"


# -- the floor and the commit number (ordering.py) ----------------------------------------
def test_the_floor_is_asked_of_the_bundle_path_list_and_the_number_refuses_a_shallow_clone() -> None:
    """FALSIFIER: ask the image path list for the floor, or count in a shallow clone, and this fails."""
    from pathlib import Path

    import pytest

    from bin.lib.fleet import ordering
    from bin.lib.fleet.runner import Result
    from bin.tests.test_fleet_parts import ScriptRunner

    repo = Path(__file__).resolve().parents[3]
    runner = ScriptRunner(Result(0, "", ""), Result(0, FLOOR + "\n", ""))
    assert ordering.bundle_floor(runner, repo, IMAGE) == FLOOR  # type: ignore[arg-type]
    assert ":(glob)operator/runners/jobs/**" in runner.calls[1]
    assert ":(glob)operator/hermes-smd-overlay/**" not in runner.calls[1]
    assert ordering.commit_seq(ScriptRunner(Result(0, "false\n", ""), Result(0, "2680\n", "")), repo, IMAGE) == 2680  # type: ignore[arg-type]
    with pytest.raises(ToolingError, match="shallow"):
        ordering.commit_seq(ScriptRunner(Result(0, "true\n", "")), repo, IMAGE)  # type: ignore[arg-type]
