#!/usr/bin/env python3
"""Seat check: the runner lanes' memory caps leave the gateway its reserve.

Run with the medchron venv python (it imports the runner's own defaults). The
boot smoke sends this file's text to the Machine over ssh; the in-image
self-test runs the image's copy at /app/lane-memory-caps-probe.py. One program,
two transports (operator/templates/seat-checks.sh).

WHY. Every lane child runs under a memory cgroup cap and the gateway runs under
none, so the caps must leave the gateway room: the sum of every lane cgroup's
limit must not exceed the authored machine memory minus a 512 MiB gateway
reserve (the #2465 shape: a 1 GB guest went health-critical when a job and the
gateway shared it). A cgroup a lane has not made yet counts at the cap the
daemon WILL write: the authored machine.lane_memory_mb value when the
customer.yaml authors one, else the code default read from the runner itself.
The litigation and drafting lanes share the demand cgroup, so it counts once. A
lane that cannot run a job on this seat (no firm inputs staged) counts zero; a
cgroup that already exists has run a job and always counts at its live limit.

FALSIFIER: raise a lane default (or an authored lane_memory_mb value) or shrink
machine.memory_mb on a seat with that lane's inputs staged and this exits
non-zero naming the sum.

SMD_SEAT_ROOT prefixes every seat path; it is unset on a seat and set by the
tests to a fixture tree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

GATEWAY_RESERVE_MIB = 512
LANES_SHARING_DEMAND = ("demand", "drafting", "litigation")


def _root() -> str:
    return os.environ.get("SMD_SEAT_ROOT", "")


def default_caps(doc: dict) -> dict[str, int]:
    """The cap each lane's daemon would write: authored bytes over code defaults."""
    import medchron.daemon as d
    import medchron.demand_lane as dl

    defaults = {"medchron": int(d.DEFAULT_MEMORY_MAX), "demand": int(dl.DEFAULT_MEMORY)}
    try:
        from medchron.lane_memory import authored_bytes
    except ImportError:
        return defaults
    defaults.update({k: int(v) for k, v in authored_bytes(doc).items()})
    return defaults


def runnable_lanes(cfg: Path) -> dict[str, bool]:
    return {
        "medchron": (cfg / "medchron-firm.yaml").is_file(),
        "demand": any((cfg / lane / f"{lane}-firm.yaml").is_file() for lane in LANES_SHARING_DEMAND),
    }


def live_cap(cgroup_root: Path, name: str) -> int | None:
    for f in (cgroup_root / name / "memory.max", cgroup_root / "memory" / name / "memory.limit_in_bytes"):
        if f.is_file():
            value = f.read_text().strip()
            return int(value) if value.isdigit() else None
    return None


def counted_caps(defaults: dict[str, int], runnable: dict[str, bool], cgroup_root: Path) -> dict[str, int]:
    caps: dict[str, int] = {}
    for name, default in defaults.items():
        live = live_cap(cgroup_root, name)
        if live is not None:
            caps[name] = live
        elif runnable.get(name):
            caps[name] = default
    return caps


def main() -> int:
    root = _root()
    cfg = Path(f"{root}/var/lib/smd-config")
    with open(cfg / "customer.yaml", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    mb = int((doc.get("machine") or {}).get("memory_mb") or 0)
    if mb <= 0:
        print("machine.memory_mb is not authored", file=sys.stderr)
        return 1
    caps = counted_caps(default_caps(doc), runnable_lanes(cfg), Path(f"{root}/sys/fs/cgroup"))
    budget = (mb - GATEWAY_RESERVE_MIB) * 1024 * 1024
    total = sum(caps.values())
    if total > budget:
        print(
            f"lane memory caps {caps} sum {total} > machine {mb} MiB minus {GATEWAY_RESERVE_MIB} MiB gateway reserve",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
