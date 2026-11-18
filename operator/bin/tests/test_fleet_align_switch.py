"""A release run by hand moves a lane seat's jobs bundle with its image, or refuses (2026-11-17).

On 2026-11-17 a laptop ran `fleet release <img> --only <client seat>`. The
tick reads the jobs bundle switch from its environment, where release-tick.yml
puts the repository variable; the laptop had no such environment, so the
aligner (bundle_align.py) was silently off, the image booted on the older
bundle, the seat's self-test refused it, and the seat was rolled back and sat
un-updated for about an hour and forty minutes. align_switch.py now reads the
switch the tick reads, from the same repository variable. Driven here through
cli.main and the real ImageRollout on the repo's fakes, each test naming its
falsifier:

  switch absent from the environment, the repository variable true: aligned
  the variable unreadable or off, a lane seat: refused before any deploy
  --no-bundle-align: deployed, pointer untouched, the record says so
  a seat that authors no lane: released whatever the switch says
  the switch in the environment (CI's urgent path): no GitHub read
  the tick: unchanged, the environment alone decides
  a rollback the re-smoke could not verify by any route: UNVERIFIED, exit 1
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bin.lib.fleet import cli
from bin.lib.fleet.artifact_tick import BUNDLE_SWITCH, bundle_align_for
from bin.lib.fleet.bundle_align import BundleAlign
from bin.lib.fleet.r2 import bundle_pointer_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.rollout import OFF_MARK, UNVERIFIED_MARK
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.smoke import Check
from bin.tests.fleet_fakes import FakeFly, FakeR2, FakeStore
from bin.tests.test_fleet_bundle_align import FLOOR, OLD, SEAT, VERSIONS, _world
from bin.tests.test_fleet_bundles import LANE_YAML, NO_LANE_YAML, _repo_with

CORE = "scott"  # authors no job lane
DEPLOY = "deploy rel-11111111-202610190000"


class GhRunner:
    """The subprocess seam: `gh variable get` answers as scripted; anything else succeeds silently."""

    def __init__(self, answer: Result | Exception) -> None:
        self.answer = answer
        self.calls: list[list[str]] = []

    def run(self, argv: Any, **_kw: Any) -> Result:
        self.calls.append(list(argv))
        if list(argv[:3]) == ["gh", "variable", "get"]:
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        return Result(0, "", "")

    def gh_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:1] == ["gh"]]


RINGS = [Ring(0, [SEAT]), Ring(1, [CORE])]


def _hand_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gh: Result | Exception, *checks: Check):
    """cli.main on a repo where ap-staging authors a lane and scott does not, with the
    real ImageRollout of test_fleet_bundle_align (its deploy and pointer writes logged)."""
    monkeypatch.delenv(BUNDLE_SWITCH, raising=False)
    w, ev, r2 = _world(*checks)
    aligner = w.roll.align
    w.store.releases[w.rel["release_id"]] = w.rel
    asked: list[bool | None] = []

    def rollout(_ctx: Any, _holder: str, align_on: bool | None = None) -> Any:
        # What cli._image_rollout builds, by the decision it is handed: the aligner, or none.
        asked.append(align_on)
        w.roll.align = aligner if align_on else None
        return w.roll

    monkeypatch.setattr(cli, "_image_rollout", rollout)
    repo = _repo_with(tmp_path, {SEAT: LANE_YAML, CORE: NO_LANE_YAML})
    runner = GhRunner(gh)
    ctx = cli.Ctx(repo, runner, w.store, r2, w.fly, w.clock.sleep, w.clock, w.logs.append)  # type: ignore[arg-type]
    ctx.rings = lambda: RINGS  # type: ignore[method-assign]
    return w, ev, r2, ctx, runner, asked


def _release(ctx: cli.Ctx, w: Any, *extra: str) -> int:
    return cli.main(["release", w.rel["release_id"], "--verify", "bash", *extra], ctx=ctx)


# -- the switch, read as the tick reads it ---------------------------------------------------
def test_a_laptop_release_reads_the_repository_variable_and_aligns_the_lane_seat(tmp_path, monkeypatch) -> None:
    """FALSIFIER: read only the environment (the code before 2026-11-17) and the
    image deploys with the pointer still at the old bundle, the incident."""
    w, ev, r2, ctx, runner, asked = _hand_world(tmp_path, monkeypatch, Result(0, "true\n", ""))
    assert _release(ctx, w, "--only", SEAT) == 0
    assert asked == [True]
    assert ev.log == [f"put {VERSIONS[FLOOR]}", DEPLOY], "pointer written before the image boots"
    assert r2.get_json(bundle_pointer_key(SEAT, "jobs"))["version"] == VERSIONS[FLOOR]
    assert runner.gh_calls() == [["gh", "variable", "get", BUNDLE_SWITCH, "--repo", "venturecrane/ss-console"]]
    said = [m for m in w.logs if m.startswith("bundle alignment: on")]
    assert said and "repository variable" in said[0]
    # Said before the deploy: the line comes ahead of the seat's own pointer note.
    assert w.logs.index(said[0]) < next(i for i, m in enumerate(w.logs) if "bundle pointer ->" in m)


@pytest.mark.parametrize(
    "gh",
    [
        Result(1, "", "HTTP 401: Bad credentials (https://api.github.com/...)"),
        Result(0, "false\n", ""),
        ToolingError("gh: command not found"),
    ],
    ids=["unreadable", "off", "no-gh"],
)
def test_alignment_off_or_unreadable_refuses_a_lane_seat_before_any_deploy(tmp_path, monkeypatch, gh) -> None:
    """FALSIFIER: default to off and deploy (the incident), and the fake records a deploy."""
    w, ev, _r2, ctx, _runner, asked = _hand_world(tmp_path, monkeypatch, gh)
    assert _release(ctx, w, "--only", SEAT) == 4
    assert ev.log == [] and w.fly.deploys == [], "nothing deployed, no pointer written"
    assert w.store.rollouts == {} and asked == [], "refused before a rollout was even built"
    assert not w.fly.snapshots


def test_the_refusal_says_how_to_run_it_with_alignment_on(tmp_path, monkeypatch, capsys) -> None:
    w, _ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    assert _release(ctx, w, "--only", SEAT) == 4
    out = capsys.readouterr().out
    assert f"{BUNDLE_SWITCH}=true operator/bin/fleet release {w.rel['release_id']} --only <seat>" in out
    assert "--no-bundle-align" in out and SEAT in out


def test_no_bundle_align_deploys_with_the_pointer_untouched_and_says_so(tmp_path, monkeypatch) -> None:
    """FALSIFIER: drop the OFF_MARK prefix in ImageRollout._finish and the row reads like an aligned one."""
    w, ev, r2, ctx, _runner, asked = _hand_world(tmp_path, monkeypatch, Result(0, "false\n", ""))
    before = r2.get_json(bundle_pointer_key(SEAT, "jobs"))
    assert _release(ctx, w, "--only", SEAT, "--no-bundle-align") == 0
    assert asked == [False] and ev.log == [DEPLOY]
    assert r2.get_json(bundle_pointer_key(SEAT, "jobs")) == before
    (row,) = w.store.rollouts.values()
    assert row["detail"].startswith(OFF_MARK), row["detail"]
    assert any(m.startswith(f"WARNING {OFF_MARK}") for m in w.logs)
    assert any(m.startswith(f"{SEAT}: WARNING {OFF_MARK}") for m in w.logs), "per seat, before its deploy"


def test_a_seat_that_authors_no_lane_still_releases_with_the_switch_off(tmp_path, monkeypatch) -> None:
    """FALSIFIER: refuse whenever the switch is off, lane or not, and scott is refused."""
    w, ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    assert _release(ctx, w, "--only", CORE) == 0
    assert ev.log == ["deploy rel-11111111-202610190000"] and w.store.states() == {CORE: "done"}
    assert not w.store.rollouts[next(iter(w.store.rollouts))]["detail"].startswith(OFF_MARK)
    assert any("no seat in this run authors a job lane" in m for m in w.logs)


def test_a_full_hand_release_with_the_switch_off_is_refused_for_its_lane_seat(tmp_path, monkeypatch) -> None:
    w, ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(0, "false\n", ""))
    assert _release(ctx, w) == 4 and ev.log == []


def test_the_switch_in_the_environment_is_read_there_and_github_is_not_asked(tmp_path, monkeypatch) -> None:
    """CI's urgent one-seat path (release-tick.yml) passes the repository variable in the
    environment: it is read there, as before. FALSIFIER: always ask gh and CI needs a GitHub login."""
    w, ev, _r2, ctx, runner, asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "unused"))
    monkeypatch.setenv(BUNDLE_SWITCH, "true")
    assert _release(ctx, w, "--only", SEAT) == 0
    assert runner.gh_calls() == [] and asked == [True] and ev.log[0] == f"put {VERSIONS[FLOOR]}"
    monkeypatch.setenv(BUNDLE_SWITCH, "")  # the variable unset in CI: off, and the lane seat is refused
    ev.log.clear()
    assert _release(ctx, w, "--only", SEAT) == 4 and ev.log == []


# -- the tick: unchanged ----------------------------------------------------------------------
def test_the_tick_builds_its_rollout_from_the_environment_alone(monkeypatch) -> None:
    """The tick calls cli._image_rollout(ctx, holder), whose aligner bundle_align_for builds
    from the environment, never from GitHub. FALSIFIER: route the tick through
    align_switch and it needs a GitHub login the runner does not have."""
    import argparse

    w, _ev, _r2 = _world()
    w.store.releases[w.rel["release_id"]] = w.rel
    calls: list[tuple[Any, ...]] = []

    def rollout(*args: Any, **kw: Any) -> Any:
        calls.append(args + tuple(kw.values()))
        return w.roll

    monkeypatch.setattr(cli, "_image_rollout", rollout)
    monkeypatch.setattr(cli, "_image_has_selftest", lambda _ctx, _rel: True)

    class _Ctx:
        store, r2, clock, logs = w.store, w.r2, w.clock, w.logs

        def log(self, msg: str) -> None:
            self.logs.append(msg)

        def rings(self) -> list[Ring]:
            return [Ring(0, [SEAT])]

    a = argparse.Namespace(release_id=w.rel["release_id"], verify="selftest", max_idle_wait=60.0, dry_run=False)
    cli._image_tick(_Ctx(), a)  # type: ignore[arg-type]
    assert len(calls) == 1 and len(calls[0]) == 2, calls


def test_the_aligner_follows_the_environment_for_the_tick_and_the_decision_for_a_hand_run(
    tmp_path, monkeypatch
) -> None:
    """bundle_align_for(ctx) is what the tick gets; bundle_align_for(ctx, decision) what a hand run gets.
    FALSIFIER: let ``enabled`` override nothing, or let it leak into the tick's call."""
    repo = _repo_with(tmp_path, {SEAT: LANE_YAML, CORE: NO_LANE_YAML})
    ctx = cli.Ctx(repo, GhRunner(Result(1, "", "")), FakeStore(), FakeR2(), FakeFly(), log=lambda m: None)  # type: ignore[arg-type]
    ctx.rings = lambda: RINGS  # type: ignore[method-assign]
    monkeypatch.delenv(BUNDLE_SWITCH, raising=False)
    assert bundle_align_for(ctx) is None
    assert isinstance(bundle_align_for(ctx, True), BundleAlign) and bundle_align_for(ctx, False) is None
    monkeypatch.setenv(BUNDLE_SWITCH, "true")
    assert isinstance(bundle_align_for(ctx), BundleAlign)
    assert bundle_align_for(ctx, False) is None
    for off in ("false", "TRUE", ""):
        monkeypatch.setenv(BUNDLE_SWITCH, off)
        assert bundle_align_for(ctx) is None
    assert ctx.runner.gh_calls() == []  # type: ignore[attr-defined]


def test_tick_refuses_no_bundle_align() -> None:
    assert cli.main(["release", "latest", "--tick", "--no-bundle-align"], ctx=_bare_ctx()) == 2


def _bare_ctx() -> cli.Ctx:
    return cli.Ctx(
        Path("/nonexistent"), GhRunner(Result(1, "", "")), FakeStore(), FakeR2(), FakeFly(), log=lambda m: None
    )  # type: ignore[arg-type]


# -- a rollback whose health no route could verify ------------------------------------------
def test_a_rollback_the_re_smoke_could_not_verify_says_unverified_and_exits_1(tmp_path, monkeypatch) -> None:
    """The boot smoke tried ssh, then `fly machine exec` (tests/boot-smoke-retry.test.ts),
    and got no verdict either way: a tooling_error re-smoke. FALSIFIER: drop the
    UNVERIFIED branch in ImageRollout._rollback and the row reads as a routine rollback."""
    no_verdict = Check("tooling_error", "84 check(s) got no verdict over ssh")
    w, ev, _r2, ctx, _runner, _asked = _hand_world(
        tmp_path, monkeypatch, Result(0, "true\n", ""), Check("fail", "FAILED: jobs-bundle-built-for-image"), no_verdict
    )
    assert _release(ctx, w, "--only", SEAT) == 1
    (row,) = w.store.rollouts.values()
    assert row["state"] == "rolled_back"
    assert row["detail"].startswith(UNVERIFIED_MARK), row["detail"]
    assert "re-smoke tooling_error" in row["detail"]
    assert ev.log[-1] == "deploy deployment-OLD"


def test_a_rollback_the_re_smoke_verified_does_not_say_unverified(tmp_path, monkeypatch) -> None:
    for verdict in (Check("pass", "re-smoke green"), Check("fail", "FAILED: x - command failed")):
        w, _ev, _r2, ctx, _runner, _asked = _hand_world(
            tmp_path / verdict.cls, monkeypatch, Result(0, "true\n", ""), Check("fail", "FAILED: y"), verdict
        )
        assert _release(ctx, w, "--only", SEAT) == 1
        (row,) = w.store.rollouts.values()
        assert row["state"] == "rolled_back" and UNVERIFIED_MARK not in row["detail"]


def test_old_is_the_seats_bundle_before_the_release() -> None:
    # The fixture these tests stand on: the seat's pointer names OLD before any run.
    w, _ev, r2 = _world()
    assert r2.get_json(bundle_pointer_key(SEAT, "jobs"))["version"] == VERSIONS[OLD]
