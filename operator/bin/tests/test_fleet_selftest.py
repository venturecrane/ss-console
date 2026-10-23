"""The seat's in-image self-test as a fleet verifier, and `fleet selftest --plant`.

judge_selftest_row    passes only the RELEASE's run: fresh, after the deploy,
                      on the release image and code version. A late row from
                      the previous image waits; it can never pass.
FleetSideChecks       what the seat cannot report about itself: started, on
                      the authored guest size.
SelftestPlant         staging only; plants, proves open, always clears,
                      proves resolved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bin.lib.fleet import ids
from bin.lib.fleet.runner import Refusal, ToolingError
from bin.lib.fleet.selftest_plant import SelftestPlant
from bin.lib.fleet.smoke import Check, FleetSideChecks, authored_guest, judge_selftest_row
from bin.tests.fleet_fakes import FakeFly, fresh_status, release, selftest_json

SINCE = ids.parse_iso("2026-10-23T00:00:00Z")


def _row(**over: object) -> dict:
    return {**fresh_status(), "selftest_json": selftest_json(**over)}


def _judge(row: dict | None, hermes: str | None = "3" * 40) -> Check | None:
    assert SINCE is not None
    return judge_selftest_row(row, SINCE, release(), hermes)


def test_judge_passes_the_release_run() -> None:
    got = _judge(_row())
    assert got is not None and got.cls == "pass" and "74 checks" in got.detail


def test_judge_fails_on_a_failed_check_naming_it() -> None:
    got = _judge(_row(failed_count=1, failed=["retired-skills-absent"]))
    assert got is not None and got.cls == "fail" and "retired-skills-absent" in got.detail


def test_judge_fails_on_the_wrong_hermes() -> None:
    got = _judge(_row(hermes_sha="4" * 40))
    assert got is not None and got.cls == "fail" and "Hermes" in got.detail


@pytest.mark.parametrize(
    "over",
    [
        {"image_ref": "registry.fly.io/hermes-x:deployment-OLD"},
        {"version": "9" * 40},
        {"finished_at": "2026-10-22T23:59:59Z"},
    ],
)
def test_judge_waits_on_a_row_that_is_not_the_release_run(over: dict) -> None:
    # FALSIFIER: drop the image or version comparison and a late green row from
    # the previous image verifies a release it never ran.
    assert _judge(_row(**over)) is None


def test_judge_waits_on_no_row_no_field_and_junk() -> None:
    assert _judge(None) is None
    assert _judge(fresh_status()) is None
    assert _judge({**fresh_status(), "selftest_json": "{not json"}) is None
    # The heartbeat row's own version must match too, not only the self-test's.
    assert _judge({**_row(), "version": "9" * 40}) is None


def test_authored_guest_follows_the_machine_size_rules(tmp_path: Path) -> None:
    y = tmp_path / "customer.yaml"
    y.write_text("machine:\n  size: shared-cpu-2x\n  memory_mb: 2048\n")
    assert authored_guest(y) == ("shared", 2, 2048)
    y.write_text("machine:\n  size: shared-cpu-3x\n  memory_mb: 2048\n")
    with pytest.raises(ValueError):
        authored_guest(y)


def _side(tmp_path: Path, fly: FakeFly, yaml_text: str | None) -> Check:
    if yaml_text is not None:
        seat = tmp_path / "operator/customers/smd-staging"
        seat.mkdir(parents=True)
        (seat / "customer.yaml").write_text(yaml_text)
    clock = {"t": 0.0}

    def sleep(s: float) -> None:
        clock["t"] += s

    return FleetSideChecks(fly, str(tmp_path), sleep, lambda: clock["t"]).run("smd-staging")  # type: ignore[arg-type]


def test_fleet_side_checks_pass_started_on_the_authored_guest(tmp_path: Path) -> None:
    assert _side(tmp_path, FakeFly(), "machine:\n  size: shared-cpu-1x\n  memory_mb: 2048\n").cls == "pass"


def test_fleet_side_checks_fail_a_wrong_guest_and_a_machine_that_never_starts(tmp_path: Path) -> None:
    fly = FakeFly()
    fly.guest_value = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024}
    assert _side(tmp_path, fly, "machine:\n  size: shared-cpu-1x\n  memory_mb: 2048\n").cls == "fail"
    stopped = FakeFly()
    stopped.machine_state = "stopped"
    got = _side(tmp_path / "b", stopped, None)
    assert got.cls == "fail" and "state=stopped" in got.detail


class PlantWorld:
    """A staging seat whose self-test answers each planter call with one new run,
    and a worker that applies the hourly rule (open from the second failed run)."""

    def __init__(self, other_failures: list[str] | None = None) -> None:
        self.fly = FakeFly()
        self.alert: str | None = None
        self.runs = 0
        self.planted = False
        self.streak = 0
        self.other = other_failures or []
        self.row: dict = fresh_status()

    def seat_status(self, seat: str) -> dict:
        calls = self.fly.plant_calls
        if len(calls) > self.runs:
            self.runs = len(calls)
            action = calls[-1][1]
            self.planted = action == "plant" or (self.planted and action == "trigger")
            failed = (["retired-skills-absent"] if self.planted else []) + self.other
            self.streak = self.streak + 1 if failed else 0
            self.row = {
                **self.row,
                "selftest_json": selftest_json(
                    run_kind="hourly",
                    finished_at=f"2099-01-01T00:00:{self.runs:02d}Z",
                    failed=failed,
                    failed_count=len(failed),
                    failed_streak=self.streak,
                ),
            }
            if self.streak >= 2:
                self.alert = "open"
            elif not failed and self.alert == "open":
                self.alert = "resolved"
        return self.row


def _plant(world: PlantWorld, seat: str = "smd-staging", check: str = "retired-skills-absent"):
    clock = {"t": 0.0}

    def sleep(s: float) -> None:
        clock["t"] += s

    plant = SelftestPlant(world.fly, world.seat_status, lambda s: world.alert, sleep, lambda: clock["t"])  # type: ignore[arg-type]
    return plant.run(seat, check, 300)


def test_plant_opens_then_resolves_and_always_clears() -> None:
    world = PlantWorld()
    report = _plant(world)
    assert report.ok, report.steps
    assert [a for _, a in world.fly.plant_calls] == ["plant", "trigger", "clear"]
    assert world.alert == "resolved"


def test_plant_says_so_when_another_failing_check_holds_the_alert_open() -> None:
    world = PlantWorld(other_failures=["curator-disabled"])
    report = _plant(world)
    assert not report.ok and any("curator-disabled" in s for s in report.steps)
    assert world.fly.plant_calls[-1][1] == "clear"


def test_plant_refuses_any_seat_but_staging_and_any_unplantable_check() -> None:
    with pytest.raises(Refusal):
        _plant(PlantWorld(), seat="ashton-price")
    with pytest.raises(Refusal):
        _plant(PlantWorld(), check="matter-mixing-fence")


def test_plant_that_the_seat_refuses_is_a_tooling_error_and_still_clears() -> None:
    world = PlantWorld()
    world.fly.plant_answer = "error no trigger directory"
    with pytest.raises(ToolingError):
        _plant(world)
    assert [a for _, a in world.fly.plant_calls] == ["plant", "clear"]
