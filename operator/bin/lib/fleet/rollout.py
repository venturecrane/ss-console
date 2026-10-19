"""One seat's image release, start to finish.

  preflight -> busy gate (quiesce + wait) -> record from-image / from-toml ->
  volume snapshot -> fly deploy --image -> boot smoke -> heartbeat read-back ->
  done | (fail) roll back to from-image + from-toml, re-smoke, halt |
  (tooling_error / timeout) halt, touch nothing

Every transition is written to fleet_rollouts before the next step starts, so a
laptop that dies mid-rollout leaves a row saying exactly where it stopped.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from . import ids, smoke
from .busy import BusyGate
from .d1 import FleetStore
from .fly import Fly, classify_deploy
from .preflight import PreflightChecker
from .runner import ToolingError
from .smoke import Check, SmokeRunner

# Outcome of one seat: whether the rollout continues past it.
HALTING = frozenset({"rolled_back", "failed", "tooling_error"})


@dataclass
class SeatOutcome:
    seat: str
    state: str
    detail: str

    @property
    def halts(self) -> bool:
        return self.state in HALTING


@dataclass
class ImagePlan:
    release: dict[str, Any]
    hermes_sha: str | None
    force_mid_job: bool = False
    accept_lane_inputs: bool = False
    max_idle_wait: float = 1800.0
    dry_run: bool = False


class ImageRollout:
    def __init__(
        self,
        store: FleetStore,
        fly: Fly,
        gate: BusyGate,
        preflight: PreflightChecker,
        smoker: SmokeRunner,
        render: Callable[[str], str],
        log: Callable[[str], None],
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.store, self.fly, self.gate, self.preflight, self.smoker = store, fly, gate, preflight, smoker
        self.render, self.log, self.sleep, self.clock = render, log, sleep, clock

    # -- helpers ---------------------------------------------------------------
    def _current_digest(self, seat: str) -> str | None:
        row = self.store.seat_status(seat) or {}
        ref = row.get("image_ref")
        if not ref:
            return None
        found = ids.digest_in_ref(ref)
        if found:
            return found
        rel = self.store.release_for_image(ref)
        return rel.get("image_digest") if rel else None

    def _deploy(self, seat: str, image: str, toml_text: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fly.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(toml_text)
            return classify_deploy(self.fly.deploy(ids.app_name(seat), image, path))

    def _verify(self, seat: str, plan: ImagePlan, since_iso: str) -> Check:
        check = self.smoker.run(seat, plan.hermes_sha)
        if check.cls != "pass":
            return check
        since = ids.parse_iso(since_iso)
        assert since is not None
        return smoke.read_back(
            self.store, seat, lambda row: smoke.judge_image_row(row, since, plan.release),
            smoke.READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip

    # -- the seat ------------------------------------------------------------------
    def run(self, seat: str, ring: int, plan: ImagePlan) -> SeatOutcome:
        rid = ids.new_id("ro")
        rel = plan.release
        if not plan.dry_run:
            self.store.start_rollout(
                {
                    "rollout_id": rid,
                    "release_id": rel["release_id"],
                    "customer_slug": seat,
                    "ring": ring,
                    "to_image": rel["image_ref"],
                }
            )
        try:
            return self._run(seat, rid, plan)
        except ToolingError as exc:
            if not plan.dry_run:
                self.gate.clear(seat)
            return self._finish(rid, plan, SeatOutcome(seat, "tooling_error", str(exc)))

    def _finish(self, rid: str, plan: ImagePlan, out: SeatOutcome, **fields: Any) -> SeatOutcome:
        self.log(f"{out.seat}: {out.state.upper()} {out.detail}")
        if not plan.dry_run:
            self.store.update_rollout(rid, out.state, detail=out.detail[:2000], **fields)
        return out

    def _run(self, seat: str, rid: str, plan: ImagePlan) -> SeatOutcome:
        toml_text = self.render(seat)
        pre = self.preflight.run(seat, plan.release, toml_text, self._current_digest(seat), plan.accept_lane_inputs)
        for note in pre.notes:
            self.log(f"{seat}: note: {note}")
        if not pre.ok:
            return self._finish(rid, plan, SeatOutcome(seat, "failed", "preflight refused: " + "; ".join(pre.problems)))
        idle = self._gate(seat, rid, plan)
        if idle is not None:
            return idle
        if plan.dry_run:
            return SeatOutcome(seat, "pending", "dry run: preflight passed and the seat is idle; would deploy now")
        return self._deploy_and_verify(seat, rid, plan, toml_text)

    def _gate(self, seat: str, rid: str, plan: ImagePlan) -> SeatOutcome | None:
        if plan.force_mid_job:
            self.log(f"{seat}: WARNING --force-mid-job: deploying WITHOUT the busy gate; a running job will be stopped")
            return None
        if plan.dry_run:
            verdict = self.gate.probe(seat)
            return (
                SeatOutcome(seat, "waiting_idle", "dry run: busy: " + "; ".join(verdict.reasons))
                if verdict.busy
                else None
            )
        verdict = self.gate.wait_idle(seat, plan.max_idle_wait, self.log)
        if verdict.busy:
            self.gate.clear(seat)
            return self._finish(
                rid,
                plan,
                SeatOutcome(seat, "waiting_idle", "still busy after the idle wait: " + "; ".join(verdict.reasons)),
            )
        return None

    def _deploy_and_verify(self, seat: str, rid: str, plan: ImagePlan, toml_text: str) -> SeatOutcome:
        app = ids.app_name(seat)
        from_image = self.fly.machine_image(app)
        try:
            from_toml = self.fly.saved_config(app)
        except ToolingError as exc:
            self.log(f"{seat}: fly config save failed ({exc}); recording the current render as the rollback toml")
            from_toml = toml_text
        snap = self.fly.snapshot(app, self.fly.volume_id(app))
        self.store.update_rollout(rid, "deploying", from_image=from_image, from_toml=from_toml, snapshot_id=snap)
        since = ids.iso(ids.utc_now())
        try:
            deployed = self._deploy(seat, plan.release["image_ref"], toml_text)
            if deployed == "tooling_error":
                return self._finish(
                    rid,
                    plan,
                    SeatOutcome(
                        seat,
                        "tooling_error",
                        "fly deploy failed without saying the release was unhealthy; the seat was not rolled back",
                    ),
                )
            self.store.update_rollout(rid, "smoking")
            check = (
                Check("fail", "fly deploy reported the new release would not come up")
                if deployed == "fail"
                else self._verify(seat, plan, since)
            )
        finally:
            # The marker lives on the volume (/run/smd-medchron is a bind mount),
            # so the new boot inherits it until it expires: clear it now.
            self.gate.clear(seat)
        if check.cls == "pass":
            return self._finish(rid, plan, SeatOutcome(seat, "done", check.detail), smoke_class="pass")
        if check.cls != "fail":
            return self._finish(
                rid,
                plan,
                SeatOutcome(seat, "tooling_error", f"{check.cls}: {check.detail} (no rollback: not a seat failure)"),
                smoke_class=check.cls,
            )
        return self._rollback(seat, rid, plan, from_image, from_toml, check, snap)

    def _rollback(
        self, seat: str, rid: str, plan: ImagePlan, from_image: str, from_toml: str, check: Check, snap: str
    ) -> SeatOutcome:
        if not int(plan.release.get("volume_forward_compatible", 1) or 0):
            return self._finish(
                rid,
                plan,
                SeatOutcome(
                    seat,
                    "failed",
                    f"smoke FAIL: {check.detail}. NOT rolled back: release is not volume-forward-compatible; volume snapshot {snap}. Page the Captain.",
                ),
                smoke_class="fail",
            )
        self.log(f"{seat}: smoke FAIL ({check.detail}); rolling back to {from_image}")
        back = self._deploy(seat, from_image, from_toml)
        again = self.smoker.run(seat, None) if back == "ok" else Check("fail", f"rollback deploy {back}")
        detail = f"smoke FAIL: {check.detail}. Rolled back to {from_image}; re-smoke {again.cls}: {again.detail}"
        return self._finish(rid, plan, SeatOutcome(seat, "rolled_back", detail), smoke_class="fail")
