"""Rings, holds, the lease and the halt: shared by image and skills releases.

* LEASE. One rollout at a time, fleet-wide: a conditional insert into
  fleet_lease (TTL 2 h, renewed after every seat). A second `fleet release`
  while one runs is refused, naming the holder. An expired lease (a laptop that
  died) is taken over.
* RINGS. operator/fleet/rings.yaml order; a ring finishes before the next
  starts. ``--parallel N`` applies within a ring; the last ring is always 1.
* HOLDS. A seat in fleet_holds is skipped and recorded skipped_held.
* HALT. A seat that ends rolled_back / failed / tooling_error stops the
  rollout: seats already started finish, nothing new starts, and every seat not
  reached is recorded halted.
* COMPLETION. A full (not --only, not --dry-run) rollout that halted nowhere
  stamps fleet_releases.completed_last_ring_at; provision-customer.sh deploys
  the newest such image to a new seat.
"""

from __future__ import annotations

import getpass
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import ids
from .d1 import FleetStore
from .rings import Ring, parallel_for
from .rollout import SeatOutcome
from .runner import Refusal


def holder_id() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - a username is decoration, never a reason to stop
        user = "unknown"
    return f"{user}@{socket.gethostname().split('.')[0]}:{os.getpid()}:{ids.new_id('l')}"


@dataclass
class WaveResult:
    outcomes: list[SeatOutcome] = field(default_factory=list)
    halted: bool = False
    completed: bool = False


class Lease:
    def __init__(self, store: FleetStore, holder: str, release_id: str) -> None:
        self.store, self.holder, self.release_id = store, holder, release_id

    def __enter__(self) -> "Lease":
        row = self.store.acquire_lease(self.holder, self.release_id)
        if not row or row.get("holder") != self.holder:
            who = (row or {}).get("holder", "unknown")
            raise Refusal(
                f"another rollout holds the fleet lease: {who} (release {(row or {}).get('release_id')}, "
                f"expires {(row or {}).get('expires_at')}). One rollout at a time."
            )
        return self

    def renew(self) -> None:
        if not self.store.renew_lease(self.holder):
            raise Refusal("lost the fleet lease mid-rollout (expired and taken by another holder); stopping")

    def __exit__(self, *exc: object) -> None:
        self.store.release_lease(self.holder)


class Waves:
    def __init__(self, store: FleetStore, log: Callable[[str], None]) -> None:
        self.store = store
        self.log = log

    def run(
        self,
        rings: list[Ring],
        do_seat: Callable[[str, int], SeatOutcome],
        *,
        release: dict[str, Any],
        only: str | None,
        parallel: int,
        dry_run: bool,
        lease: Lease | None,
    ) -> WaveResult:
        holds = self.store.holds()
        result = WaveResult()
        halt = threading.Event()
        for ring in rings:
            seats = [s for s in ring.seats if only is None or s == only]
            if not seats:
                continue
            self.log(f"ring {ring.number}: {', '.join(seats)}")
            if halt.is_set():
                result.outcomes += [self._mark_halted(release, s, ring.number, dry_run) for s in seats]
                continue
            result.outcomes += self._ring(ring, seats, holds, do_seat, release, parallel, dry_run, lease, halt)
        result.halted = halt.is_set()
        if only and not any(o.seat == only for o in result.outcomes):
            raise Refusal(f"{only} is in no ring (operator/fleet/rings.yaml) or is not a live seat")
        result.completed = not (result.halted or only or dry_run)
        return result

    def _ring(self, ring: Ring, seats: list[str], holds: dict[str, Any], do_seat: Callable[[str, int], SeatOutcome],
              release: dict[str, Any], parallel: int, dry_run: bool, lease: Lease | None, halt: threading.Event) -> list[SeatOutcome]:  # fmt: skip
        def one(seat: str) -> SeatOutcome:
            if halt.is_set():
                return self._mark_halted(release, seat, ring.number, dry_run)
            if seat in holds:
                return self._mark_held(release, seat, ring.number, holds[seat], dry_run)
            out = do_seat(seat, ring.number)
            if out.halts:
                halt.set()
            if lease is not None:
                lease.renew()
            return out

        width = parallel_for(ring, parallel)
        if width == 1:
            return [one(s) for s in seats]
        with ThreadPoolExecutor(max_workers=width) as pool:
            return list(pool.map(one, seats))

    def _record(
        self, release: dict[str, Any], seat: str, ring: int, state: str, detail: str, dry_run: bool
    ) -> SeatOutcome:
        self.log(f"{seat}: {state.upper()} {detail}")
        if not dry_run:
            rid = ids.new_id("ro")
            self.store.start_rollout({"rollout_id": rid, "release_id": release["release_id"], "customer_slug": seat, "ring": ring,
                                      "to_image": release.get("image_ref"), "to_skills_version": release.get("skills_version")})  # fmt: skip
            self.store.update_rollout(rid, state, detail=detail)
        return SeatOutcome(seat, state, detail)

    def _mark_held(
        self, release: dict[str, Any], seat: str, ring: int, hold: dict[str, Any], dry_run: bool
    ) -> SeatOutcome:
        return self._record(
            release, seat, ring, "skipped_held", f"held by {hold.get('held_by')}: {hold.get('reason')}", dry_run
        )

    def _mark_halted(self, release: dict[str, Any], seat: str, ring: int, dry_run: bool) -> SeatOutcome:
        return self._record(
            release, seat, ring, "halted", "not reached: the rollout halted at an earlier seat", dry_run
        )
