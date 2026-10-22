"""`fleet release`: the rollout's decisions, driven end to end against fakes.

Each test names the falsifier it carries: the behaviour the plan requires, and
the one change to the code that would turn the test red.

  stale heartbeat -> the busy gate refuses (seat skipped waiting_idle, never deployed)
  smoke tooling error -> NO rollback, rollout halts
  smoke fail -> rollback to the from-image with the from-toml, then halt
  lease held -> a second release refuses before touching anything
  held seat -> skipped_held, never deployed
  volume_forward_compatible=0 -> a failing seat halts WITHOUT rollback
  heartbeat reports another image -> fail -> rollback
"""

from __future__ import annotations

import pytest

from bin.lib.fleet import ids
from bin.lib.fleet.busy import BusyGate
from bin.lib.fleet.preflight import PreflightChecker
from bin.lib.fleet.r2 import image_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.rollout import ImagePlan, ImageRollout
from bin.lib.fleet.runner import Refusal, Result
from bin.lib.fleet.smoke import Check
from bin.lib.fleet.waves import Lease, Waves
from bin.tests.fleet_fakes import DIGEST, OLD_DIGEST, FakeFly, FakeR2, FakeSmoke, FakeStore, fresh_status, release

RENDERED = """app = "hermes-x"
kill_signal = "SIGTERM"
kill_timeout = 300
[env]
  R2_BUCKET_CONFIG = "smd-customer-config"
  R2_SKILL_BODIES_BUCKET = "ss-operator-x-skills"
  SMD_D1_AUDIT_BINDING = "/opt/data/audit/audit.db"
"""
REQUIRED = [
    "ANTHROPIC_API_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_BUCKET_CONFIG",
    "R2_SECRET_ACCESS_KEY",
    "R2_SKILL_BODIES_BUCKET",
    "SMD_D1_AUDIT_BINDING",
]
RINGS = [
    Ring(0, ["smd-staging"]),
    Ring(1, ["pilot-smokeball"]),
    Ring(2, ["scott", "brokery"]),
    Ring(3, ["ashton-price"], 1),
]


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class World:
    def __init__(self, *smoke_checks: Check, rel: dict | None = None) -> None:
        self.store = FakeStore()
        self.r2 = FakeR2()
        self.fly = FakeFly()
        self.smoke = FakeSmoke(*smoke_checks)
        self.clock = Clock()
        self.logs: list[str] = []
        self.rel = rel or release()
        self.store.insert_release(self.rel)
        self.r2.put_json(image_key(DIGEST, "required-env.json"), {"required": REQUIRED})
        for seat in ("smd-staging", "pilot-smokeball", "scott", "brokery", "ashton-price"):
            self.store.status[seat] = fresh_status()
        self.gate = BusyGate(self.fly, "tester", self.clock.sleep, self.clock)  # type: ignore[arg-type]
        self.roll = ImageRollout(
            self.store, self.fly, self.gate, PreflightChecker(self.fly, self.r2), self.smoke,  # type: ignore[arg-type]
            lambda seat: RENDERED, self.logs.append, self.clock.sleep, self.clock,
        )  # fmt: skip

    def release_all(self, only: str | None = None, parallel: int = 1, **plan_kw):
        plan = ImagePlan(self.rel, "3" * 40, **plan_kw)
        waves = Waves(self.store, self.logs.append)  # type: ignore[arg-type]
        lease = Lease(self.store, "tester", self.rel["release_id"])  # type: ignore[arg-type]
        with lease:
            return waves.run(
                RINGS,
                lambda s, r: self.roll.run(s, r, plan),
                release=self.rel,
                only=only,
                parallel=parallel,
                dry_run=False,
                lease=lease,
            )


def deployed_images(world: World) -> list[tuple[str, str]]:
    return [(app, image) for app, image, _ in world.fly.deploys]


def test_happy_path_every_ring_done_and_release_completes() -> None:
    w = World()
    result = w.release_all()
    assert set(w.store.states().values()) == {"done"}
    assert result.completed and not result.halted
    assert [a for a, _ in deployed_images(w)] == [
        "hermes-smd-staging", "hermes-pilot-smokeball", "hermes-scott", "hermes-brokery", "hermes-ashton-price",
    ]  # fmt: skip
    assert all(img == w.rel["image_ref"] for _, img in deployed_images(w))
    # Every seat snapshotted before its deploy, and the smoke got the release's Hermes pin.
    assert len(w.fly.snapshots) == 5
    assert all(sha == "3" * 40 for _, sha in w.smoke.calls)


def test_stale_heartbeat_gate_refuses_and_the_seat_is_never_deployed() -> None:
    w = World()
    stale = {
        "lanes": [{"file": "heartbeat.json", "age_s": 4000, "running": None, "wakes_pending": 0, "daemon_alive": True}],
        "children": [],
        "crons": [],
        "errors": [],
    }
    w.fly.probe_facts["smd-staging"] = [stale]
    result = w.release_all(only="smd-staging", max_idle_wait=120)
    assert w.store.states()["smd-staging"] == "waiting_idle"
    assert w.fly.deploys == []  # FALSIFIER: treat a stale heartbeat as idle and this deploys
    assert ("smd-staging", "set") in w.fly.quiesce_calls  # it quiesced while waiting
    assert ("smd-staging", "clear") in w.fly.quiesce_calls  # and removed its marker when it gave up
    assert not result.completed


def test_busy_then_idle_waits_then_deploys() -> None:
    w = World()
    busy = {"lanes": [], "children": [{"file": "child.pid", "pid": "77", "alive": True}], "crons": [], "errors": []}
    idle = {"lanes": [], "children": [], "crons": [], "errors": []}
    w.fly.probe_facts["smd-staging"] = [busy, busy, idle]
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "done"
    assert len(w.fly.deploys) == 1


def test_smoke_tooling_error_halts_without_rollback() -> None:
    w = World(Check("tooling_error", "3 checks got no verdict over ssh"))
    result = w.release_all()
    states = w.store.states()
    assert states["smd-staging"] == "tooling_error"
    # FALSIFIER: roll back on a tooling error and a second deploy (to the from-image) appears.
    assert deployed_images(w) == [("hermes-smd-staging", w.rel["image_ref"])]
    assert result.halted and not result.completed
    assert states["pilot-smokeball"] == "halted" and states["ashton-price"] == "halted"


def test_failing_smoke_rolls_back_to_from_image_and_halts() -> None:
    w = World(
        Check("pass", "green"),
        Check("fail", "FAILED: matter-mixing-fence — command failed"),
        Check("pass", "re-smoke green"),
    )
    result = w.release_all()
    states = w.store.states()
    assert states["smd-staging"] == "done"
    assert states["pilot-smokeball"] == "rolled_back"
    pilot = [d for d in w.fly.deploys if d[0] == "hermes-pilot-smokeball"]
    assert [img for _, img, _ in pilot] == [w.rel["image_ref"], "registry.fly.io/hermes-pilot-smokeball:deployment-OLD"]
    assert pilot[1][2] == 'app = "hermes-pilot-smokeball"\n# the deployed toml\n'  # rolled back with the from-toml
    assert result.halted
    assert states["scott"] == "halted" and states["ashton-price"] == "halted"
    assert w.store.completed == []


def test_not_volume_forward_compatible_halts_without_rollback() -> None:
    w = World(Check("fail", "FAILED: x — command failed"), rel=release(volume_forward_compatible=0))
    w.release_all()
    assert w.store.states()["smd-staging"] == "failed"
    assert len(w.fly.deploys) == 1  # no rollback deploy


def test_heartbeat_reporting_another_image_is_a_fail() -> None:
    w = World()
    w.store.status_script["smd-staging"] = [fresh_status(image="registry.fly.io/hermes-smd-staging:deployment-OLD")]
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "rolled_back"


def test_no_fresh_heartbeat_is_a_timeout_not_a_rollback() -> None:
    w = World()
    stale_row = dict(fresh_status(), last_heartbeat_ts="2000-01-01T00:00:00Z")
    w.store.status_script["smd-staging"] = [stale_row]
    w.release_all(only="smd-staging")
    row = next(iter(w.store.rollouts.values()))
    assert row["state"] == "tooling_error" and row["smoke_class"] == "timeout"
    assert len(w.fly.deploys) == 1


def test_held_seat_is_skipped_and_never_deployed() -> None:
    w = World()
    w.store.hold("pilot-smokeball", "demo for the firm today", "captain")
    w.release_all()
    states = w.store.states()
    assert states["pilot-smokeball"] == "skipped_held"
    assert "hermes-pilot-smokeball" not in [a for a, _ in deployed_images(w)]
    assert states["ashton-price"] == "done"


def test_lease_held_by_another_rollout_refuses() -> None:
    w = World()
    w.store.lease_row = {"lease_name": "rollout", "holder": "someone-else", "release_id": "img-x", "expires_at": "2099"}
    with pytest.raises(Refusal, match="someone-else"):
        w.release_all()
    assert w.fly.deploys == [] and w.store.rollouts == {}


def test_expired_lease_is_taken_over() -> None:
    w = World()
    w.store.lease_row = {"lease_name": "rollout", "holder": "dead-laptop", "release_id": "img-x", "expires_at": "2000"}
    w.store.lease_expired = True
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "done"
    assert w.store.lease_row is None  # released at the end


def test_preflight_missing_required_env_refuses_before_touching() -> None:
    w = World()
    w.fly.secrets["hermes-smd-staging"] = ({"R2_ACCESS_KEY_ID"}, set())
    result = w.release_all()
    assert w.store.states()["smd-staging"] == "failed"
    assert "ANTHROPIC_API_KEY" in next(iter(w.store.rollouts.values()))["detail"]
    assert w.fly.deploys == [] and w.fly.snapshots == []
    assert result.halted


def test_undeclared_staged_secret_refuses_declared_one_passes() -> None:
    w = World()
    w.fly.secrets["hermes-smd-staging"] = ({*REQUIRED}, {"NEW_KEY"})
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "failed"
    w2 = World(rel=release(declared_secrets='["NEW_KEY"]'))
    w2.fly.secrets["hermes-smd-staging"] = ({*REQUIRED}, {"NEW_KEY"})
    w2.release_all(only="smd-staging")
    assert w2.store.states()["smd-staging"] == "done"


def _lane_world() -> World:
    w = World()
    w.r2.put_json("vaults/smd-staging/medchron-firm.yaml", {})
    w.r2.put_json(image_key(DIGEST, "lane-inputs.json"), {"medchron": "new"})
    w.r2.put_json(image_key(OLD_DIGEST, "lane-inputs.json"), {"medchron": "old"})
    # The seat runs the OLD image until the deploy, then reports the target.
    w.store.status_script["smd-staging"] = [
        fresh_status(image=f"{ids.FLEET_REGISTRY}:old@{OLD_DIGEST}"),
        fresh_status(),
    ]
    return w


def test_lane_inputs_changed_refuses_unless_accepted() -> None:
    w = _lane_world()
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "failed"
    assert "lane medchron" in next(iter(w.store.rollouts.values()))["detail"]
    assert w.fly.deploys == []
    w2 = _lane_world()
    w2.release_all(only="smd-staging", accept_lane_inputs=True)
    assert w2.store.states()["smd-staging"] == "done"


def test_lane_inputs_unchanged_passes() -> None:
    w = _lane_world()
    w.r2.put_json(image_key(OLD_DIGEST, "lane-inputs.json"), {"medchron": "new"})
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "done"


def test_ssh_hang_during_the_gate_is_a_tooling_error_not_a_deploy() -> None:
    w = World()
    w.fly.ssh_raises.add("smd-staging")
    result = w.release_all()
    assert w.store.states()["smd-staging"] == "tooling_error"
    assert w.fly.deploys == [] and result.halted


def test_deploy_failure_without_a_seat_verdict_does_not_roll_back() -> None:
    w = World()
    w.fly.deploy_results = [Result(1, "", "Error: context deadline exceeded")]
    w.release_all(only="smd-staging")
    assert w.store.states()["smd-staging"] == "tooling_error"
    assert len(w.fly.deploys) == 1


def test_force_mid_job_skips_the_gate_loudly() -> None:
    w = World()
    w.fly.probe_facts["smd-staging"] = [
        {"lanes": [], "children": [{"file": "child.pid", "pid": "9", "alive": True}], "crons": [], "errors": []}
    ]
    w.release_all(only="smd-staging", force_mid_job=True)
    assert w.store.states()["smd-staging"] == "done"
    assert any("--force-mid-job" in line for line in w.logs)


def test_only_unknown_seat_refuses() -> None:
    w = World()
    with pytest.raises(Refusal, match="no ring"):
        w.release_all(only="nobody")


def test_parallel_ring_still_halts_and_last_ring_stays_serial() -> None:
    w = World(
        Check("pass", "a"),
        Check("pass", "b"),
        Check("fail", "FAILED: y — command failed"),
        Check("pass", "c"),
        Check("pass", "re"),
    )
    result = w.release_all(parallel=4)
    assert result.halted
    assert w.store.states()["ashton-price"] == "halted"


def test_dry_run_touches_nothing() -> None:
    w = World()
    plan = ImagePlan(w.rel, None, dry_run=True)
    waves = Waves(w.store, w.logs.append)  # type: ignore[arg-type]
    result = waves.run(
        RINGS, lambda s, r: w.roll.run(s, r, plan), release=w.rel, only=None, parallel=1, dry_run=True, lease=None
    )
    assert w.fly.deploys == [] and w.store.rollouts == {} and w.fly.quiesce_calls == []
    assert not result.completed
    assert ids.RELEASE_ID_RE.match(w.rel["release_id"])


# -- seat leases and record-based completion (2026-10-21) -------------------------------
# Falsifiers: drop the SeatLease wrapper in Waves._leased and the held-seat tests
# deploy over another holder; restore `completed = not (halted or only)` and the
# --only-completes test goes red; count waiting_idle as done and the busy-seat
# test goes red.


def _run(w: World, *, only: str | None = None, holder: str = "tester", fleet_lease: bool = True, **plan_kw):
    plan = ImagePlan(w.rel, "3" * 40, **plan_kw)
    waves = Waves(w.store, w.logs.append)  # type: ignore[arg-type]

    def walk(lease):
        return waves.run(RINGS, lambda s, r: w.roll.run(s, r, plan), release=w.rel, only=only, parallel=1,
                         dry_run=False, lease=lease, holder=holder)  # fmt: skip

    if not fleet_lease:
        return walk(None)
    with Lease(w.store, holder, w.rel["release_id"]) as lease:  # type: ignore[arg-type]
        return walk(lease)


def test_each_seat_deploy_holds_its_seat_lease_and_releases_it() -> None:
    w = World()
    seen: list[str] = []
    real = w.roll.run

    def spy(seat, ring, plan):
        row = w.store.lease(f"seat:{seat}")
        seen.append(row["holder"] if row else "")
        return real(seat, ring, plan)

    w.roll.run = spy  # type: ignore[method-assign]
    _run(w)
    assert seen == ["tester"] * 5
    assert not any(k.startswith("seat:") for k in w.store.leases)


def test_full_rollout_skips_a_seat_another_holder_is_deploying() -> None:
    w = World()
    w.store.leases["seat:scott"] = {"lease_name": "seat:scott", "holder": "reprovision@laptop", "release_id": "x"}
    result = _run(w)
    states = w.store.states()
    assert states["scott"] == "waiting_idle"
    scott = next(r for r in w.store.rollouts.values() if r["customer_slug"] == "scott")
    assert "reprovision@laptop" in scott["detail"]
    assert "hermes-scott" not in [a for a, _ in deployed_images(w)]
    assert states["ashton-price"] == "done" and not result.halted
    assert not result.completed  # scott never got it
    assert w.store.leases["seat:scott"]["holder"] == "reprovision@laptop"  # never taken over


def test_only_refuses_a_seat_another_holder_is_deploying() -> None:
    w = World()
    w.store.leases["seat:scott"] = {"lease_name": "seat:scott", "holder": "someone", "release_id": "x"}
    with pytest.raises(Refusal, match="being deployed by someone"):
        _run(w, only="scott", fleet_lease=False)
    assert w.fly.deploys == []


def test_only_runs_while_a_full_rollout_holds_the_fleet_lease() -> None:
    w = World()
    w.store.lease_row = {"lease_name": "rollout", "holder": "full-rollout", "release_id": "img-x", "expires_at": "2099"}
    _run(w, only="scott", holder="fixer", fleet_lease=False)
    assert w.store.states()["scott"] == "done"
    assert w.store.lease_row["holder"] == "full-rollout"


def test_only_fix_of_the_failed_seats_completes_the_release() -> None:
    w = World()
    w.fly.secrets["hermes-brokery"] = ({*REQUIRED}, {"TELEGRAM_BOT_TOKEN"})  # the 2026-10-20 refusal
    first = _run(w)
    assert first.halted and not first.completed
    w.fly.secrets["hermes-brokery"] = ({*REQUIRED}, set())  # the staged secret was applied
    assert not _run(w, only="brokery", fleet_lease=False).completed  # ashton-price was halted, never deployed
    assert w.store.states()["brokery"] == "done"
    assert _run(w, only="ashton-price", fleet_lease=False).completed


def test_a_busy_seat_keeps_the_release_incomplete() -> None:
    w = World()
    w.fly.probe_facts["ashton-price"] = [
        {"lanes": [], "children": [{"file": "negotiation-child.pid", "pid": "4286", "alive": True}],
         "crons": [], "errors": []}
    ]  # fmt: skip
    result = _run(w, max_idle_wait=60)
    assert w.store.states()["ashton-price"] == "waiting_idle"
    assert not result.halted and not result.completed
