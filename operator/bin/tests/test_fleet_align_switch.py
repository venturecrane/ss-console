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
  the switch cannot be read, a lane seat: refused before any deploy
  the switch readably off, on all three paths (laptop, CI's urgent run with the
    variable unset, the tick): deployed as the tick deploys, pointer untouched,
    the run and the row say "bundle alignment: OFF (switch off)"
  --no-bundle-align on an unreadable switch: deployed, marked the same way
  a seat that authors no lane: released whatever the switch says
  the switch in the environment: no GitHub read
  the tick: the environment alone decides
  a rollback the re-smoke could not verify by any route: UNVERIFIED, exit 1
  a deploy that would not come up: the row names the marker and Fly's lines
    around it, tail-bounded and redacted; which deploys are seat failures is unchanged
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
from bin.lib.fleet.align_switch import FORCED_OFF, SWITCH_OFF
from bin.lib.fleet.r2 import image_key
from bin.lib.fleet.rollout import OFF_MARK, UNVERIFIED_MARK, off_note
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.smoke import Check
from bin.tests.fleet_fakes import DIGEST, FakeFly, FakeR2, FakeStore
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
    [Result(1, "", "HTTP 401: Bad credentials (https://api.github.com/...)"), ToolingError("gh: command not found")],
    ids=["unreadable", "no-gh"],
)
def test_a_switch_that_cannot_be_read_refuses_a_lane_seat_before_any_deploy(tmp_path, monkeypatch, gh) -> None:
    """FALSIFIER: read "could not tell" as "off" and deploy (the incident), and the fake records a deploy."""
    w, ev, _r2, ctx, _runner, asked = _hand_world(tmp_path, monkeypatch, gh)
    assert _release(ctx, w, "--only", SEAT) == 4
    assert ev.log == [] and w.fly.deploys == [], "nothing deployed, no pointer written"
    assert w.store.rollouts == {} and asked == [], "refused before a rollout was even built"
    assert not w.fly.snapshots


def test_the_refusal_says_how_to_run_it_with_the_switch_read(tmp_path, monkeypatch, capsys) -> None:
    w, _ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    assert _release(ctx, w, "--only", SEAT) == 4
    out = capsys.readouterr().out
    assert f"{BUNDLE_SWITCH}=<true|false> operator/bin/fleet release {w.rel['release_id']} --only <seat>" in out
    assert "could not be read" in out and "--no-bundle-align" in out and SEAT in out


def _assert_released_off(w: Any, ev: Any, r2: Any, why: str, before: Any) -> None:
    """Deployed as the tick deploys with the switch off: the image, the pointer untouched, said loudly."""
    assert ev.log == [DEPLOY] and w.store.states() == {SEAT: "done"}
    assert r2.get_json(bundle_pointer_key(SEAT, "jobs")) == before
    (row,) = w.store.rollouts.values()
    assert row["detail"].startswith(off_note(why)), row["detail"]
    assert any(off_note(why) in m and m.startswith("WARNING") for m in w.logs), w.logs
    assert any(m == f"{SEAT}: WARNING {off_note(why)}" for m in w.logs), "per seat, before its deploy"


def test_a_switch_readably_off_on_a_laptop_releases_with_the_pointer_untouched_and_says_so(
    tmp_path, monkeypatch
) -> None:
    """Off is a decision someone made: the run does what the tick does. FALSIFIER:
    refuse on a readable off, and this exits 4 with nothing deployed."""
    for gh in (Result(0, "false\n", ""), Result(1, "", "variable BUNDLE_RELEASE_ENABLED was not found")):
        w, ev, r2, ctx, _runner, asked = _hand_world(tmp_path / str(gh.rc), monkeypatch, gh)
        before = r2.get_json(bundle_pointer_key(SEAT, "jobs"))
        assert _release(ctx, w, "--only", SEAT) == 0
        assert asked == [False]
        _assert_released_off(w, ev, r2, SWITCH_OFF, before)


def test_the_urgent_ci_release_with_the_variable_unset_behaves_as_main_did(tmp_path, monkeypatch) -> None:
    """release-tick.yml's urgent one-seat image run, args as the workflow passes them,
    with BUNDLE_RELEASE_ENABLED arriving as "" (the repository variable unset): the image
    deploys with the pointer untouched, as on main, and GitHub is not asked. FALSIFIER:
    treat "" as unreadable and the urgent release to a lane seat becomes impossible."""
    w, ev, r2, ctx, runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "unused"))
    r2.put_json(image_key(DIGEST, "image-capabilities.json"), {"schema_version": 1, "selftest": True})
    monkeypatch.setenv(BUNDLE_SWITCH, "")
    before = r2.get_json(bundle_pointer_key(SEAT, "jobs"))
    args = ["release", w.rel["release_id"], "--only", SEAT, "--verify", "selftest", "--no-ssh-fallback"]
    assert cli.main([*args, "--max-idle-wait", "1800"], ctx=ctx) == 0
    assert runner.gh_calls() == []
    _assert_released_off(w, ev, r2, SWITCH_OFF, before)


def test_no_bundle_align_deploys_an_unreadable_switch_anyway_and_says_so(tmp_path, monkeypatch) -> None:
    """FALSIFIER: drop the off_note prefix in ImageRollout._finish and the row reads like an aligned one."""
    w, ev, r2, ctx, _runner, asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    before = r2.get_json(bundle_pointer_key(SEAT, "jobs"))
    assert _release(ctx, w, "--only", SEAT, "--no-bundle-align") == 0
    assert asked == [False]
    _assert_released_off(w, ev, r2, FORCED_OFF, before)


def test_a_seat_that_authors_no_lane_still_releases_with_the_switch_unreadable(tmp_path, monkeypatch) -> None:
    """FALSIFIER: refuse whenever the switch cannot be read, lane or not, and scott is refused."""
    w, ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    assert _release(ctx, w, "--only", CORE) == 0
    assert ev.log == [DEPLOY] and w.store.states() == {CORE: "done"}
    assert not w.store.rollouts[next(iter(w.store.rollouts))]["detail"].startswith(OFF_MARK)
    assert any("no seat in this run authors a job lane" in m for m in w.logs)


def test_a_full_hand_release_with_the_switch_unreadable_is_refused_for_its_lane_seat(tmp_path, monkeypatch) -> None:
    w, ev, _r2, ctx, _runner, _asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "HTTP 401"))
    assert _release(ctx, w) == 4 and ev.log == []


def test_the_switch_in_the_environment_is_read_there_and_github_is_not_asked(tmp_path, monkeypatch) -> None:
    """CI passes the repository variable in the environment: it is read there, as before.
    FALSIFIER: always ask gh and CI needs a GitHub login."""
    w, ev, _r2, ctx, runner, asked = _hand_world(tmp_path, monkeypatch, Result(1, "", "unused"))
    monkeypatch.setenv(BUNDLE_SWITCH, "true")
    assert _release(ctx, w, "--only", SEAT) == 0
    assert runner.gh_calls() == [] and asked == [True] and ev.log[0] == f"put {VERSIONS[FLOOR]}"


# -- the tick ----------------------------------------------------------------------------------
class _TickCtx:
    def __init__(self, w: Any, repo: Path | None = None) -> None:
        self.store, self.r2, self.clock, self.logs, self.repo = w.store, w.r2, w.clock, w.logs, repo

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def rings(self) -> list[Ring]:
        return [Ring(0, [SEAT])]


def _tick(ctx: Any, w: Any) -> int:
    import argparse

    a = argparse.Namespace(release_id=w.rel["release_id"], verify="selftest", max_idle_wait=60.0, dry_run=False)
    return cli._image_tick(ctx, a)  # type: ignore[arg-type]


def test_the_tick_builds_its_rollout_from_the_environment_alone(monkeypatch) -> None:
    """The tick calls cli._image_rollout(ctx, holder), whose aligner bundle_align_for builds
    from the environment, never from GitHub. FALSIFIER: route the tick through
    align_switch.decide and it needs a GitHub login the runner does not have."""
    w, _ev, _r2 = _world()
    w.store.releases[w.rel["release_id"]] = w.rel
    calls: list[tuple[Any, ...]] = []

    def rollout(*args: Any, **kw: Any) -> Any:
        calls.append(args + tuple(kw.values()))
        return w.roll

    monkeypatch.setattr(cli, "_image_rollout", rollout)
    monkeypatch.setattr(cli, "_image_has_selftest", lambda _ctx, _rel: True)
    _tick(_TickCtx(w), w)
    assert len(calls) == 1 and len(calls[0]) == 2, calls


def test_the_tick_with_its_switch_off_releases_a_lane_seat_unaligned_and_says_so(tmp_path, monkeypatch) -> None:
    """The third path: the tick, its environment saying off. Deployed with the pointer
    untouched, as always, now logged and recorded. FALSIFIER: drop tick_off from
    cli._image_tick and neither the log line nor the row's prefix appears."""
    w, ev, r2 = _world()
    w.store.releases[w.rel["release_id"]] = w.rel
    w.roll.align = None  # what bundle_align_for(ctx) builds with the switch off
    before = r2.get_json(bundle_pointer_key(SEAT, "jobs"))
    monkeypatch.setattr(cli, "_image_rollout", lambda _ctx, _holder: w.roll)
    monkeypatch.setattr(cli, "_image_has_selftest", lambda _ctx, _rel: True)
    _tick(_TickCtx(w, _repo_with(tmp_path, {SEAT: LANE_YAML})), w)
    assert ev.log == [DEPLOY] and r2.get_json(bundle_pointer_key(SEAT, "jobs")) == before
    (row,) = w.store.rollouts.values()
    assert row["detail"].startswith(off_note(SWITCH_OFF)), row["detail"]
    assert any(m.startswith(f"tick: WARNING {off_note(SWITCH_OFF)}") for m in w.logs), w.logs


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


# -- a deploy that would not come up keeps its evidence (2026-11-17) --------------------------
# A client seat's rollout that day failed with "fly deploy reported the new release
# would not come up", and the row kept nothing Fly printed: the cause is still
# unknown. The row now names the marker that classed it a seat failure and the
# lines around it, redacted and bounded.
def _deploy_fail_world(stderr: str):
    from bin.tests.test_fleet_release import World

    w = World(Check("pass", "re-smoke green"))
    w.fly.deploy_results = [Result(1, "", stderr)]
    w.release_all(only="ap-staging")
    (row,) = w.store.rollouts.values()
    return w, row


FLY_SAID = (
    "==> Building image\n"
    "--> Pushing image done\n"
    "Updating existing machines in 'hermes-ap-staging'\n"
    "Machine 6e8206d0f36d08 [app] update failed: timeout reached waiting for health checks to pass\n"
    "  check[servicecheck-00-http-8080] failed\n"
    "Error: failed to update VM 6e8206d0f36d08\n"
)


def test_a_deploy_that_would_not_come_up_records_the_marker_and_what_fly_said() -> None:
    """FALSIFIER: return "" for a 'fail' in _deploy_saying (the code before) and the row names neither."""
    w, row = _deploy_fail_world(FLY_SAID)
    assert row["state"] == "rolled_back", row["detail"]
    assert 'marker "health checks"' in row["detail"]
    assert "waiting for health checks to pass" in row["detail"] and "failed to update VM" in row["detail"]
    # Rollback unchanged: the from-image was redeployed and re-smoked.
    assert [img for _, img, _ in w.fly.deploys][-1].endswith("deployment-OLD")


def test_the_excerpt_is_bounded_to_its_tail() -> None:
    """FALSIFIER: drop the [-DEPLOY_EXCERPT_CHARS:] bound and the whole 5,000-character line lands in the row."""
    from bin.lib.fleet.fly import DEPLOY_EXCERPT_CHARS, deploy_failure_evidence

    noise = "x" * 5000
    res = Result(1, "", f"Machine failed to start: {noise} END-OF-LINE\n")
    said = deploy_failure_evidence(res)
    assert said.startswith('marker "failed to start"; rc=1: ')
    assert len(said) <= len('marker "failed to start"; rc=1: ') + DEPLOY_EXCERPT_CHARS
    assert said.endswith("END-OF-LINE")
    _w, row = _deploy_fail_world(f"Machine failed to start: {noise} END-OF-LINE\n")
    assert noise not in row["detail"] and "END-OF-LINE" in row["detail"]


def test_a_line_that_looks_like_a_secret_is_never_stored() -> None:
    """FALSIFIER: show the window's lines unredacted and the values land in the row."""
    stderr = (
        "ANTHROPIC_API_KEY=sk-ant-canary-0001\n"
        "  SMOKEBALL_REFRESH=canary-0002\n"
        "Machine 6e8206d0f36d08 has crashed, restarting\n"
        "authorization: Bearer canary-0003\n"
    )
    _w, row = _deploy_fail_world(stderr)
    assert 'marker "crash"' in row["detail"] and "has crashed" in row["detail"]
    for canary in ("canary-0001", "canary-0002", "canary-0003"):
        assert canary not in row["detail"]
    assert "[line withheld]" in row["detail"]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("timeout waiting for health checks", "fail"),
        ("process FAILED TO START", "fail"),
        ("smoke checks for 6e82 failed", "fail"),
        ("main child exited with non-zero status", "fail"),
        ("Machine failed", "fail"),
        ("app crashed", "fail"),
        ("Error: something else entirely", "tooling_error"),
        ("connection reset by peer while waiting for health checks", "tooling_error"),
    ],
)
def test_which_deploys_are_seat_failures_is_unchanged(text: str, want: str) -> None:
    from bin.lib.fleet.fly import DEPLOY_SEAT_FAILURE_MARKERS, classify_deploy
    from bin.lib.fleet.runner import looks_like_transport_failure

    res = Result(1, "", text)
    old = (
        "tooling_error"
        if looks_like_transport_failure(res)
        else ("fail" if any(m in res.text.lower() for m in DEPLOY_SEAT_FAILURE_MARKERS) else "tooling_error")
    )
    assert classify_deploy(res) == old == want
