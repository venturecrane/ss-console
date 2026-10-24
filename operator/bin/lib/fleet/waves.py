"""Rings, holds, the leases and the halt: shared by image and skills releases.

* LEASES. Two scopes in one table (fleet_lease, TTL 2 h, an expired holder is
  taken over). The FLEET lease is held by a full rollout while it walks the
  rings, so two full rollouts never race; a second one is refused, naming the
  holder. A SEAT lease (``seat:<slug>``) is held for the length of one seat's
  deploy, by a rollout and by reprovision.sh alike, so two deploys never land
  on the same seat at once. ``--only`` takes only its seat's lease: fixing one
  seat never waits for a rollout elsewhere in the fleet (2026-10-21, when a
  captain-mark restart queued behind a six-seat release for most of an hour).
  A full rollout that reaches a seat whose lease another holder has records it
  waiting_idle (no halt) and moves on; an ``--only`` against a held seat is
  refused.
* RINGS. operator/fleet/rings.yaml order; a ring finishes before the next
  starts. ``--parallel N`` applies within a ring; the last ring is always 1.
* HOLDS. A seat in fleet_holds is skipped and recorded skipped_held. Holds are
  re-read from D1 before EVERY seat, so a hold set while a release is walking
  stops the next seat it covers, not only the next run.
* RESUME. A full run skips a seat whose latest rollout of THIS release is
  ``done`` (reported, not recorded again), so re-running a release after a halt
  continues from where it stopped. ``--only`` still redeploys on request.
* HALT. A seat that ends rolled_back / failed / tooling_error stops the
  rollout: seats already started finish, nothing new starts, and every seat not
  reached is recorded halted.
* COMPLETION. Read from the records, not from one run: after any real run,
  a release whose latest rollout on EVERY ringed seat is ``done`` gets
  fleet_releases.completed_last_ring_at. So a release repaired seat by seat
  (``--only``) completes when its last seat is done, and a seat left
  waiting_idle, held or halted keeps it incomplete. provision-customer.sh
  deploys the newest completed image only to a NEW seat; an existing seat is
  redeployed on the image it already runs.
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
from .d1 import FleetStore, seat_lease_name
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
    """The fleet lease: one full rollout walking the rings at a time."""

    def __init__(self, store: FleetStore, holder: str, release_id: str) -> None:
        self.store, self.holder, self.release_id = store, holder, release_id

    def __enter__(self) -> "Lease":
        row = self.store.acquire_lease(self.holder, self.release_id)
        if not row or row.get("holder") != self.holder:
            who = (row or {}).get("holder", "unknown")
            raise Refusal(
                f"another rollout holds the fleet lease: {who} (release {(row or {}).get('release_id')}, "
                f"expires {(row or {}).get('expires_at')}). One full rollout at a time; "
                "--only <seat> does not need it."
            )
        return self

    def renew(self) -> None:
        if not self.store.renew_lease(self.holder):
            raise Refusal("lost the fleet lease mid-rollout (expired and taken by another holder); stopping")

    def __exit__(self, *exc: object) -> None:
        self.store.release_lease(self.holder)


class SeatHeld(Refusal):
    """Another holder is deploying this seat right now."""


class SeatLease:
    """One seat's lease, held for the length of one deploy of that seat."""

    def __init__(self, store: FleetStore, holder: str, seat: str, release_id: str) -> None:
        self.store, self.holder, self.release_id = store, holder, release_id
        self.seat, self.name = seat, seat_lease_name(seat)

    def __enter__(self) -> "SeatLease":
        row = self.store.acquire_lease(self.holder, self.release_id, self.name)
        if not row or row.get("holder") != self.holder:
            raise SeatHeld(
                f"{self.seat} is being deployed by {(row or {}).get('holder', 'unknown')} "
                f"(for {(row or {}).get('release_id')}, lease expires {(row or {}).get('expires_at')})"
            )
        return self

    def __exit__(self, *exc: object) -> None:
        self.store.release_lease(self.holder, self.name)


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
        holder: str | None = None,
    ) -> WaveResult:
        """Walk the rings. ``holder`` (None only in a dry run) takes each seat's lease."""
        done = set() if only else self.store.release_done_seats(release["release_id"])
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
            seat_run = self._leased(do_seat, release, holder, refuse_held=only is not None, dry_run=dry_run)
            result.outcomes += self._ring(ring, seats, done, seat_run, release, parallel, dry_run, lease, halt)
        result.halted = halt.is_set()
        if only and not any(o.seat == only for o in result.outcomes):
            raise Refusal(f"{only} is in no ring (operator/fleet/rings.yaml) or is not a live seat")
        if not dry_run:
            every = {s for ring in rings for s in ring.seats}
            result.completed = every <= self.store.release_done_seats(release["release_id"])
        return result

    def _leased(self, do_seat: Callable[[str, int], SeatOutcome], release: dict[str, Any], holder: str | None,
                *, refuse_held: bool, dry_run: bool) -> Callable[[str, int], SeatOutcome]:  # fmt: skip
        """Wrap one seat's deploy in that seat's lease.

        A seat another holder is deploying is never touched. Under --only that is
        a refusal (the one seat asked for cannot be done); in a full rollout the
        seat is recorded waiting_idle, which does not halt and keeps the release
        incomplete until that seat is done.
        """
        if dry_run or holder is None:
            return do_seat

        def run(seat: str, ring: int) -> SeatOutcome:
            try:
                with SeatLease(self.store, holder, seat, release["release_id"]):
                    return do_seat(seat, ring)
            except SeatHeld as exc:
                if refuse_held:
                    raise
                return self._record(release, seat, ring, "waiting_idle", str(exc), dry_run)

        return run

    def _ring(self, ring: Ring, seats: list[str], done: set[str], do_seat: Callable[[str, int], SeatOutcome],
              release: dict[str, Any], parallel: int, dry_run: bool, lease: Lease | None, halt: threading.Event) -> list[SeatOutcome]:  # fmt: skip
        def one(seat: str) -> SeatOutcome:
            if halt.is_set():
                return self._mark_halted(release, seat, ring.number, dry_run)
            if seat in done:
                self.log(f"{seat}: already done on {release['release_id']}; not redeployed")
                return SeatOutcome(seat, "done", "already done on this release; not redeployed")
            # Read now, not at the start of the run: a hold set a minute ago counts.
            hold = self.store.holds().get(seat)
            if hold is not None:
                return self._mark_held(release, seat, ring.number, hold, dry_run)
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
