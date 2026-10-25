"""Ring membership (operator/fleet/rings.yaml) resolved against the live seat set."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bin.lib.seat_dirs import seat_slugs, unprovisioned_slugs

from . import ids


# Minutes every seat of a ring must have been done on a release, with no open
# seat alarm, before the next ring takes it (ADR 0096). A ring's own value comes
# from its seats' seat.yaml `bake_minutes`, rendered into rings.yaml.
DEFAULT_BAKE_MINUTES = 30


@dataclass
class Ring:
    number: int
    seats: list[str] = field(default_factory=list)
    max_parallel: int | None = None
    bake_minutes: int = DEFAULT_BAKE_MINUTES


def live_seats(customers_dir: Path) -> list[str]:
    """Every provisioned seat dir: the one rule in bin/lib/seat_dirs.py, less fly_app: null seats.

    A seat authored with ``fly_app: null`` (seat_dirs.unprovisioned_slugs) has
    no app; the rings.yaml generator leaves it out of every ring, and this keeps
    the backstop below from putting it back in the last ring, where a release
    would deploy to an app that does not exist and halt the rollout.
    """
    skip = unprovisioned_slugs(customers_dir)
    return [s for s in seat_slugs(customers_dir) if s not in skip]


def load_rings(rings_file: Path, customers_dir: Path) -> list[Ring]:
    raw = yaml.safe_load(rings_file.read_text(encoding="utf-8")) or {}
    rings: list[Ring] = []
    seen: set[str] = set()
    for entry in raw.get("rings") or []:
        bake = entry.get("bake_minutes", DEFAULT_BAKE_MINUTES)
        if isinstance(bake, bool) or not isinstance(bake, int) or bake < 0:
            raise ValueError(
                f"{rings_file}: ring {entry.get('ring')} bake_minutes {bake!r} is not a whole minute count"
            )
        ring = Ring(int(entry["ring"]), [], entry.get("max_parallel"), bake)
        for seat in entry.get("seats") or []:
            ids.slug(seat)
            if seat in seen:
                raise ValueError(f"{rings_file}: seat {seat} is in two rings")
            seen.add(seat)
            ring.seats.append(seat)
        rings.append(ring)
    if not rings:
        raise ValueError(f"{rings_file}: no rings")
    rings.sort(key=lambda r: r.number)
    # Last ring always deploys one seat at a time (the paying client's seat).
    rings[-1].max_parallel = 1
    live = live_seats(customers_dir)
    for seat in live:
        if seat not in seen:
            rings[-1].seats.append(seat)
    known = set(live)
    for ring in rings:
        ring.seats = [s for s in ring.seats if s in known]
    return rings


def parallel_for(ring: Ring, requested: int) -> int:
    cap = ring.max_parallel if ring.max_parallel else requested
    return max(1, min(requested, cap))
