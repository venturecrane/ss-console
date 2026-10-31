"""One seat's `jobs` bundle release, and which seats get one at all (ADR 0100 W3).

WHAT A BUNDLE IS. The job runner venv (/opt/medchron, with its pricing table)
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
restarted) -> write the pointer -> ``fly machine restart`` -> read back a fresh
heartbeat reporting the version (and, on an image that carries it, a passing
self-test from the new boot). Anything else restores the previous pointer (or
removes it when there was none), restarts again behind the busy gate so the
seat runs what it ran, and records the seat rolled_back (a verdict) or
tooling_error (no verdict). So the pointer is left at the new version only on
done, and a retry always starts from what the seat really ran.

ORDER. A bundle published at S reaches a seat only once that seat's image
descends from base(S) (ordering.py), as skills do.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from . import ids, ordering, smoke
from .busy import BusyGate
from .d1 import FleetStore
from .fly import Fly
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
    return [Ring(r.number, [s for s in r.seats if s in authoring], r.max_parallel, r.bake_minutes) for r in rings]


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
    ) -> None:
        self.store, self.r2, self.fly, self.gate = store, r2, fly, gate
        self.log, self.sleep, self.clock, self.max_idle_wait = log, sleep, clock, max_idle_wait

    def load_manifest(self, name: str, version: str) -> tuple[dict[str, Any], str, str]:
        """The manifest CI published, its key and sha256. Refused unless its tarball is there too."""
        key = bundle_manifest_key(name, version)
        raw = self.r2.get_bytes(key)
        if raw is None:
            raise ToolingError(f"no bundle manifest at {key} (has CI published {name} {version}?)")
        manifest = json.loads(raw)
        if manifest.get("name") != name or manifest.get("version") != version:
            raise ToolingError(
                f"{key} names {manifest.get('name')!r} {manifest.get('version')!r}, not {name} {version}"
            )
        if not isinstance(manifest.get("sha256"), str) or len(manifest["sha256"]) != 64:
            raise ToolingError(f"{key} carries no tarball sha256")
        if self.r2.head(bundle_tarball_key(name, version)) is None:
            raise ToolingError(f"{key} is published but its tarball is not")
        return manifest, key, hashlib.sha256(raw).hexdigest()

    def run(self, seat: str, ring: int, release: dict[str, Any], manifest: dict[str, Any], key: str, sha: str,
            dry_run: bool) -> SeatOutcome:  # fmt: skip
        rid = ids.new_id("ro")
        if not dry_run:
            self.store.start_rollout(
                {"rollout_id": rid, "release_id": release["release_id"], "customer_slug": seat, "ring": ring}
            )
        try:
            out = self._run(seat, manifest, key, sha, dry_run)
        except ToolingError as exc:
            out = SeatOutcome(seat, "tooling_error", str(exc))
        self.log(f"{seat}: {out.state.upper()} {out.detail}")
        if not dry_run:
            self.store.update_rollout(rid, out.state, detail=out.detail[:2000])
        return out

    def _run(self, seat: str, manifest: dict[str, Any], key: str, sha: str, dry_run: bool) -> SeatOutcome:
        name, version = manifest["name"], manifest["version"]
        pointer = bundle_pointer_key(seat, name)
        previous = self.r2.get_json(pointer)
        if dry_run:
            verdict = self.gate.probe(seat)
            if verdict.busy:
                return SeatOutcome(seat, "waiting_idle", "dry run: busy: " + "; ".join(verdict.reasons))
            return SeatOutcome(seat, "pending", f"dry run: idle; would point {name} at {version} and restart")
        row = self.store.seat_status(seat) or {}
        caps, _where = image_capabilities(self.store, self.r2, seat)
        selftest = bool(caps and caps.get("selftest"))
        verdict = self.gate.wait_idle(seat, self.max_idle_wait, self.log)
        if verdict.busy:
            self.gate.clear(seat)
            return SeatOutcome(seat, "waiting_idle", "still busy after the idle wait: " + "; ".join(verdict.reasons))
        since = ids.utc_now()
        self.r2.put_json(pointer, {"version": version, "manifest_key": key, "manifest_sha256": sha})
        try:
            restarted = self.fly.restart(ids.app_name(seat))
            if not restarted.ok:
                self._repoint(pointer, previous)
                return SeatOutcome(
                    seat, "tooling_error",
                    f"fly machine restart failed (rc={restarted.rc}); the pointer is restored and the seat "
                    "was not restarted",
                )  # fmt: skip
            check = self._verify(seat, name, version, row, since, selftest)
        finally:
            self.gate.clear(seat)
        if check.cls == "pass":
            return SeatOutcome(seat, "done", check.detail)
        state = "rolled_back" if check.cls in ("fail", "timeout") else "tooling_error"
        return SeatOutcome(
            seat, state, f"{check.cls}: {check.detail}; {self._roll_back(seat, name, pointer, previous)}"
        )

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

    def _repoint(self, pointer: str, previous: dict[str, Any] | None) -> None:
        if previous:
            self.r2.put_json(pointer, previous)
        else:
            self.r2.delete(pointer)

    def _roll_back(self, seat: str, name: str, pointer: str, previous: dict[str, Any] | None) -> str:
        """Restore the pointer, then restart behind the busy gate so the seat runs what it ran."""
        self._repoint(pointer, previous)
        back = (previous or {}).get("version")
        said = f"pointer restored to {back}" if back else "pointer removed (there was none)"
        verdict = self.gate.wait_idle(seat, self.max_idle_wait, self.log)
        if verdict.busy:
            self.gate.clear(seat)
            return f"{said}; restart deferred, the seat is busy ({'; '.join(verdict.reasons)}): its next boot takes it"
        since = ids.utc_now()
        try:
            res = self.fly.restart(ids.app_name(seat))
        finally:
            self.gate.clear(seat)
        if not res.ok:
            return f"{said}; the rollback restart failed (rc={res.rc}): its next boot takes it"
        want = back or "image"
        again = smoke.read_back(
            self.store, seat, lambda r: judge_bundle_row(r, since, name, want),
            ROLLBACK_READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        return f"{said}; restarted; read-back of {name} {want}: {again.cls}"
