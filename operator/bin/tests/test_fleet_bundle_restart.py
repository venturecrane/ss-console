"""A bundle release reads a slow restart back from the Machine (2026-11-19, the A&P seat).

`fly machine restart` returned rc=1 after ~70 s while the Machine was still
stopping; it stopped at 3 min 45 s and then started. The step put the old pointer
back on that exit code, so the seat booted on the old bundle, and every tick
restarted it again. Now the exit code decides only "rc=0, carry on"; anything
else is read back from the Machine (bundles.py, A RESTART THAT GAVE NO ANSWER).

Real Fly, real BusyGate, real BundleRollout: only the `fly` binary is scripted
(:class:`ScriptedFlyctl`), on the same clock as the step. `machines list`
answers from a state timeline keyed on clock time, `machine restart` advances
the clock and returns what it is scripted to, and the heartbeat reports the
version the pointer named when the Machine last booted. Each test names its
falsifier; the ones marked "fails on main" were run against origin/main's
bundles.py and fly.py.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bin.lib.fleet import bundles
from bin.lib.fleet.bundles import BundleRollout
from bin.lib.fleet.busy import BusyGate
from bin.lib.fleet.fly import Fly, FlyTokens
from bin.lib.fleet.r2 import bundle_manifest_key, bundle_pointer_key, bundle_tarball_key, image_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.rollout import SeatOutcome
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.tick import Ticker
from bin.tests.fleet_fakes import DIGEST, IDLE_FACTS, FakeR2, FakeStore, fresh_status, release

REPO = Path(__file__).resolve().parents[3]
SEAT = "lane-a"
APP = "hermes-lane-a"
MID = "6e8206d0f36d08"
V1 = "202611190100-11111111"
V2 = "202611190200-22222222"
T0 = 1_795_000_000.0
POINTER = bundle_pointer_key(SEAT, "jobs")
FLY_API_EOF = f'Error: get app: Get "https://api.machines.dev/v1/apps/{APP}/machines": EOF'
SLOW_STOP = Result(1, "", f"Error: failed to restart machine {MID}: timeout reached waiting for machine to stop")
INCIDENT = [(0.0, "stopping", None), (225.0, "stopped", "exit"), (240.0, "started", "start")]


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@dataclass
class Restart:
    """One `fly machine restart`: how long it takes, what it returns (None raises), and what the Machine does.

    ``timeline`` is (seconds after the request, state, new event type or None).
    ``unreadable_after`` makes every `machines list` from that moment on fail,
    until ``unreadable_for`` seconds have passed (forever when None).
    """

    takes: float
    result: Result | None
    timeline: list[tuple[float, str, str | None]] = field(default_factory=list)
    unreadable_after: float | None = None
    unreadable_for: float | None = None


class ScriptedFlyctl:
    """The `fly` binary for one seat's one Machine, on the test clock."""

    def __init__(self, clock: Clock, restarts: list[Restart]) -> None:
        self.clock = clock
        self.restarts = list(restarts)
        self.calls: list[tuple[float, list[str]]] = []
        self.execs: list[tuple[float, str]] = []
        self.timeline: list[tuple[float, str, str | None]] = []
        self.unreadable_from: float | None = None
        self.unreadable_until: float | None = None
        # The listing fails from the first busy probe on: the restart's own read of the Machine cannot answer.
        self.unreadable_after_probe = False

    def state(self, now: float) -> str:
        return next((s for t, s, _ in reversed(self.timeline) if t <= now), "started")

    def boots(self, now: float) -> list[float]:
        return [t for t, s, e in self.timeline if s == "started" and e and t <= now]

    def _listing(self, now: float) -> Result:
        if self.unreadable_from is not None and now >= self.unreadable_from:
            if self.unreadable_until is None or now < self.unreadable_until:
                return Result(1, "", FLY_API_EOF)
        events = [{"type": "launch", "status": "created", "source": "user", "timestamp": int((T0 - 86400) * 1000)}]
        events += [{"type": e, "status": s, "source": "flyd", "timestamp": int(t * 1000)}
                   for t, s, e in self.timeline if e and t <= now]  # fmt: skip
        machine = {"id": MID, "name": "lane", "state": self.state(now), "region": "phx",
                   "config": {"image": f"registry.fly.io/{APP}:deployment-OLD"}, "events": events[::-1]}  # fmt: skip
        return Result(0, json.dumps([machine]), "")

    def _restart(self, now: float) -> Result:
        script = self.restarts.pop(0) if self.restarts else Restart(5, Result(0, "", ""))
        self.timeline += [(now + t, s, e) for t, s, e in script.timeline]
        if script.unreadable_after is not None:
            self.unreadable_from = now + script.unreadable_after
            if script.unreadable_for is not None:
                self.unreadable_until = self.unreadable_from + script.unreadable_for
        self.clock.sleep(script.takes)
        if script.result is None:
            raise ToolingError(f"fly gave no answer in {script.takes:.0f}s (fly machine restart ...)")
        return script.result

    def _exec(self, argv: list[str], now: float) -> Result:
        encoded = re.search(r"b64decode\('([^']+)'\)", argv[4])
        assert encoded, argv
        source = zlib.decompress(base64.b64decode(encoded.group(1))).decode()
        kind = "quiesce" if "ARGS = json.loads" in source else "probe"
        self.execs.append((now, kind))
        if kind == "probe" and self.unreadable_after_probe:
            self.unreadable_from = now
        if self.state(now) != "started":
            return Result(1, "", f"Error: machine {MID} is not started")
        if kind == "quiesce":
            return Result(0, "SMD_QUIESCE set {}" if '\\"action\\": \\"set\\"' in source else "SMD_QUIESCE cleared", "")
        return Result(0, "SMD_BUSY_PROBE " + json.dumps(IDLE_FACTS), "")

    def run(
        self, argv: Any, *, input_text: Any = None, timeout: float = 120, cwd: Any = None, env: Any = None
    ) -> Result:
        argv = list(argv)
        now = self.clock()
        self.calls.append((now, argv))
        if argv[1:3] == ["machines", "list"]:
            return self._listing(now)
        if argv[1:3] == ["machine", "restart"]:
            assert argv[3:] == [MID, "-a", APP], argv
            return self._restart(now)
        if argv[1:3] == ["machine", "exec"]:
            return self._exec(argv, now)
        raise AssertionError(f"unexpected fly call: {argv}")

    def restart_calls(self) -> list[float]:
        return [t for t, argv in self.calls if argv[1:3] == ["machine", "restart"]]


class TimedR2(FakeR2):
    """FakeR2 that remembers when each pointer was written, so a boot reads what it really would have."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock
        self.history: list[tuple[float, str, Any]] = []

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        super().put_bytes(key, data, content_type)
        self.history.append((self.clock(), key, json.loads(data) if key == POINTER else None))

    def delete(self, key: str) -> None:
        super().delete(key)
        self.history.append((self.clock(), key, None))

    def pointer_at(self, t: float) -> str:
        doc = next((v for at, k, v in reversed(self.history) if k == POINTER and at <= t), None)
        return (doc or {}).get("version") or "image"

    def pointer_writes(self) -> list[tuple[float, str | None]]:
        return [(at, (v or {}).get("version")) for at, k, v in self.history if k == POINTER]


class BootingStore(FakeStore):
    """fleet_status whose heartbeat reports the bundle the pointer named at the Machine's last boot."""

    def __init__(self, flyctl: ScriptedFlyctl, r2: TimedR2) -> None:
        super().__init__()
        self.flyctl, self.r2 = flyctl, r2
        self.refuses = False
        # fleet_status reads that fail once the restart was requested (D1 blipped).
        self.fail_after_restart = 0
        # Rollout states whose write D1 refuses, and each lease renewal (time, holder, name).
        self.failing_status_writes: set[str] = set()
        self.renewals: list[tuple[float, str, str]] = []

    def update_rollout(self, rid: str, state: str, **fields: Any) -> None:
        if state in self.failing_status_writes:
            raise ToolingError(f"wrangler d1 execute failed writing rollout state {state}")
        super().update_rollout(rid, state, **fields)

    def renew_lease(self, holder: str, name: str = "rollout") -> bool:
        self.renewals.append((self.flyctl.clock(), holder, name))
        return super().renew_lease(holder, name)

    def seat_status(self, seat: str) -> dict[str, Any] | None:
        if self.fail_after_restart and self.flyctl.restart_calls():
            self.fail_after_restart -= 1
            raise ToolingError("fleet_status read failed: D1 did not answer")
        row = super().seat_status(seat)
        boots = self.flyctl.boots(self.flyctl.clock())
        if row is not None and boots:
            version = "image" if self.refuses else self.r2.pointer_at(boots[-1])
            row["bundles_json"] = json.dumps({"jobs": version})
        return row


class RestartWorld:
    def __init__(self, *restarts: Restart, previous: str | None = V1) -> None:
        self.clock = Clock()
        self.r2 = TimedR2(self.clock)
        self.flyctl = ScriptedFlyctl(self.clock, list(restarts))
        self.store = BootingStore(self.flyctl, self.r2)
        self.logs: list[str] = []
        self.fly = Fly(self.flyctl, str(REPO), FlyTokens({}), sleep=self.clock.sleep)  # type: ignore[arg-type]
        self.gate = BusyGate(self.fly, "t", self.clock.sleep, self.clock)
        self.roll = BundleRollout(self.store, self.r2, self.fly, self.gate, self.logs.append,  # type: ignore[arg-type]
                                  self.clock.sleep, self.clock, max_idle_wait=60)  # fmt: skip
        self.store.insert_release(release())
        self.r2.put_json(image_key(DIGEST, "image-capabilities.json"), {"schema_version": 1})
        for version in (V1, V2):
            manifest = {"name": "jobs", "version": version, "sha256": "e" * 64, "size": 10}
            self.r2.put_bytes(bundle_manifest_key("jobs", version), json.dumps(manifest).encode())
            self.r2.put_bytes(bundle_tarball_key("jobs", version), b"zstd")
            self.store.insert_release({"release_id": f"bnd-{version}", "kind": "bundle", "artifact_name": "jobs",
                                       "artifact_version": version, "ss_console_sha": "1" * 40})  # fmt: skip
        self.store.status[SEAT] = {**fresh_status(), "bundles_json": json.dumps({"jobs": previous or "image"})}
        if previous:
            self.r2.put_json(POINTER, {"version": previous, "manifest_key": bundle_manifest_key("jobs", previous)})
        self.r2.history.clear()

    def run(self, version: str = V2) -> Any:
        manifest, key, sha = self.roll.load_manifest("jobs", version)
        return self.roll.run(SEAT, 0, self.store.releases[f"bnd-{version}"], manifest, key, sha, False)

    def pointer(self) -> str | None:
        return (self.r2.get_json(POINTER) or {}).get("version")

    def probes_after_the_restart(self) -> list[float]:
        first = self.flyctl.restart_calls()[0]
        return [t for t, kind in self.flyctl.execs if kind == "probe" and t > first]


# -- the incident --------------------------------------------------------------------------
def test_the_incident_a_slow_stop_takes_the_bundle_and_keeps_the_pointer() -> None:
    """FALSIFIER (fails on main): trust rc=1 and the previous pointer is written back at 70 s, the
    Machine boots at 240 s on V1, and the step reports a tooling_error."""
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT))
    asked = w.clock()
    out = w.run()
    assert out.state == "done", out.detail
    assert "restarted anyway" in out.detail and "rc=1" in out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2], "the previous pointer is never written back"
    assert w.r2.deletes == []
    assert len(w.flyctl.restart_calls()) == 1, "exactly one restart"
    assert w.store.seat_status(SEAT)["bundles_json"] == json.dumps({"jobs": V2})
    assert w.clock() - asked >= 240, "it waited for the Machine to come back"


def test_the_next_tick_on_the_same_timeline_never_restarts_again() -> None:
    """FALSIFIER (fails on main): the first run puts V1 back, the Machine boots on V1, and the
    next tick restarts the seat once more (it did so every tick on 2026-11-19)."""
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT))
    w.run()
    w.clock.sleep(300)  # the next tick
    second = w.run()
    assert len(w.flyctl.restart_calls()) == 1, second.detail
    assert second.state == "done" and "no restart" in second.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2, V2], "V2 by the restart, V2 again with no restart; never V1"


def test_a_restart_that_never_happened_restores_the_pointer_after_the_no_show_window() -> None:
    """FALSIFIER: keep the pointer on a Machine that stayed started with no new event and the
    seat runs V1 under a V2 pointer until a boot nobody verified."""
    w = RestartWorld(Restart(70, SLOW_STOP))
    asked = w.clock()
    out = w.run()
    assert out.state == "tooling_error", out.detail
    assert out.detail.startswith("restart did not happen: Machine stayed started for 120 s with no new event")
    assert "fly rc=1" in out.detail and "pointer restored to " + V1 in out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2, V1]
    restored_at = w.r2.pointer_writes()[-1][0]
    assert restored_at - asked >= 70 + bundles.RESTART_NO_SHOW_S
    assert len(w.flyctl.restart_calls()) == 1, "no second restart"


def test_a_restart_that_succeeded_before_flyctl_errored_is_done() -> None:
    w = RestartWorld(Restart(70, SLOW_STOP, [(10.0, "stopping", "exit"), (40.0, "started", "start")]))
    out = w.run()
    assert out.state == "done", out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2]
    assert len(w.flyctl.restart_calls()) == 1


def test_a_seat_stuck_stopped_is_seat_down() -> None:
    """FALSIFIER: wait forever, or probe the busy gate on a Machine that cannot answer, and the
    seat is down with nobody paged."""
    w = RestartWorld(Restart(70, SLOW_STOP, [(0.0, "stopping", None), (225.0, "stopped", "exit")]))
    asked = w.clock()
    out = w.run()
    assert out.state == "rolled_back" and out.halts, out.detail
    assert out.detail.startswith("SEAT DOWN: Machine state=stopped ")
    assert "after the restart request" in out.detail and "pointer restored to " + V1 in out.detail
    restored_at = w.r2.pointer_writes()[-1][0]
    assert bundles.RESTART_SETTLE_S <= restored_at - asked < bundles.RESTART_SETTLE_S + 2 * bundles.RESTART_POLL_S
    assert w.pointer() == V1
    assert w.probes_after_the_restart() == [], "no busy probe on a Machine that is not started"
    assert len(w.flyctl.restart_calls()) == 1


def test_fly_unreadable_after_the_restart_leaves_the_heartbeat_to_decide() -> None:
    """FALSIFIER: read "cannot read the Machine" as "did not restart" and the incident repeats
    whenever the Machines API drops a connection."""
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT, unreadable_after=0))
    out = w.run()
    assert out.state == "done", out.detail
    assert "could not be read back" in out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2]


def test_fly_unreadable_and_no_heartbeat_is_the_existing_rollback() -> None:
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT, unreadable_after=0))
    w.store.refuses = True
    asked = w.clock()
    out = w.run()
    assert out.state == "rolled_back", out.detail
    assert out.detail.startswith("timeout: ") and "pointer restored to " + V1 in out.detail
    restored_at = w.r2.pointer_writes()[-1][0]
    assert restored_at - asked >= bundles.BUNDLE_READBACK_WINDOW_S, "restored by the rollback, not by the observer"
    assert "no rollback restart" in out.detail
    assert len(w.flyctl.restart_calls()) == 1


def test_a_restart_that_gave_no_answer_at_all_is_read_back() -> None:
    """FALSIFIER (fails on main): the ToolingError escaped to run(), the pointer stayed at V2
    unverified and the step said tooling_error."""
    w = RestartWorld(Restart(600, None, INCIDENT))
    out = w.run()
    assert out.state == "done", out.detail
    assert "gave no answer" in out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2]
    assert len(w.flyctl.restart_calls()) == 1


def test_a_rollback_restart_still_in_flight_is_read_back_not_called_failed() -> None:
    """FALSIFIER (fails on main): "the rollback restart failed (rc=1): its next boot takes it"
    while the Machine was in fact restarting onto the restored pointer."""
    w = RestartWorld(
        Restart(5, Result(0, "", ""), [(0.0, "stopping", None), (30.0, "started", "start")]),
        Restart(70, SLOW_STOP, [(0.0, "stopping", None), (100.0, "stopped", "exit"), (110.0, "started", "start")]),
        previous=None,
    )
    w.store.refuses = True  # the new boot never reports V1, so the step rolls back
    out = w.run(V1)
    assert out.state == "rolled_back", out.detail
    assert "restart failed" not in out.detail
    assert "pointer removed (there was none)" in out.detail
    assert "restarted (fly rc=1" in out.detail and "read-back of jobs image: pass" in out.detail
    assert len(w.flyctl.restart_calls()) == 2, "the step's restart and the rollback's, never a third"
    assert [v for _, v in w.r2.pointer_writes()] == [V1, None], "V1 written, then removed by the rollback"


def test_a_machine_still_stopping_at_the_deadline_is_left_to_the_heartbeat_and_never_restarted_again() -> None:
    """A drain that outlasts every allowance is not SEAT DOWN on state alone (the Machine may yet
    start). The heartbeat never reports it, so the step rolls back; the rollback finds the Machine
    still stopping and issues no restart on top of it, so it is recorded rolled_back (halts).
    FALSIFIER: count `stopping` at the deadline as down and a still-booting seat is paged and
    rolled back under its boot; restart it in the rollback and two restarts are in flight."""
    w = RestartWorld(Restart(70, SLOW_STOP, [(0.0, "stopping", None)]))
    out = w.run()
    assert out.state == "rolled_back" and out.halts, out.detail
    assert out.detail.startswith("timeout: ") and "still stopping" in out.detail
    assert w.probes_after_the_restart() == [] and len(w.flyctl.restart_calls()) == 1
    assert [v for _, v in w.r2.pointer_writes()] == [V2, V1]


@pytest.mark.parametrize(
    "takes,result,timeline",
    [
        (600, None, [(0.0, "stopping", None), (300.0, "stopped", "exit"), (605.0, "started", "start")]),
        (470, SLOW_STOP, [(0.0, "stopping", None), (300.0, "stopped", "exit"), (485.0, "started", "start")]),
        (500, SLOW_STOP, [(0.0, "stopping", None), (300.0, "stopped", "exit"), (510.0, "started", "start")]),
    ],
    ids=["flyctl-hangs-to-600s", "rc1-at-470s", "rc1-after-480s"],
)
def test_a_flyctl_slower_than_the_settle_allowance_still_takes_the_bundle(
    takes: float, result: Result | None, timeline: list[Any]
) -> None:
    """Review D1/D1b. FALSIFIER: measure the 480 s from the request alone and the first read,
    already past it, finds the Machine stopped mid-boot and writes V1 back under the boot."""
    w = RestartWorld(Restart(takes, result, timeline))
    out = w.run()
    assert out.state == "done", out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2]
    assert len(w.flyctl.restart_calls()) == 1


def test_a_rollback_never_restarts_a_machine_whose_first_restart_is_still_in_flight() -> None:
    """Review D3. The Machine cannot be read right after the request, so the step leaves it to the
    heartbeat; a fleet_status blip ends the read-back with no verdict and the rollback begins while
    the first restart is still draining. FALSIFIER: restart a `stopping` Machine and a second
    restart lands on the first one's drain."""
    w = RestartWorld(
        Restart(
            70,
            SLOW_STOP,
            [(0.0, "stopping", None), (400.0, "stopped", "exit"), (415.0, "started", "start")],
            unreadable_after=0,
            unreadable_for=205,
        ),  # fmt: skip
    )
    w.store.fail_after_restart = 1
    asked = w.clock()
    out = w.run()
    restarts = [t - asked for t in w.flyctl.restart_calls()]
    assert len(restarts) == 2 and restarts[1] >= 415, (restarts, out.detail)
    assert out.state == "tooling_error", out.detail
    assert "read-back of jobs " + V1 + ": pass" in out.detail
    assert [v for _, v in w.r2.pointer_writes()] == [V2, V1]
    assert [t for t in w.probes_after_the_restart() if t - asked < 415] == [], "no busy probe before it started"


def test_a_machine_that_cannot_be_found_before_the_restart_is_never_restarted() -> None:
    """Review defect 3 / A. The read of the Machine (its id and baseline) fails, then Fly answers
    again and the Machine stays started. FALSIFIER: treat the failed id lookup as "the restart gave
    no answer", settle, wait out the 15-minute read-back and page rolled_back for a restart that
    was never sent."""
    w = RestartWorld()
    w.flyctl.unreadable_after_probe = True
    w.flyctl.unreadable_until = T0 + 30
    out = w.run()
    assert out.state == "tooling_error", out.detail
    assert out.detail.startswith("restart not sent: ")
    assert w.flyctl.restart_calls() == []
    assert [v for _, v in w.r2.pointer_writes()] == [V2, V1]
    assert w.clock() - T0 < 60, "no settle and no read-back"


def test_a_machine_already_stopped_before_the_request_is_compared_with_its_own_state() -> None:
    """Review defect 5. FALSIFIER: count "not started" as movement when it was not started before
    the request either, and a restart that did nothing waits out the deadline and reads as down."""
    w = RestartWorld(Restart(70, SLOW_STOP))
    w.flyctl.timeline = [(T0 - 10, "stopped", None)]
    got = w.roll._restart_and_settle(SEAT)
    assert got.outcome == "not_happened", got


def test_a_rollback_that_leaves_the_seat_down_says_seat_down_first() -> None:
    """Review B: the page subject shows only the first 60 characters
    (workers/fleet-alerts/src/rollout-failed.ts)."""
    w = RestartWorld(
        Restart(5, Result(0, "", ""), [(0.0, "stopping", None), (30.0, "started", "start")]),
        Restart(70, SLOW_STOP, [(0.0, "stopping", None), (100.0, "failed", "exit")]),
    )
    w.store.refuses = True
    out = w.run()
    assert out.state == "rolled_back", out.detail
    assert out.detail.startswith("SEAT DOWN: timeout: "), out.detail


def test_a_rollback_with_no_restart_halts_so_the_twin_gate_stays_shut() -> None:
    """Review C. The read-back gives no verdict and the rollback cannot restart (the Machine is
    unreadable), so the seat still runs the rejected V2. FALSIFIER: record that tooling_error and
    the next tick finds the seat reporting V2, records done with no self-test, and opens the twin
    gate for the client seat."""
    w = RestartWorld(Restart(5, Result(0, "", ""), [(0.0, "stopping", None), (3.0, "started", "start")],
                             unreadable_after=5))  # fmt: skip
    w.store.fail_after_restart = 1
    client_runs: list[str] = []

    def do_seat(seat: str, ring: int) -> Any:
        if seat == SEAT:
            return w.run()
        client_runs.append(seat)
        return SeatOutcome(seat, "done", "client")

    rings = [Ring(0, [SEAT, "client-b"], None, 0, {"client-b": SEAT})]
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]
    rel = w.store.releases[f"bnd-{V2}"]
    for _ in range(2):
        ticker.run_promoting(rings, [rel], lambda r: do_seat, "h")
        w.clock.sleep(300)
    states = [r["state"] for r in w.store.rollouts.values() if r["customer_slug"] == SEAT]
    assert "done" not in states and states[0] == "rolled_back", states
    assert client_runs == [], "the client seat never takes a bundle its twin did not prove"


def test_the_rollout_row_says_deploying_then_smoking_while_the_seat_restarts() -> None:
    """Review D: restart-hold (workers/fleet-alerts/src/restart-hold.ts) holds boot-shaped pages
    only while the row is deploying or smoking. FALSIFIER: leave the row pending and a slow drain
    the step accepts pages heartbeat_red."""
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT))
    w.run()
    (row,) = w.store.rollouts.values()
    assert row["history"] == ["pending", "deploying", "smoking", "done"]
    down = RestartWorld(Restart(70, SLOW_STOP, [(0.0, "stopping", None), (225.0, "stopped", "exit")]))
    down.run()
    (row,) = down.store.rollouts.values()
    assert row["history"] == ["pending", "deploying", "rolled_back"]


@pytest.mark.parametrize("refused", [{"smoking"}, {"deploying"}, {"deploying", "smoking"}])
def test_a_rollout_status_write_that_fails_never_decides_the_outcome(refused: set[str]) -> None:
    """Re-review 1. FALSIFIER: let the smoking write raise after a real restart and the step ends
    tooling_error with the pointer at V2 and no read-back, so the next tick records done with no
    self-test; let the deploying write raise and the quiesce marker is never cleared."""
    w = RestartWorld(Restart(70, SLOW_STOP, INCIDENT))
    w.store.failing_status_writes = refused
    out = w.run()
    assert out.state == "done", out.detail
    assert w.store.rollouts and next(iter(w.store.rollouts.values()))["state"] == "done"
    assert any(kind == "quiesce" and t > w.flyctl.restart_calls()[0] for t, kind in w.flyctl.execs), "marker cleared"
    assert any("could not record rollout state" in line for line in w.logs)


def test_a_rollback_onto_a_machine_that_stays_stopped_is_seat_down() -> None:
    """Re-review 2. The seat's new boot stops for good; the rollback restart does nothing to it.
    FALSIFIER: report that as an ordinary rollback and the page subject never says the seat is down."""
    w = RestartWorld(
        Restart(5, Result(0, "", ""), [(0.0, "stopping", None), (30.0, "started", "start"), (60.0, "stopped", "exit")]),
        Restart(70, SLOW_STOP),
    )
    w.store.refuses = True
    out = w.run()
    assert out.state == "rolled_back" and out.halts, out.detail
    assert out.detail.startswith("SEAT DOWN: timeout: "), out.detail
    assert "rollback restart did not happen" in out.detail
    assert len(w.flyctl.restart_calls()) == 2


def test_the_seat_lease_is_renewed_after_the_read_back_and_after_the_rollback_idle_wait() -> None:
    """Re-review 3. The urgent one-seat path can run past the 2-hour seat lease (idle wait 30 min,
    restart and settle 12, read-back 15, self-test 20, rollback settle 8, idle wait 30, restart 12,
    read-back 10: about 137 min). FALSIFIER: never renew and a tick takes the expired
    lease and rolls the same seat during the rollback."""
    w = RestartWorld(Restart(5, Result(0, "", ""), [(0.0, "stopping", None), (30.0, "started", "start")]))
    w.roll = BundleRollout(w.store, w.r2, w.fly, w.gate, w.logs.append, w.clock.sleep, w.clock,  # type: ignore[arg-type]
                           max_idle_wait=60, holder="h-1")  # fmt: skip
    w.store.acquire_lease("h-1", f"bnd-{V2}", "seat:lane-a")
    w.store.refuses = True
    asked = w.clock()
    out = w.run()
    assert out.state == "rolled_back", out.detail
    names = {(holder, name) for _, holder, name in w.store.renewals}
    assert names == {("h-1", "seat:lane-a")}
    times = [t - asked for t, _, _ in w.store.renewals]
    assert len(times) == 2 and times[0] >= bundles.BUNDLE_READBACK_WINDOW_S, times
    second_restart = w.flyctl.restart_calls()[1] - asked
    assert times[0] <= times[1] <= second_restart, "after the read-back, then after the rollback's idle wait"


# -- Fly.machine -------------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["started", "stopping", "stopped", "replacing"])
def test_fly_machine_reads_the_one_machine_state_and_events(state: str) -> None:
    listing = [{
        "id": MID, "name": "lane", "state": state, "region": "phx", "instance_id": "01K",
        "config": {"image": "registry.fly.io/hermes-fleet:rel"},
        "events": [
            {"type": "start", "status": "started", "source": "flyd", "timestamp": 1795000240123},
            {"type": "exit", "status": "stopped", "source": "flyd", "timestamp": 1795000225000,
             "request": {"exit_event": {"exit_code": 0, "oom_killed": False}}},
            {"type": "restart", "status": "stopping", "source": "user", "timestamp": 1795000000456},
            "not an event",
        ],
    }]  # fmt: skip

    class Binary:
        def run(self, argv: Any, **_kw: Any) -> Result:
            assert list(argv)[1:] == ["machines", "list", "-a", APP, "--json"]
            return Result(0, json.dumps(listing), "")

    got = Fly(Binary(), str(REPO), FlyTokens({})).machine(APP)  # type: ignore[arg-type]
    assert got["id"] == MID and got["state"] == state
    assert got["events"] == [
        {"type": "start", "status": "started", "timestamp": 1795000240123, "source": "flyd"},
        {"type": "exit", "status": "stopped", "timestamp": 1795000225000, "source": "flyd"},
        {"type": "restart", "status": "stopping", "timestamp": 1795000000456, "source": "user"},
    ]


def test_fly_machine_refuses_no_machine_an_unreadable_timestamp_and_a_missing_state_or_id() -> None:
    for listing in (
        [],
        [{"id": MID, "state": "started", "events": [{"type": "start", "timestamp": "soon"}]}],
        [{"id": MID, "events": []}],  # review defect 4: no state is an unread, never movement
        [{"id": MID, "state": "", "events": []}],
        [{"state": "started", "events": []}],
    ):

        class Binary:
            def run(self, argv: Any, **_kw: Any) -> Result:
                return Result(0, json.dumps(listing), "")

        with pytest.raises(ToolingError):
            Fly(Binary(), str(REPO), FlyTokens({})).machine(APP)  # type: ignore[arg-type]
