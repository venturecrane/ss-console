"""Authored lane memory caps: ``customer.yaml`` ``machine.lane_memory_mb``.

Every lane child runs under a memory cgroup cap, and the caps plus a 512 MiB
gateway reserve must fit the Machine (boot smoke
``lane-memory-caps-leave-gateway-reserve``). The code defaults (medchron
2.5 GiB, the demand cgroup 1 GiB) assume a 4 GB guest. A seat on a smaller
guest authors lower caps instead of paying for a bigger one:

    machine:
      memory_mb: 2048
      lane_memory_mb: {medchron: 1024, demand: 512}

``medchron`` caps the chronology cgroup. ``demand`` caps the demand cgroup,
which the drafting, litigation, negotiation and casefile lanes share (each may
create it at its own cap when it runs first, so all five get the same value). Unauthored,
or a value outside the validator's bounds, means the code default exactly.

The seat entrypoint runs ``python -m medchron.lane_memory <customer.yaml>``
before each daemon start and passes the printed ``NAME=BYTES`` words to the
daemon's environment; boot smoke imports :func:`authored_bytes` to count the
same numbers. The console validator (``sections-identity.ts``) holds the same
bounds.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

#: Inclusive MiB bounds, identical to the console validator's.
MIN_MIB = 256
MAX_MIB = 8192

#: The env each authored lane cap becomes. The demand cgroup is shared, so
#: every lane that can create it reads the same value.
LANE_ENVS: dict[str, tuple[str, ...]] = {
    "medchron": ("SMD_MEDCHRON_MEMORY_MAX_BYTES",),
    "demand": (
        "SMD_DEMAND_MEMORY_MAX_BYTES",
        "SMD_DRAFTING_MEMORY_MAX_BYTES",
        "SMD_LITIGATION_MEMORY_MAX_BYTES",
        "SMD_NEGOTIATION_MEMORY_MAX_BYTES",
        "SMD_CASEFILE_MEMORY_MAX_BYTES",
    ),
}


def authored_bytes(doc: Any) -> dict[str, int]:
    """Lane -> cap in bytes, for each lane the customer.yaml authors validly."""
    machine = doc.get("machine") if isinstance(doc, dict) else None
    lanes = machine.get("lane_memory_mb") if isinstance(machine, dict) else None
    if not isinstance(lanes, dict):
        return {}
    out: dict[str, int] = {}
    for lane in LANE_ENVS:
        mib = lanes.get(lane)
        if isinstance(mib, int) and not isinstance(mib, bool) and MIN_MIB <= mib <= MAX_MIB:
            out[lane] = mib * 1024 * 1024
    return out


def env_words(doc: Any) -> list[str]:
    """The ``NAME=BYTES`` words the daemon's environment gets."""
    caps = authored_bytes(doc)
    return [f"{name}={caps[lane]}" for lane, names in LANE_ENVS.items() if lane in caps for name in names]


def main(argv: list[str]) -> int:
    """Print the env words for the customer.yaml at ``argv[0]``; nothing (and
    exit 0) when it is unreadable, so the daemon starts on its defaults."""
    try:
        doc = yaml.safe_load(Path(argv[0]).read_text())
    except (IndexError, OSError, yaml.YAMLError):
        return 0
    for word in env_words(doc):
        print(word)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
