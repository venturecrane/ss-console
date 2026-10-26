"""machine.lane_memory_mb -> the lane cgroup cap env (medchron.lane_memory).

The entrypoint passes these words to the daemon; boot smoke counts
authored_bytes. Unauthored or out-of-bounds means no words, so every lane
keeps its code default exactly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from medchron import daemon, demand_lane, drafting_lane, litigation_lane, negotiation_lane
from medchron.lane_memory import LANE_ENVS, authored_bytes, env_words

MIB = 1024 * 1024


def _doc(lanes: object) -> dict:
    return {"machine": {"size": "shared-cpu-1x", "memory_mb": 2048, "lane_memory_mb": lanes}}


def test_authored_caps_become_bytes() -> None:
    assert authored_bytes(_doc({"medchron": 1024, "demand": 512})) == {"medchron": 1024 * MIB, "demand": 512 * MIB}


def test_each_lane_is_optional() -> None:
    assert authored_bytes(_doc({"demand": 512})) == {"demand": 512 * MIB}


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"machine": {"memory_mb": 2048}},
        _doc(None),
        _doc("1024"),
        _doc({"medchron": 255}),
        _doc({"medchron": 8193}),
        _doc({"medchron": "1024"}),
        _doc({"medchron": 1024.5}),
        _doc({"medchron": True}),
    ],
)
def test_unauthored_or_invalid_means_the_code_default(doc: dict) -> None:
    assert authored_bytes(doc) == {}
    assert env_words(doc) == []


def test_demand_value_reaches_every_lane_that_can_create_the_shared_cgroup() -> None:
    # Drafting, litigation, negotiation and casefile each create the demand cgroup at
    # THEIR OWN cap when they run before the demand lane; a demand-only export
    # would let a 1 GiB default land in a cgroup authored at 512 MiB.
    assert set(LANE_ENVS["demand"]) == {
        demand_lane.MEMORY_ENV,
        drafting_lane.MEMORY_ENV,
        litigation_lane.MEMORY_ENV,
        negotiation_lane.MEMORY_ENV,
        # The case file manager (casefile_lane.MEMORY_ENV) joins it too.
        "SMD_CASEFILE_MEMORY_MAX_BYTES",
    }
    assert LANE_ENVS["medchron"] == (daemon.MEMORY_MAX_ENV,)
    words = env_words(_doc({"demand": 512}))
    assert sorted(words) == sorted(f"{n}={512 * MIB}" for n in LANE_ENVS["demand"])


def test_cli_prints_the_words_the_entrypoint_passes(tmp_path: Path) -> None:
    yml = tmp_path / "customer.yaml"
    yml.write_text("machine:\n  memory_mb: 2048\n  lane_memory_mb: {medchron: 1024, demand: 512}\n")
    out = subprocess.run(  # noqa: S603 - argv is this interpreter, the module and a temp file
        [sys.executable, "-m", "medchron.lane_memory", str(yml)], capture_output=True, text=True, check=True
    ).stdout.split()
    assert f"SMD_MEDCHRON_MEMORY_MAX_BYTES={1024 * MIB}" in out
    assert f"SMD_DEMAND_MEMORY_MAX_BYTES={512 * MIB}" in out
    assert len(out) == 6


def test_cli_prints_nothing_for_a_missing_file(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 - argv is this interpreter, the module and a temp path
        [sys.executable, "-m", "medchron.lane_memory", str(tmp_path / "absent.yaml")], capture_output=True, text=True
    )
    assert done.returncode == 0 and done.stdout == ""


@pytest.mark.parametrize("lane_mod", [demand_lane, drafting_lane, litigation_lane, negotiation_lane])
def test_built_lanes_carry_the_authored_caps(lane_mod: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # What the entrypoint's `env NAME=BYTES ...` hands the daemon is what each
    # built lane holds as memory_max, the value _cgroup_preexec writes.
    for word in env_words(_doc({"medchron": 1024, "demand": 512})):
        name, value = word.split("=")
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(daemon.SOCKET_ENV, str(tmp_path / "broker.sock"))
    monkeypatch.setenv("CUSTOMER_SLUG", "fixture-seat")
    monkeypatch.setenv(daemon.RUN_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(lane_mod.QUEUE_ENV, str(tmp_path / "queue"))
    d = daemon.build()
    assert d.memory_max == 1024 * MIB
    lane = lane_mod.build_lane(d)
    assert lane is not None and lane.memory_max == 512 * MIB


# ---- seat check lane-memory-caps-leave-gateway-reserve, executed ----
# The check is operator/templates/lane-memory-caps-probe.py: the boot smoke
# ships its text over ssh and the in-image self-test runs /app's copy. It runs
# here as a seat would, with SMD_SEAT_ROOT pointing its seat paths at a tmp tree.

_PROBE = Path(__file__).resolve().parents[4] / "operator" / "templates" / "lane-memory-caps-probe.py"


def _run_smoke(
    tmp_path: Path, customer_yaml: str, *, live: dict[str, int] | None = None
) -> subprocess.CompletedProcess[str]:
    cfg, cg = tmp_path / "var/lib/smd-config", tmp_path / "sys/fs/cgroup"
    (cfg / "demand").mkdir(parents=True)
    cg.mkdir(parents=True)
    (cfg / "customer.yaml").write_text(customer_yaml)
    (cfg / "medchron-firm.yaml").write_text("{}\n")
    (cfg / "demand" / "demand-firm.yaml").write_text("{}\n")
    for name, limit in (live or {}).items():
        (cg / name).mkdir()
        (cg / name / "memory.max").write_text(f"{limit}\n")
    env = {**os.environ, "SMD_SEAT_ROOT": str(tmp_path)}
    # The program is this repo's own seat check, not untrusted input.
    return subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True, env=env)  # noqa: S603 - repo-authored program


_PILOT = "machine:\n  memory_mb: 2048\n  lane_memory_mb: {medchron: 1024, demand: 512}\n"


def test_smoke_counts_the_authored_caps(tmp_path: Path) -> None:
    done = _run_smoke(tmp_path, _PILOT)
    assert done.returncode == 0, done.stderr


def test_smoke_counts_the_code_defaults_when_unauthored(tmp_path: Path) -> None:
    # FALSIFIER: the 2560 + 1024 MiB defaults do not fit 2048 - 512.
    done = _run_smoke(tmp_path, "machine:\n  memory_mb: 2048\n")
    assert done.returncode == 1 and "gateway reserve" in done.stderr


def test_smoke_fails_an_authored_cap_that_does_not_fit(tmp_path: Path) -> None:
    done = _run_smoke(tmp_path, "machine:\n  memory_mb: 2048\n  lane_memory_mb: {medchron: 1536, demand: 512}\n")
    assert done.returncode == 1 and "gateway reserve" in done.stderr


def test_smoke_live_cgroup_still_wins_over_the_authored_cap(tmp_path: Path) -> None:
    done = _run_smoke(tmp_path, _PILOT, live={"medchron": 2560 * MIB})
    assert done.returncode == 1 and "gateway reserve" in done.stderr
