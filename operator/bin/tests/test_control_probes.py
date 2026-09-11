"""Tests for the negative-fire probe suite (ss#2387).

THE FALSIFIER IS THE POINT. A probe suite is worth exactly what its ability to
come back red is worth, and this venture has shipped several checks that could
not: a conformance test asserting only "refused" (which proved nothing about
which gate fired), an exemption that made a control unable to fire at all, a
reconciler that sat green for weeks scanning nothing because a missing secret
exited 0. So the first thing pinned here is not that the probes pass. It is that
each one is RED against a deliberately broken target before it is trusted GREEN
against the real one.

The broken targets are two, because the probes ask two opposite questions:
  * a refusal probe is falsified by a breaker that records but never stops (the
    exact shape of an inert control that still looks present);
  * a caller-search probe is falsified by a tree that DOES contain a caller.

Run::

    cd operator && python3 -m pytest bin/tests/test_control_probes.py -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_BIN = Path(__file__).resolve().parents[1]
_OP = _BIN.parent
_spec = importlib.util.spec_from_file_location("control_probes", _BIN / "control-probes.py")
cp = importlib.util.module_from_spec(_spec)
sys.modules["control_probes"] = cp
_spec.loader.exec_module(cp)


def _specs() -> dict:
    return cp.load_specs()


def _local_specs() -> dict:
    return {n: s for n, s in _specs().items() if s.get("kind") == "local"}


# --------------------------------------------------------------------------- #
# the falsifier: red before green                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(_local_specs()))
def test_every_local_probe_is_red_against_a_broken_target(name: str) -> None:
    spec = _local_specs()[name]
    ctx = cp.ProbeContext()
    real = cp.run_probe(spec, ctx)
    broken = cp.run_probe(spec, cp.falsify(spec, ctx))
    assert broken.status != cp.HOLD, (
        f"{name}: the broken target produced a HOLD, so the probe never asked its "
        "question and the falsifier proved nothing"
    )
    assert real.status != broken.status, (
        f"{name}: reports {real.status} against the real substrate and {broken.status} "
        "against a deliberately broken one. A probe that answers the same either way "
        "has measured nothing."
    )


def test_the_neutered_breaker_is_actually_neutered() -> None:
    """Guard the falsifier itself: if `_neuter` stopped neutering, every
    red-before-green assertion above would silently become a tautology."""
    mod, machine = cp._machine(cp.ProbeContext(neutered=True))
    state = cp._await(machine.record_cost_cents(customer="probe", persona="probe", amount_cents=10**6))
    assert state.level == mod.StickyStopLevel.OK
    # No raise: that is the defect being simulated.
    cp._await(machine.assert_allowed(customer="probe", persona="probe"))


def test_self_test_reports_every_local_probe_can_fail() -> None:
    rows = cp.self_test(cp.ProbeContext())
    assert rows, "self-test ran zero probes"
    cannot = [name for name, flipped, _ in rows if not flipped]
    assert not cannot, f"probe(s) that cannot come back red: {cannot}"


def test_self_test_catches_a_probe_that_cannot_fail() -> None:
    """The falsifier's own falsifier. A probe wired to a runner that ignores its
    target answers identically either way, and --self-test must catch it."""
    cp.LOCAL_PROBES["_always_fires"] = lambda spec, ctx: (True, "always")
    try:
        rows = cp.self_test(
            cp.ProbeContext(),
            {
                "cannot_fail": {
                    "probe": "cannot_fail",
                    "control": "x",
                    "kind": "local",
                    "expect": "refuse",
                    "runner": "_always_fires",
                }
            },
        )
    finally:
        cp.LOCAL_PROBES.pop("_always_fires")
    assert rows and rows[0][1] is False


# --------------------------------------------------------------------------- #
# outcome semantics                                                            #
# --------------------------------------------------------------------------- #


def test_inert_control_that_fires_is_a_finding_not_a_celebration() -> None:
    """An `expected-fail` probe that FIRES means the registry row is wrong, and
    the run must go red on it. Otherwise a control quietly getting wired would
    read as the same green as one that never was."""
    spec = {"probe": "p", "control": "c", "kind": "local", "expect": "expected-fail", "runner": "_fires"}
    cp.LOCAL_PROBES["_fires"] = lambda s, c: (True, "wired now")
    try:
        result = cp.run_probe(spec, cp.ProbeContext())
    finally:
        cp.LOCAL_PROBES.pop("_fires")
    assert result.status == cp.UNEXPECTED_PASS
    assert result.is_finding


def test_a_probe_that_raises_is_never_a_pass() -> None:
    def _boom(spec, ctx):
        raise RuntimeError("substrate exploded")

    cp.LOCAL_PROBES["_boom"] = _boom
    try:
        result = cp.run_probe(
            {"probe": "p", "control": "c", "kind": "local", "expect": "refuse", "runner": "_boom"},
            cp.ProbeContext(),
        )
    finally:
        cp.LOCAL_PROBES.pop("_boom")
    assert result.status == cp.FAIL


def test_missing_runner_holds_rather_than_passing() -> None:
    result = cp.run_probe(
        {"probe": "p", "control": "c", "kind": "local", "expect": "refuse", "runner": "does_not_exist"},
        cp.ProbeContext(),
    )
    assert result.status == cp.HOLD


def test_boot_probes_hold_and_name_the_observation_command() -> None:
    """A boot probe is somebody else's green. Reporting it as ours would be the
    borrowed-proof version of the built-but-not-wired defect."""
    for name, spec in _specs().items():
        if spec.get("kind") != "boot":
            continue
        result = cp.run_probe(spec, cp.ProbeContext())
        assert result.status == cp.HOLD, f"{name}: a boot probe must not report a status here"
        assert spec.get("observe"), f"{name}: boot probe must name an observation command"


# --------------------------------------------------------------------------- #
# HOLD is loud (the reconcile-sends lesson)                                    #
# --------------------------------------------------------------------------- #


def test_seat_probe_without_a_seat_holds() -> None:
    seat = next(s for s in _specs().values() if s.get("kind") == "seat")
    assert cp.run_probe(seat, cp.ProbeContext()).status == cp.HOLD


def test_seat_probe_without_a_driver_holds_even_with_a_seat() -> None:
    seat = next(s for s in _specs().values() if s.get("kind") == "seat")
    result = cp.run_probe(dict(seat, seat_command=[]), cp.ProbeContext(seat="smd-staging"))
    assert result.status == cp.HOLD
    assert "no driver authored" in result.detail


def test_seat_transport_failure_holds_and_is_not_a_finding() -> None:
    def _explode(slug, argv):
        raise OSError("no such seat")

    spec = {
        "probe": "p",
        "control": "c",
        "kind": "seat",
        "expect": "refuse",
        "seat_command": ["true"],
        "expect_pattern": "x",
    }
    result = cp.run_probe(spec, cp.ProbeContext(seat="smd-staging", run_seat=_explode))
    assert result.status == cp.HOLD
    assert not result.is_finding


def test_seat_probe_fires_when_the_driver_output_matches() -> None:
    spec = {
        "probe": "p",
        "control": "c",
        "kind": "seat",
        "expect": "refuse",
        "seat_command": ["echo"],
        "expect_pattern": "REFUSED",
    }
    ok = cp.run_probe(spec, cp.ProbeContext(seat="s", run_seat=lambda slug, argv: (0, "gate REFUSED the send")))
    bad = cp.run_probe(spec, cp.ProbeContext(seat="s", run_seat=lambda slug, argv: (0, "gate allowed the send")))
    assert (ok.status, bad.status) == (cp.PASS, cp.FAIL)


def test_hold_exits_two_and_findings_exit_one() -> None:
    """The exit-code contract the workflow depends on. A HOLD exiting 0 is the
    exact defect that let the send reconciler scan nothing for weeks."""
    code = subprocess.run(
        [sys.executable, str(_BIN / "control-probes.py"), "--kind", "boot"],
        capture_output=True,
        text=True,
    ).returncode
    assert code == 2, "a run made entirely of holds must not exit 0"


def test_local_run_is_clean_today() -> None:
    """The suite's own live state: every local probe attempted, no findings."""
    proc = subprocess.run([sys.executable, str(_BIN / "control-probes.py")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# spec hygiene                                                                 #
# --------------------------------------------------------------------------- #


def test_every_spec_is_well_formed() -> None:
    for name, spec in _specs().items():
        assert spec.get("probe") == name, f"{name}: key must equal the probe id"
        assert spec.get("kind") in {"local", "seat", "boot"}, f"{name}: bad kind"
        assert spec.get("expect") in {"refuse", "flag", "expected-fail"}, f"{name}: bad expect"
        for required in ("control", "violation", "proves", "limits"):
            assert spec.get(required), f"{name}: missing {required}"
        if spec["kind"] == "local":
            assert spec.get("runner") in cp.LOCAL_PROBES, (
                f"{name}: names runner {spec.get('runner')!r} with no implementation"
            )


def test_seat_script_is_emitted_and_names_every_seat_probe(tmp_path: Path) -> None:
    out = tmp_path / "seat-probes.sh"
    count = cp.emit_seat_script(_specs(), out)
    body = out.read_text()
    seat_names = [n for n, s in _specs().items() if s.get("kind") == "seat"]
    assert count == len(seat_names)
    for name in seat_names:
        assert name in body
    # A probe with no driver must make the script exit non-zero, not shrug.
    assert "rc=2" in body


def test_emitted_seat_script_is_repo_relative(tmp_path: Path) -> None:
    """A generated script that hardcodes the worktree it was born in is not a
    deliverable; it is a souvenir."""
    out = tmp_path / "seat-probes.sh"
    cp.emit_seat_script(_specs(), out)
    body = out.read_text()
    assert "operator/bin/seat-probe.sh" in body
    assert str(_OP) not in body


def test_probe_specs_parse_as_yaml_with_a_maintainer() -> None:
    doc = yaml.safe_load((_OP / "contracts" / "runtime-control-probes.yaml").read_text())
    assert doc.get("maintainer")
    assert doc.get("probes")
