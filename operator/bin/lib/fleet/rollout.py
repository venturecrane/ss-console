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
from datetime import datetime
from typing import Any, Callable

from . import ids, smoke
from .busy import BusyGate
from .d1 import FleetStore
from .fly import Fly, classify_deploy, deploy_excerpt
from .ledger import VerifyLedger
from .preflight import PreflightChecker
from .runner import ToolingError
from .smoke import Check, SmokeRunner

# Outcome of one seat: whether the rollout continues past it. Only a seat that
# showed the RELEASE is bad halts: it came up broken (rolled_back) or was refused
# or broken and left as is (failed). A tooling_error decided nothing about the
# release (Fly said no without saying why, or no verdict came back), so it stalls
# that one seat and never the rest: on 2026-10-29 a Fly deploy that failed with
# no reason, on smd-staging and then on brokery, froze every later seat, the
# client's included, for hours.
HALTING = frozenset({"rolled_back", "failed"})
# A seat that did not take the release and showed nothing wrong with it: the
# next tick tries it again, and it never holds the rings after it back
# (tick.plan_tick). The pager reads repeated tooling_error rows as stuck.
STALLED = frozenset({"tooling_error", "waiting_idle"})

# A `fly deploy` that fails with no seat-failure marker is tried once more after
# this pause before the seat is called a tooling_error. Re-deploying the same
# image is safe: nothing was rolled back, and the second call converges on it.
DEPLOY_RETRY_PAUSE_S = 60.0


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
    # Which verifier decides the seat (see _verify). "bash": the boot smoke over
    # ssh decides and the self-test's verdict, when one has arrived, is recorded
    # beside it. "selftest": the seat's own heartbeat self-test decides, after
    # the fleet-side checks, falling back to the boot smoke when no verdict
    # arrives. An image that does not carry the self-test is always "bash".
    verify: str = "bash"
    selftest_capable: bool = False
    # False (``--no-ssh-fallback``, and always in ``--tick``): the deploy path
    # never runs the ssh boot smoke. A self-test that gives no verdict is a
    # tooling_error (halt, page), and a rollback is re-verified by the self-test
    # on the rollback image. The busy gate's exec probe is not the boot smoke and
    # still runs: it is what keeps a running job from being restarted.
    ssh_fallback: bool = True


# The terminal states a verify-ledger row is written for: the ones a judge (or a
# refusal before anything was touched) decided. waiting_idle decided nothing.
LEDGER_STATES = frozenset({"done", "rolled_back", "failed", "tooling_error"})


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
        fleet_side: smoke.FleetSideChecks | None = None,
        ledger: VerifyLedger | None = None,
    ) -> None:
        self.store, self.fly, self.gate, self.preflight, self.smoker = store, fly, gate, preflight, smoker
        self.render, self.log, self.sleep, self.clock = render, log, sleep, clock
        self.fleet_side = fleet_side
        self.ledger = ledger

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
        return self._deploy_saying(seat, image, toml_text)[0]

    def _deploy_saying(self, seat: str, image: str, toml_text: str) -> tuple[str, str]:
        """``fly deploy``: its class, and what Fly said when it decided nothing.

        A tooling_error is tried once more after DEPLOY_RETRY_PAUSE_S. What Fly
        printed is kept, because before 2026-10-30 it was thrown away and three
        halts in one day could not say why.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fly.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(toml_text)
            res = self.fly.deploy(ids.app_name(seat), image, path)
            cls = classify_deploy(res)
            if cls != "tooling_error":
                return cls, ""
            first = deploy_excerpt(res)
            self.log(f"{seat}: fly deploy decided nothing ({first}); trying once more in {DEPLOY_RETRY_PAUSE_S:.0f}s")
            self.sleep(DEPLOY_RETRY_PAUSE_S)
            res = self.fly.deploy(ids.app_name(seat), image, path)
            cls = classify_deploy(res)
            return cls, (deploy_excerpt(res) if cls == "tooling_error" else "")

    def _verify(self, seat: str, plan: ImagePlan, since_iso: str) -> Check:
        since = ids.parse_iso(since_iso)
        assert since is not None
        if plan.verify == "selftest" and plan.selftest_capable and self.fleet_side is not None:
            return self._verify_selftest(seat, plan, since)
        check = self._verify_bash(seat, plan, since)
        return Check(check.cls, f"{check.detail} | {self._selftest_note(seat, plan, since)}")

    def _verify_bash(self, seat: str, plan: ImagePlan, since: datetime) -> Check:
        check = self.smoker.run(seat, plan.hermes_sha)
        if check.cls != "pass":
            return check
        return smoke.read_back(
            self.store, seat, lambda row: smoke.judge_image_row(row, since, plan.release),
            smoke.READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip

    def _selftest_note(self, seat: str, plan: ImagePlan, since: datetime) -> str:
        """The self-test's verdict as of now, recorded beside the bash verdict. No wait."""
        if not plan.selftest_capable:
            return "selftest: the image does not carry it"
        try:
            row = self.store.seat_status(seat)
        except ToolingError as exc:
            return f"selftest: unread ({exc})"
        return smoke.selftest_note(row, since, plan.release, plan.hermes_sha)

    def _verify_selftest(self, seat: str, plan: ImagePlan, since: datetime) -> Check:
        assert self.fleet_side is not None
        side = self.fleet_side.run(seat)
        if side.cls != "pass":
            return side
        got = smoke.read_back(
            self.store, seat, lambda row: smoke.judge_selftest_row(row, since, plan.release, plan.hermes_sha),
            smoke.SELFTEST_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        if got.cls == "timeout":
            if not plan.ssh_fallback:
                # No second instrument in this mode, so no verdict is no
                # decision: halt and page, never guess, never roll back.
                return Check(
                    "tooling_error",
                    f"selftest: no verdict ({got.detail}); no ssh fallback in this mode, so nothing was decided",
                )
            # The instrument must not grade itself into silence: no verdict
            # from the seat means the boot smoke decides, and both are recorded.
            bash = self._verify_bash(seat, plan, since)
            return Check(bash.cls, f"selftest: no verdict ({got.detail}); boot smoke fallback: {bash.detail}")
        if got.cls != "pass":
            return got
        image = smoke.read_back(
            self.store, seat, lambda row: smoke.judge_image_row(row, since, plan.release),
            smoke.READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        return Check(image.cls, f"{side.detail}; {got.detail}; {image.detail}")

    def _resmoke(self, seat: str, plan: ImagePlan, from_image: str, since: datetime) -> Check:
        """Verify the rollback. The boot smoke, unless this mode has no ssh fallback:
        then the self-test the rollback image runs at boot, read off the heartbeat."""
        if plan.ssh_fallback or not plan.selftest_capable or self.fleet_side is None:
            return self.smoker.run(seat, None)
        side = self.fleet_side.run(seat)
        if side.cls != "pass":
            return side
        target = {"image_ref": from_image, "image_digest": ids.digest_in_ref(from_image)}
        got = smoke.read_back(
            self.store, seat, lambda row: smoke.judge_selftest_row(row, since, target, None),
            smoke.SELFTEST_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        return Check(got.cls, f"self-test on the rollback image: {got.detail}")

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
            detail = out.detail[:1800]
            note = self._ledger_note(rid, plan, out)
            if note:
                self.log(f"{out.seat}: {note}")
                detail = f"{detail} | {note}"
            self.store.update_rollout(rid, out.state, detail=detail, **fields)
        return out

    def _ledger_note(self, rid: str, plan: ImagePlan, out: SeatOutcome) -> str:
        """One verify-ledger row for a decided seat; a failure is a note, never a halt."""
        if self.ledger is None or out.state not in LEDGER_STATES:
            return ""
        return self.ledger.record(out.seat, plan.release["release_id"], rid, out.state, out.detail)

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
            deployed, said = self._deploy_saying(seat, plan.release["image_ref"], toml_text)
            if deployed == "tooling_error":
                return self._finish(
                    rid,
                    plan,
                    SeatOutcome(
                        seat,
                        "tooling_error",
                        "fly deploy failed twice without saying the release was unhealthy; the seat was not "
                        f"rolled back. Fly said: {said}",
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
        since = ids.utc_now()
        back = self._deploy(seat, from_image, from_toml)
        again = (
            self._resmoke(seat, plan, from_image, since) if back == "ok" else Check("fail", f"rollback deploy {back}")
        )
        detail = f"smoke FAIL: {check.detail}. Rolled back to {from_image}; re-smoke {again.cls}: {again.detail}"
        return self._finish(rid, plan, SeatOutcome(seat, "rolled_back", detail), smoke_class="fail")
