"""One seat's `jobs` bundle release, and which seats get one at all (ADR 0100 W3).

WHAT A BUNDLE IS. The job runner venv (/opt/jobs, with its pricing table)
and the Smokeball connector venv (/opt/connectors/smokeball), built by CI on
merge inside the published core image at those exact paths
(scripts/ci-publish-bundle.sh, operator/templates/jobs-runtime.sh), packed as
fleet/bundles/jobs/<version>.tar.zst with a manifest beside it. A seat reads its
pointer, vaults/<slug>/bundles/jobs-pointer.json = {"version", "manifest_key",
"manifest_sha256"}, AT BOOT ONLY: it verifies and installs the version, bind
mounts it over the image's copy, and reports ``bundles: {"jobs": <version>}`` on
its heartbeat (``"image"`` when the image's own copy runs instead).

WHO GETS ONE. Only seats whose repo customer.yaml authors a job lane, by the one
predicate (operator/hermes-smd-overlay/shared/capabilities.py
``authored_lanes``). The rings are FILTERED to those seats
(:func:`authoring_rings`), not deferred: a deferred seat keeps a release
incomplete forever (tick.plan_tick), and a seat that authors no lane is never
going to take a jobs bundle. A seat that authors none is never touched, and its
absence never blocks completion. Nothing here reads a vertical, a product or a
slug.

ONE SEAT. Because the seat reads its pointer only at boot, a bundle release is a
restart: busy gate (the image rollout's BusyGate: a running job is never
restarted; its marker stands until the read-back, or the rollback and its
read-back, has decided) -> write the pointer -> ``fly machine restart`` -> read back a fresh
heartbeat reporting the version (and, on an image that carries it, a passing
self-test from the new boot). The rollout row reads ``deploying`` from the
pointer write and ``smoking`` from the read-back, as the image step's do, so
restart-hold (workers/fleet-alerts) holds the boot's pages. Anything else
restores the previous pointer (or removes it when there was none) and restarts
the seat so it runs what it ran: behind the busy gate when its Machine is
started, without it when the Machine is stopped, not at all when the Machine
cannot be read, is still in transition after RESTART_SETTLE_S (a restart is in
flight), or is gone. It records rolled_back (a verdict, or no restart ran, so
the seat may still run the rejected bundle and must not be retried into done)
or tooling_error (no verdict, and the rollback restart ran). A seat the
rollback leaves down (gone, or stopped with a rollback restart that did not
reach it) is recorded rolled_back with a detail beginning SEAT DOWN. The
deploying/smoking writes are best effort: a status row never decides an
outcome. The seat's lease (2 h) is renewed after the read-back and again
before the rollback restart, because the urgent one-seat path's worst case
(two 30-minute idle waits) runs past it.

A RESTART THAT GAVE NO ANSWER (2026-11-19). flyctl's exit code decides only
"rc=0, carry on". A non-zero exit or no answer at all is not "it did not
restart": on the A&P seat flyctl returned rc=1 after 70 s while the Machine was
still draining (it stopped at 3 min 45 s, then started), the old code put the
previous pointer back, and the seat booted on the old bundle, every tick. So the
Machine is read before the request (its id, state and newest event; a failed
read sends nothing: "restart not sent", pointer restored) and read back after it
(:meth:`BundleRollout._restart_and_settle`). A state other than the one before,
or a newer event, means the restart is under way, and the pointer stays while it
finishes. A Machine unchanged with no new event RESTART_NO_SHOW_S after flyctl
returned gets its pointer back as "restart did not happen". failed or destroyed
is SEAT DOWN at once (pointer restored, rolled_back, which pages); so is a
Machine that, at or after the deadline, has read stopped on every read for
RESTART_STOPPED_GRACE_S. The deadline is the later of the request plus
RESTART_SETTLE_S and flyctl's return plus RESTART_NO_SHOW_S. Any
other state at the deadline (stopping, replacing, starting), or a Machine that
cannot be read, goes to the heartbeat read-back: cannot evaluate never reads as
did not happen, and a still-booting seat is never rolled back under its boot.
No second restart is issued while one may be in flight.

ALREADY RUNNING IT (ADR 0103). A seat whose pointer names a bundle of another
layout than its image mounts adopts the newest bundle of its own layout at boot
(bundle_installer, ``layout-<n>/latest.json``), so it can report the target
version before this step runs. Then the pointer is written WITHOUT a restart and
the step is done once a fresh beat still reports the version; nothing on the
seat changes, so no busy gate and no self-test.

ORDER. A bundle published at S reaches a seat only once that seat's image
descends from base(S) (ordering.py), as skills do. Since 2026-11-07 the IMAGE
step writes the pointer at the bundle a new image needs before it reboots the
seat (bundle_align.py), so this step usually finds the seat already reporting
the version and points it with no restart.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from . import ids, ordering, smoke
from .busy import BusyGate
from .d1 import FleetStore, seat_lease_name
from .fly import Fly, deploy_excerpt
from .r2 import R2, bundle_manifest_key, bundle_pointer_key, bundle_tarball_key
from .rings import Ring
from .rollout import SeatOutcome
from .runner import ToolingError
from .skills import image_capabilities
from .smoke import Check

JOBS = "jobs"
CAPABILITIES = "operator/hermes-smd-overlay/shared/capabilities.py"
# Boot plus the bundle install (download, verify, extract, import check) before
# the first beat that can report it. The image read-back allows 6 minutes for a
# boot alone.
BUNDLE_READBACK_WINDOW_S = 15 * 60
ROLLBACK_READBACK_WINDOW_S = 10 * 60
# Reading a restart back when `fly machine restart` gave no answer (module doc).
RESTART_POLL_S = 10
# A Machine still `started`, with no event newer than before the request, this
# long after flyctl returned never began the restart.
RESTART_NO_SHOW_S = 120
# The longest a real restart may take: the 300 s kill_timeout a draining seat may
# use (operator/templates/fly.toml.template) plus 180 s for the boot.
RESTART_SETTLE_S = 480
# A restart passes through `stopped` for seconds (15 s on 2026-11-19, from stopped
# to started); one held this long, past the deadline, is not starting: four times
# that, and six reads.
RESTART_STOPPED_GRACE_S = 60
GONE_STATES = frozenset({"failed", "destroyed"})
# Outcomes in which no restart reached the Machine, and how the record names each.
UNSENT = {"not_sent": "restart not sent", "not_happened": "restart did not happen"}
LEFT_TO_READBACK = ", so it was left to the heartbeat read-back"


@dataclass(frozen=True)
class Restart:
    """How a restart ended: restarted | not_sent | not_happened | unknown | down. ``words`` is empty on rc=0."""

    outcome: str
    words: str


@dataclass(frozen=True)
class RolledBack:
    """What a rollback did: its words, whether it left the seat down, and whether a restart ran."""

    words: str
    down: bool
    restarted: bool


# -- which seats -------------------------------------------------------------------
@lru_cache(maxsize=4)
def _capabilities(repo: str) -> ModuleType:
    path = Path(repo) / CAPABILITIES
    spec = importlib.util.spec_from_file_location("smd_capabilities", path)
    if spec is None or spec.loader is None:
        raise ToolingError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def authoring_seats(repo: Path, seats: list[str]) -> set[str]:
    """The seats whose repo customer.yaml authors at least one job lane.

    An unreadable customer.yaml is a tooling error, never "authors nothing":
    cannot evaluate must not read as a seat to leave behind (or to touch).
    """
    caps = _capabilities(str(repo))
    out: set[str] = set()
    for seat in seats:
        path = repo / "operator" / "customers" / ids.slug(seat) / "customer.yaml"
        doc = caps.read(path)
        if doc is None:
            raise ToolingError(f"cannot read {path}, so cannot tell whether {seat} authors a job lane")
        if caps.authored_lanes(doc):
            out.add(seat)
    return out


def authoring_rings(rings: list[Ring], authoring: set[str]) -> list[Ring]:
    """The rings with every seat that authors no lane removed (an emptied ring is skipped by the tick)."""
    # The twins ride along (ADR 0107): a client seat's bundle waits for its twin
    # exactly as its image does.
    return [
        Ring(r.number, [s for s in r.seats if s in authoring], r.max_parallel, r.bake_minutes, dict(r.twins))
        for r in rings
    ]


# -- what a seat reports ---------------------------------------------------------------
def reported(row: dict[str, Any] | None, name: str = JOBS) -> str | None:
    """The version a fleet_status row says the seat runs bundle ``name`` at, or None."""
    raw = (row or {}).get("bundles_json")
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        return None
    value = doc.get(name) if isinstance(doc, dict) else None
    return value if isinstance(value, str) else None


def judge_bundle_row(row: dict[str, Any] | None, since: Any, name: str, version: str) -> Check | None:
    """Pass on a fresh beat reporting ``version``; otherwise keep waiting (a boot can report late)."""
    if not row or not smoke._fresh(row, since):  # the one freshness rule every judge uses
        return None
    if reported(row, name) == version:
        return Check("pass", f"heartbeat at {row.get('last_heartbeat_ts')} reports bundle {name} {version}")
    return None


def deferrals(
    release: dict[str, Any],
    seats: list[str],
    status: dict[str, dict[str, Any]],
    image_sha_of: Callable[[str | None], str | None],
    at_or_after: Callable[[str, str], bool],
    base_of: Callable[[str], str | None],
) -> dict[str, str]:
    """The seats that may not take this bundle release yet (ordering.deferrals, shared with skills)."""
    name = release.get("artifact_name") or JOBS
    return ordering.deferrals(
        f"bundle {name}",
        release.get("ss_console_sha"),
        release.get("artifact_version"),
        seats,
        status,
        lambda row: reported(row, name),
        image_sha_of,
        at_or_after,
        base_of,
    )


# -- one seat --------------------------------------------------------------------------
def load_manifest(r2: R2, name: str, version: str) -> tuple[dict[str, Any], str, str]:
    """The manifest CI published, its key and sha256. Refused unless its tarball is there too.

    Shared by this step and the image step's pointer write (bundle_align.py).
    """
    key = bundle_manifest_key(name, version)
    raw = r2.get_bytes(key)
    if raw is None:
        raise ToolingError(f"no bundle manifest at {key} (has CI published {name} {version}?)")
    manifest = json.loads(raw)
    if manifest.get("name") != name or manifest.get("version") != version:
        raise ToolingError(f"{key} names {manifest.get('name')!r} {manifest.get('version')!r}, not {name} {version}")
    if not isinstance(manifest.get("sha256"), str) or len(manifest["sha256"]) != 64:
        raise ToolingError(f"{key} carries no tarball sha256")
    if r2.head(bundle_tarball_key(name, version)) is None:
        raise ToolingError(f"{key} is published but its tarball is not")
    return manifest, key, hashlib.sha256(raw).hexdigest()


class BundleRollout:
    def __init__(
        self,
        store: FleetStore,
        r2: R2,
        fly: Fly,
        gate: BusyGate,
        log: Callable[[str], None],
        sleep: Callable[[float], None],
        clock: Callable[[], float],
        max_idle_wait: float = 1800.0,
        holder: str | None = None,
    ) -> None:
        self.store, self.r2, self.fly, self.gate = store, r2, fly, gate
        self.log, self.sleep, self.clock, self.max_idle_wait = log, sleep, clock, max_idle_wait
        # The holder of each seat's lease (waves.SeatLease, taken by the caller with
        # this same holder). None: nothing to renew (a test, or a dry run).
        self.holder = holder

    def _mark(self, rid: str, state: str) -> None:
        """Best effort: the deploying/smoking row only holds boot-shaped pages; it never decides an outcome."""
        try:
            self.store.update_rollout(rid, state)
        except ToolingError as exc:
            self.log(f"could not record rollout state {state} ({exc}); carrying on")

    def _renew(self, seat: str) -> None:
        """Renew the seat's lease (2 h TTL, d1.LEASE_TTL): the urgent path's worst case runs past it.

        Best effort, as the status rows are: a lost or unrenewable lease is
        logged, and the step finishes what it began on the seat.
        """
        if self.holder is None:
            return
        try:
            kept = self.store.renew_lease(self.holder, seat_lease_name(seat))
        except ToolingError as exc:
            self.log(f"{seat}: could not renew the seat lease ({exc}); carrying on")
            return
        if not kept:
            self.log(f"{seat}: the seat lease is no longer ours; finishing what this step began on the seat")

    def load_manifest(self, name: str, version: str) -> tuple[dict[str, Any], str, str]:
        return load_manifest(self.r2, name, version)

    def run(self, seat: str, ring: int, release: dict[str, Any], manifest: dict[str, Any], key: str, sha: str,
            dry_run: bool) -> SeatOutcome:  # fmt: skip
        rid = ids.new_id("ro")
        if not dry_run:
            self.store.start_rollout(
                {"rollout_id": rid, "release_id": release["release_id"], "customer_slug": seat, "ring": ring}
            )
        try:
            out = self._run(seat, rid, manifest, key, sha, dry_run)
        except ToolingError as exc:
            out = SeatOutcome(seat, "tooling_error", str(exc))
        self.log(f"{seat}: {out.state.upper()} {out.detail}")
        if not dry_run:
            self.store.update_rollout(rid, out.state, detail=out.detail[:2000])
        return out

    def _run(self, seat: str, rid: str, manifest: dict[str, Any], key: str, sha: str, dry_run: bool) -> SeatOutcome:
        name, version = manifest["name"], manifest["version"]
        pointer = bundle_pointer_key(seat, name)
        previous = self.r2.get_json(pointer)
        row = self.store.seat_status(seat) or {}
        if reported(row, name) == version:
            # The seat already runs it: its boot adopted the newest bundle of
            # its image's layout (layout-<n>/latest.json, ADR 0103) while the
            # pointer still named another layout's. A restart would change
            # nothing on the seat; writing the pointer makes the next boot agree.
            if dry_run:
                return SeatOutcome(
                    seat,
                    "pending",
                    f"dry run: already reports {name} {version}; would point it there without a restart",
                )
            return self._point_without_restart(seat, name, version, pointer, previous, key, sha)
        if dry_run:
            verdict = self.gate.probe(seat)
            if verdict.busy:
                return SeatOutcome(seat, "waiting_idle", "dry run: busy: " + "; ".join(verdict.reasons))
            return SeatOutcome(seat, "pending", f"dry run: idle; would point {name} at {version} and restart")
        caps, _where = image_capabilities(self.store, self.r2, seat)
        selftest = bool(caps and caps.get("selftest"))
        verdict = self.gate.wait_idle(seat, self.max_idle_wait, self.log)
        if verdict.busy:
            self.gate.clear(seat)
            return SeatOutcome(seat, "waiting_idle", "still busy after the idle wait: " + "; ".join(verdict.reasons))
        try:
            return self._swap(seat, rid, {"version": version, "manifest_key": key, "manifest_sha256": sha},
                              (name, pointer, previous), row, selftest)  # fmt: skip
        finally:
            # The step's end: after the read-back, or after the rollback and its
            # read-back. The supervisor keeps a live fleet marker across each
            # restart, so no lane claims or resumes until here (2026-11-22: a
            # marker cleared before the rollback let a job start that the
            # rollback restart then killed).
            self.gate.clear(seat)

    def _swap(self, seat: str, rid: str, target: dict[str, Any], where: tuple[str, str, dict[str, Any] | None],
              row: dict[str, Any], selftest: bool) -> SeatOutcome:  # fmt: skip
        """Point, restart, read back; roll back on a bad verdict. The caller holds the marker throughout."""
        name, pointer, previous = where
        version = target["version"]
        since = ids.utc_now()
        # restart-hold (workers/fleet-alerts) holds a booting seat's pages only
        # while the row is deploying or smoking, as the image step's rows are.
        self._mark(rid, "deploying")
        self.r2.put_json(pointer, target)
        restart = self._restart_and_settle(seat)
        if restart.outcome in UNSENT:
            said = self._restore(pointer, previous)
            return SeatOutcome(seat, "tooling_error", f"{UNSENT[restart.outcome]}: {restart.words}; {said}")
        if restart.outcome == "down":
            said = self._restore(pointer, previous)
            return SeatOutcome(seat, "rolled_back", f"SEAT DOWN: {restart.words}; {said}; not restarted again")
        self._mark(rid, "smoking")
        check = self._verify(seat, name, version, row, since, selftest)
        self._renew(seat)
        note = f"; {restart.words}" if restart.words else ""
        if check.cls == "pass":
            return SeatOutcome(seat, "done", check.detail + note)
        back = self._roll_back(seat, rid, name, pointer, previous)
        # No restart means the seat still runs the rejected bundle: a tooling_error
        # would let the next tick find it reporting the version and record done
        # with no self-test, opening the twin gate. So it halts instead.
        halts = back.down or not back.restarted or check.cls in ("fail", "timeout")
        lead = "SEAT DOWN: " if back.down else ""
        state = "rolled_back" if halts else "tooling_error"
        return SeatOutcome(seat, state, f"{lead}{check.cls}: {check.detail}{note}; {back.words}")

    # -- the restart, read back -------------------------------------------------------
    def _read_machine(self, app: str) -> dict[str, Any] | None:
        """The Machine, or None when Fly gave no clean answer (cannot evaluate, never "nothing happened")."""
        try:
            return self.fly.machine(app)
        except ToolingError:
            return None

    @staticmethod
    def _newest(machine: dict[str, Any]) -> int:
        return max((int(e.get("timestamp") or 0) for e in machine.get("events") or []), default=0)

    @staticmethod
    def _held(since: float | None, state: str | None, now: float) -> float | None:
        """When the Machine was first seen in the run of `stopped` reads ending now, or None."""
        if state != "stopped":
            return None
        return now if since is None else since

    def _restart_and_settle(self, seat: str) -> Restart:
        """``fly machine restart``, and when it gives no answer, what the Machine says happened.

        The Machine is read first, for its id and for what it looked like before;
        a failed read sends nothing (not_sent). Then the one command is issued and
        only read back: never a second restart.
        """
        app = ids.app_name(seat)
        try:
            before = self.fly.machine(app)
        except ToolingError as exc:
            return Restart("not_sent", f"could not read the Machine to restart it: {str(exc)[-300:]}")
        asked = self.clock()
        try:
            res = self.fly.restart(app, before["id"])
        except ToolingError as exc:
            said = f"fly machine restart gave no answer: {str(exc)[-300:]}"
        else:
            if res.ok:
                return Restart("restarted", "")
            said = f"fly {deploy_excerpt(res)}"
        self.log(f"{seat}: {said}; reading the Machine back")
        return self._settle(app, asked, before, said)

    def _settle(self, app: str, asked: float, before: dict[str, Any], said: str) -> Restart:
        """Poll the Machine until it says what the restart did (module doc, A RESTART THAT GAVE NO ANSWER).

        The deadline counts from whichever is later, the request plus
        RESTART_SETTLE_S or flyctl's return plus RESTART_NO_SHOW_S, so a flyctl
        that took longer than the allowance still gets a read of the boot.
        """
        returned = self.clock()
        deadline = max(asked + RESTART_SETTLE_S, returned + RESTART_NO_SHOW_S)
        baseline, was = self._newest(before), before["state"]
        moved = clean = False
        stopped_since: float | None = None
        while True:
            m = self._read_machine(app)
            now = self.clock()
            state = m["state"] if m else None
            stopped_since = self._held(stopped_since, state, now)
            if m is not None:
                clean = True
                moved = moved or state != was or self._newest(m) > baseline
                if state == "started" and moved:
                    return Restart("restarted", f"{said}; the Machine restarted anyway, started "
                                                f"{now - asked:.0f} s after the request")  # fmt: skip
                if state in GONE_STATES:
                    return self._down(state, now - asked, said)
            if not moved and now - returned >= RESTART_NO_SHOW_S:
                if clean:
                    return Restart("not_happened", f"Machine stayed {was} for {RESTART_NO_SHOW_S} s with no "
                                                   f"new event ({said})")  # fmt: skip
                return Restart("unknown", f"{said}; the Machine could not be read back{LEFT_TO_READBACK}")
            if moved and now >= deadline:
                if stopped_since is not None and now - stopped_since >= RESTART_STOPPED_GRACE_S:
                    return self._down("stopped", now - asked, said)
                if state != "stopped":
                    return Restart("unknown", f"{said}; the Machine was {state or 'unreadable'} "
                                              f"{now - asked:.0f} s after the request{LEFT_TO_READBACK}")  # fmt: skip
            self.sleep(RESTART_POLL_S)

    @staticmethod
    def _down(state: str, elapsed: float, said: str) -> Restart:
        return Restart("down", f"Machine state={state} {elapsed:.0f} s after the restart request ({said})")

    def _at_rest(self, app: str) -> dict[str, Any] | None:
        """The Machine once it is started, gone, or `stopped` for the grace period; bounded by RESTART_SETTLE_S.

        Returns the last read (still in transition at the bound, or None when it
        could not be read), so the rollback never restarts on top of a restart.
        """
        deadline = self.clock() + RESTART_SETTLE_S
        stopped_since: float | None = None
        while True:
            m = self._read_machine(app)
            now = self.clock()
            state = m["state"] if m else None
            stopped_since = self._held(stopped_since, state, now)
            held = stopped_since is not None and now - stopped_since >= RESTART_STOPPED_GRACE_S
            if state == "started" or state in GONE_STATES or held or now >= deadline:
                return m
            self.sleep(RESTART_POLL_S)

    def _point_without_restart(self, seat: str, name: str, version: str, pointer: str,
                               previous: dict[str, Any] | None, key: str, sha: str) -> SeatOutcome:  # fmt: skip
        """Write the pointer at what the seat already runs; done once a fresh beat still reports it.

        No busy gate (nothing restarts) and no self-test (no new boot to judge).
        A seat that stops reporting the version gets its previous pointer back,
        still without a restart: what it runs was never this step's doing.
        """
        since = ids.utc_now()
        self.r2.put_json(pointer, {"version": version, "manifest_key": key, "manifest_sha256": sha})
        check = smoke.read_back(
            self.store, seat, lambda r: judge_bundle_row(r, since, name, version),
            BUNDLE_READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        if check.cls == "pass":
            return SeatOutcome(
                seat, "done", f"already ran {name} {version}; pointer written, no restart; {check.detail}"
            )
        said = self._restore(pointer, previous)
        state = "rolled_back" if check.cls in ("fail", "timeout") else "tooling_error"
        return SeatOutcome(seat, state, f"{check.cls}: {check.detail}; {said}; the seat was not restarted")

    def _verify(self, seat: str, name: str, version: str, row: dict[str, Any], since: Any, selftest: bool) -> Check:
        got = smoke.read_back(
            self.store, seat, lambda r: judge_bundle_row(r, since, name, version),
            BUNDLE_READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        if got.cls != "pass" or not selftest:
            return got
        image = row.get("image_ref") or ""
        target = {"image_ref": image, "image_digest": ids.digest_in_ref(image)}
        test = smoke.read_back(
            self.store, seat, lambda r: smoke.judge_selftest_row(r, since, target, None),
            smoke.SELFTEST_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        if test.cls == "timeout":
            return Check("tooling_error", f"{got.detail}; self-test: no verdict ({test.detail})")
        return Check(test.cls, f"{got.detail}; {test.detail}")

    def _restore(self, pointer: str, previous: dict[str, Any] | None) -> str:
        """Put the previous pointer back (or remove it when there was none), and say which."""
        if previous:
            self.r2.put_json(pointer, previous)
        else:
            self.r2.delete(pointer)
        back = (previous or {}).get("version")
        return f"pointer restored to {back}" if back else "pointer removed (there was none)"

    def _roll_back(self, seat: str, rid: str, name: str, pointer: str, previous: dict[str, Any] | None) -> RolledBack:
        """Restore the pointer, then restart so the seat runs what it ran.

        The Machine is read first and waited on until it is at rest
        (:meth:`_at_rest`), so a restart still in flight is never restarted on
        top of. Started: behind the busy gate. Stopped: restarted without the gate
        (its probe would raise, and those words would hide these). Gone: SEAT
        DOWN. Unreadable, or still in transition at the bound: no restart.
        """
        said = self._restore(pointer, previous)
        machine = self._at_rest(ids.app_name(seat))
        if machine is None:
            return RolledBack(f"{said}; the Machine could not be read, so no rollback restart: its next boot takes it",
                              False, False)  # fmt: skip
        state = machine["state"]
        if state in GONE_STATES:
            return RolledBack(f"{said}; the Machine is {state}, so no rollback restart", True, False)
        if state not in ("started", "stopped"):
            return RolledBack(f"{said}; the Machine is still {state} after {RESTART_SETTLE_S} s, so no rollback "
                              "restart on top of the one in flight: its next boot takes it", False, False)  # fmt: skip
        if state == "started":
            # The caller's marker still stands and is renewed here; the caller
            # clears it once this read-back has decided.
            verdict = self.gate.wait_idle(seat, self.max_idle_wait, self.log)
            if verdict.busy:
                why = "; ".join(verdict.reasons)
                return RolledBack(f"{said}; restart deferred, the seat is busy ({why}): its next boot takes it",
                                  False, False)  # fmt: skip
        self._renew(seat)
        self._mark(rid, "smoking")
        since = ids.utc_now()
        restart = self._restart_and_settle(seat)
        if restart.outcome in UNSENT:
            # A stopped Machine that the restart did not reach stays stopped: the seat is down.
            stays = "; the Machine stays stopped" if state == "stopped" else ": its next boot takes it"
            return RolledBack(f"{said}; the rollback {UNSENT[restart.outcome]} ({restart.words}){stays}",
                              state == "stopped", False)  # fmt: skip
        if restart.outcome == "down":
            return RolledBack(f"{said}; the rollback restart left it down: {restart.words}", True, True)
        want = (previous or {}).get("version") or "image"
        again = smoke.read_back(
            self.store, seat, lambda r: judge_bundle_row(r, since, name, want),
            ROLLBACK_READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        how = f"restarted ({restart.words})" if restart.words else "restarted"
        return RolledBack(f"{said}; {how}; read-back of {name} {want}: {again.cls}", False, True)
