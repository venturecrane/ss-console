"""The release controller (ADR 0096): ticks, the bake, per-app Fly tokens, verify rows.

Each test names its falsifier: the one change to the code that turns it red.
"""

from __future__ import annotations

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


def test_a_halt_stands_until_a_person_acts(tmp_path) -> None:
    w = _tick_world(tmp_path)
    for seat in w.store.status:
        w.store.status[seat] = {**fresh_status(), "selftest_json": selftest_json(failed_count=1, failed=["x"])}
    assert _tick(w, 0) == TICK_HALT
    assert w.store.states()["smd-staging"] == "rolled_back"
    # FALSIFIER: ignore halting rows and the next tick retries the seat.
    assert _tick(w, 120) == TICK_HALTED_STANDING
    assert len(w.fly.deploys) == 2  # the release deploy and its rollback, nothing since


def test_a_selftest_timeout_in_a_tick_halts_without_any_boot_smoke(tmp_path) -> None:
    w = _tick_world(tmp_path)
    for seat in w.store.status:
        w.store.status[seat] = fresh_status()  # no self-test row ever arrives
    assert _tick(w, 0) == TICK_HALT
    assert w.store.states()["smd-staging"] == "tooling_error"
    assert w.smoke.calls == []  # FALSIFIER: fall back to bash and the boot smoke is called


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


def test_cli_tick_refuses_force_and_lane_acceptance() -> None:
    w = World()
    ctx = cli.Ctx(cli.REPO, None, w.store, w.r2, w.fly, log=lambda m: None)  # type: ignore[arg-type]
    rid = w.rel["release_id"]
    assert cli.main(["release", rid, "--tick", "--force-mid-job"], ctx=ctx) == 2
    assert cli.main(["release", rid, "--tick", "--accept-lane-inputs"], ctx=ctx) == 2
    assert cli.main(["release", rid, "--tick", "--only", "scott"], ctx=ctx) == 2
    assert w.fly.deploys == []


class AncestorRunner:
    """Answers `git merge-base --is-ancestor <sha> HEAD`: 0 for shas on main, 1 otherwise."""

    def __init__(self, on_main: set[str]) -> None:
        self.on_main = on_main

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        assert argv[:2] == ["git", "-C"] and "--is-ancestor" in argv
        return Result(0 if argv[-2] in self.on_main else 1, "", "")


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
    ctx.runner = AncestorRunner(set())  # type: ignore[assignment]
    with pytest.raises(cli.Refusal, match="built from main"):
        cli._image_release(ctx, "latest")


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
    fly.build_push("/tmp/fly.toml", "rel-x", "1" * 40)
    assert [env for _, env in runner.calls] == [{"FLY_API_TOKEN": REG_TOK}] * 2
    extractor = ImageExtractor(RecordingRunner(out="tok"), tokens=FlyTokens(CI_ENV))  # type: ignore[arg-type]
    extractor._auth()
    assert extractor.runner.calls[-1][1] == {"FLY_API_TOKEN": REG_TOK}  # type: ignore[attr-defined]


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
