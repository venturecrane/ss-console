"""`fleet release <id> --tick`: one resumable step of a release, for a scheduler.

A tick is what the GitHub Actions release controller runs every 15 minutes
(.github/workflows/release-tick.yml, ADR 0096). It holds no state of its own:
everything it decides is read from D1 at the moment of decision, so a tick that
dies, a runner that is cancelled or a tick that is skipped loses nothing.

ONE TICK.
  1. Take the fleet lease. Another rollout holding it (a laptop run, a stuck
     tick inside its TTL) is not an error: this tick waits.
  2. Read each seat's latest rollout of THIS release and the holds.
  3. A seat whose latest rollout is rolled_back, failed or tooling_error is a
     STANDING HALT: nothing moves until a person acts (``fleet release <id>
     --only <seat>`` from a laptop, or a hold). The pager already went out
     (fleet-alerts rollout_failed reads the same rows).
  4. Find the first ring with a seat that is neither done nor held. Before it
     may start, the ring before it must have BAKED: every done seat there done
     for at least that ring's ``bake_minutes``, at least one seat done, and no
     open selftest_failing, image_drift or rollout_failed alarm on those seats.
     Not baked: wait. Baked: roll that ring's remaining seats (and only them).
  5. Record, renew, release the lease, exit.

So a tick advances at most one ring, never re-deploys a done seat, and never
starts the client ring until the ring before it has proved the release on real
seats for the bake. A busy seat ends waiting_idle and the next tick retries it;
a held seat is left alone and keeps the release incomplete.

What a tick never does: ``--force-mid-job`` (a running client job always wins),
``--accept-lane-inputs`` (a lane schema change is a person's call: the seat's
preflight refuses, the tick halts, and someone re-stages), and the ssh boot
smoke (the seat's own self-test decides; no verdict is a tooling_error).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import ids
from .d1 import FleetStore
from .rings import Ring
from .rollout import HALTING, SeatOutcome
from .runner import Refusal
from .waves import Lease, Waves

# Exit codes of a tick (cli.py maps them; release-tick.yml reads them).
TICK_PROGRESS = 0  # a ring took (part of) the release, or the release completed
TICK_HALT = 1  # this tick halted the release
TICK_WAITING = 5  # nothing to do yet: bake running, lease held, every seat done or held
TICK_HALTED_STANDING = 6  # an earlier tick or run halted; a person must act

# The alarms that keep the next ring waiting. rollout_failed is event-shaped
# and never `open` (workers/fleet-alerts/src/rollout-failed.ts); the halt it
# reports is read from the rollout rows themselves in step 3.
BAKE_ALARMS = ("selftest_failing", "image_drift", "rollout_failed")


@dataclass
class TickPlan:
    code: int
    reason: str
    ring: Ring | None = None
    seats: list[str] | None = None


def _done_at(latest: dict[str, dict[str, Any]], seat: str) -> datetime | None:
    row = latest.get(seat) or {}
    return ids.parse_iso(row.get("finished_at")) if row.get("state") == "done" else None


def bake_gate(
    ring: Ring,
    latest: dict[str, dict[str, Any]],
    alarms: Callable[[list[str]], list[dict[str, Any]]],
    now: datetime,
) -> str | None:
    """Why ``ring``'s successor may not start yet, or None when it may."""
    done = [s for s in ring.seats if (latest.get(s) or {}).get("state") == "done"]
    if not done:
        return f"ring {ring.number} has no seat done on this release; nothing has proved it for the next ring"
    times = [_done_at(latest, s) for s in done]
    if any(t is None for t in times):
        return f"ring {ring.number}: a done seat has no finish time; cannot measure its bake"
    last = max(t for t in times if t is not None)
    baked = (now - last).total_seconds() / 60.0
    if baked < ring.bake_minutes:
        return (
            f"ring {ring.number} is baking: its last seat finished at {ids.iso(last)}, "
            f"{baked:.0f} of {ring.bake_minutes} minutes"
        )
    open_rows = alarms(done)
    if open_rows:
        names = ", ".join(sorted(f"{r.get('customer_slug')}:{r.get('condition')}" for r in open_rows))
        return f"ring {ring.number} baked, but an alarm is open on it ({names})"
    return None


def overlay_newer(rows: dict[str, dict[str, Any]], newer: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """An older release's rows, with every seat already done on a NEWER release counted as done.

    Promotion never moves a seat backwards: a seat done on something newer is
    not pending for an older release, and a halt it once had on the older one
    is superseded. A seat done on THIS release keeps its own row, so its bake is
    measured from when it took this release, not from a later redeploy.
    """
    merged = {seat: dict(row) for seat, row in rows.items()}
    for newer_rows in newer:
        for seat, row in newer_rows.items():
            if row.get("state") == "done" and (merged.get(seat) or {}).get("state") != "done":
                merged[seat] = dict(row)
    return merged


def plan_tick(
    rings: list[Ring],
    latest: dict[str, dict[str, Any]],
    holds: dict[str, Any],
    alarms: Callable[[list[str]], list[dict[str, Any]]],
    now: datetime,
) -> TickPlan:
    """The one step this tick may take. Pure: every input was read before the call."""
    ringed = [s for ring in rings for s in ring.seats]
    halted = sorted(s for s in ringed if (latest.get(s) or {}).get("state") in HALTING)
    if halted:
        states = ", ".join(f"{s}={latest[s]['state']}" for s in halted)
        return TickPlan(
            TICK_HALTED_STANDING,
            f"the release is halted ({states}); a person must act: fix and run "
            "`fleet release <id> --only <seat>`, or hold the seat",
        )
    before: Ring | None = None
    for ring in rings:
        if not ring.seats:
            continue
        done = [s for s in ring.seats if (latest.get(s) or {}).get("state") == "done"]
        pending = [s for s in ring.seats if s not in done and s not in holds]
        if pending:
            if before is not None:
                wait = bake_gate(before, latest, alarms, now)
                if wait:
                    return TickPlan(TICK_WAITING, wait, ring)
            return TickPlan(TICK_PROGRESS, f"ring {ring.number}: {', '.join(pending)}", ring, pending)
        if not done:
            return TickPlan(
                TICK_WAITING,
                f"ring {ring.number}: every seat is held; nothing proves the release for the rings after it",
                ring,
            )
        before = ring
    held = sorted(s for s in ringed if s in holds and (latest.get(s) or {}).get("state") != "done")
    if held:
        return TickPlan(TICK_WAITING, f"every unheld seat is done; held and not yet on it: {', '.join(held)}")
    return TickPlan(TICK_WAITING, "every seat is done on this release")


class Ticker:
    """Run one tick against the records: lease, plan, at most one ring, completion."""

    def __init__(self, store: FleetStore, log: Callable[[str], None], clock: Callable[[], float]) -> None:
        self.store, self.log, self.clock = store, log, clock

    def plan(self, rings: list[Ring], release: dict[str, Any]) -> TickPlan:
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        return plan_tick(
            rings,
            self.store.release_latest(release["release_id"]),
            self.store.holds(),
            lambda seats: self.store.open_alerts(seats, BAKE_ALARMS),
            now,
        )

    def run(
        self,
        rings: list[Ring],
        release: dict[str, Any],
        do_seat: Callable[[str, int], SeatOutcome],
        holder: str,
        *,
        dry_run: bool = False,
    ) -> int:
        rid = release["release_id"]
        if dry_run:
            plan = self.plan(rings, release)
            self.log(f"tick {rid} (dry run, nothing deployed or recorded): {plan.reason}")
            return plan.code
        lease = Lease(self.store, holder, rid)
        try:
            lease.__enter__()
        except Refusal as exc:
            self.log(f"tick {rid}: waiting: {exc}")
            return TICK_WAITING
        try:
            return self._step(rings, release, do_seat, holder, lease)
        finally:
            lease.__exit__(None, None, None)

    def run_promoting(
        self,
        rings: list[Ring],
        candidates: list[dict[str, Any]],
        do_seat_for: Callable[[dict[str, Any]], Callable[[str, int], SeatOutcome]],
        holder: str,
        *,
        dry_run: bool = False,
    ) -> int:
        """`latest`: one step across every release still in flight, newest first.

        With one release in flight this is :meth:`run`. With several (merges
        landing faster than a release crosses the rings), restarting at ring 0
        for each would starve the later rings: on 2026-10-26 five merges in four
        hours left the pilot three hours stale and the client seat untouched by
        the controller. So each ring takes the NEWEST release that has baked on
        the ring before it: the newest goes as far as its own bakes allow, and
        while it waits, an older release that already proved itself on the
        earlier rings moves on. A seat already done on a newer release is never
        moved back (:func:`overlay_newer`). A halt on the newest release stops
        everything, as before; a halt on an older one only skips that release.
        """
        newest = candidates[0]["release_id"]
        if dry_run:
            for i, (rel, plan) in enumerate(self._plans(rings, candidates)):
                self.log(f"tick {rel['release_id']} (dry run, nothing deployed or recorded): {plan.reason}")
                if plan.code == TICK_PROGRESS or (i == 0 and plan.code == TICK_HALTED_STANDING):
                    return plan.code
            return TICK_WAITING
        lease = Lease(self.store, holder, newest)
        try:
            lease.__enter__()
        except Refusal as exc:
            self.log(f"tick {newest}: waiting: {exc}")
            return TICK_WAITING
        try:
            for i, (rel, plan) in enumerate(self._plans(rings, candidates)):
                self.log(f"tick {rel['release_id']}: {plan.reason}")
                if plan.code == TICK_HALTED_STANDING:
                    if i == 0:
                        return plan.code
                    continue  # superseded on the seats that matter; never blocks the newest
                if plan.code == TICK_PROGRESS:
                    if i:
                        self.log(f"tick {rel['release_id']}: the newest release that has baked on the earlier rings")
                    return self._advance(rings, rel, plan, do_seat_for(rel), holder, lease)
            return self._complete(rings, candidates[0], TICK_WAITING)
        finally:
            lease.__exit__(None, None, None)

    def _plans(self, rings: list[Ring], candidates: list[dict[str, Any]]):
        newer: list[dict[str, dict[str, Any]]] = []
        for rel in candidates:
            rows = self.store.release_latest(rel["release_id"])
            now = datetime.fromtimestamp(self.clock(), timezone.utc)
            yield (
                rel,
                plan_tick(
                    rings,
                    overlay_newer(rows, newer),
                    self.store.holds(),
                    lambda seats: self.store.open_alerts(seats, BAKE_ALARMS),
                    now,
                ),
            )
            newer.append(rows)

    def _step(self, rings: list[Ring], release: dict[str, Any], do_seat: Callable[[str, int], SeatOutcome],
              holder: str, lease: Lease) -> int:  # fmt: skip
        rid = release["release_id"]
        plan = self.plan(rings, release)
        self.log(f"tick {rid}: {plan.reason}")
        return self._advance(rings, release, plan, do_seat, holder, lease)

    def _advance(self, rings: list[Ring], release: dict[str, Any], plan: TickPlan,
                 do_seat: Callable[[str, int], SeatOutcome], holder: str, lease: Lease) -> int:  # fmt: skip
        rid = release["release_id"]
        if plan.code != TICK_PROGRESS or plan.ring is None or not plan.seats:
            return self._complete(rings, release, plan.code)
        ring = Ring(plan.ring.number, list(plan.seats), plan.ring.max_parallel, plan.ring.bake_minutes)
        result = Waves(self.store, self.log).run(
            [ring], do_seat, release=release, only=None, parallel=1, dry_run=False, lease=lease, holder=holder
        )
        for out in result.outcomes:
            self.log(f"  {out.seat:<18} {out.state:<14} {out.detail}")
        if result.halted:
            self.log(f"tick {rid}: HALTED at ring {ring.number}")
            return TICK_HALT
        return self._complete(rings, release, TICK_PROGRESS)

    def _complete(self, rings: list[Ring], release: dict[str, Any], code: int) -> int:
        """Stamp the release completed once every ringed seat is done on it (read from the records)."""
        if code == TICK_HALTED_STANDING:
            return code
        # Re-read: stamping a release twice would move it to the front of
        # latest_completed and hand a NEW seat an older image.
        current = self.store.release(release["release_id"]) or release
        if current.get("completed_last_ring_at"):
            return code
        every = {s for ring in rings for s in ring.seats}
        if every and every <= self.store.release_done_seats(release["release_id"]):
            self.store.mark_completed(release["release_id"])
            self.log(f"{release['release_id']}: every seat is done; release marked completed")
            return TICK_PROGRESS
        return code
