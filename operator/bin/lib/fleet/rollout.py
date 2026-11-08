"""One seat's image release, start to finish.

  preflight -> busy gate (quiesce + wait) -> record from-image / from-toml ->
  volume snapshot -> jobs bundle pointer written and read back (a lane seat) ->
  fly deploy --image -> boot smoke -> heartbeat read-back ->
  done | (fail) restore the pointer, roll back to from-image + from-toml,
  re-smoke, halt | (tooling_error / timeout) halt, touch nothing

A seat is never put on an image of a lower volume epoch (volume_epoch.py,
ADR 0103): a forward deploy that would be refused before anything is touched,
and a rollback that would be is not made (the seat halts on the failing image,
its volume snapshot kept), exactly as for a release built
not-volume-forward-compatible.

The pointer comes before the deploy because a seat reads it only at boot: the
first boot on the new image must mount the bundle built for it (bundle_align.py,
the 2026-11-07 window). A pointer that cannot be written stops the seat before
its deploy.

Every transition is written to fleet_rollouts before the next step starts, so a
laptop that dies mid-rollout leaves a row saying exactly where it stopped.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from . import ids, smoke, volume_epoch
from .busy import BusyGate
from .d1 import FleetStore
from .fly import Fly, classify_deploy, deploy_excerpt
from .ledger import VerifyLedger
from .preflight import PreflightChecker
from .runner import ToolingError
from .smoke import Check, SmokeRunner

if TYPE_CHECKING:  # bundle_align -> bundles -> rollout (SeatOutcome): types only here
    from .bundle_align import Alignment, BundleAlign

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
        align: BundleAlign | None = None,
    ) -> None:
        self.store, self.fly, self.gate, self.preflight, self.smoker = store, fly, gate, preflight, smoker
        self.render, self.log, self.sleep, self.clock = render, log, sleep, clock
        self.fleet_side = fleet_side
        self.ledger = ledger
        self.align = align

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

    def current_sha(self, seat: str) -> str | None:
        """The ss-console commit of the image the seat runs now (the tick asks, for a halted row)."""
        return self._current_sha(seat)

    def _current_sha(self, seat: str) -> str | None:
        """The ss-console commit the seat's running image was built from (its release row)."""
        ref = (self.store.seat_status(seat) or {}).get("image_ref")
        rel = self.store.release_for_image(ref) if ref else None
        sha = rel.get("ss_console_sha") if rel else None
        return str(sha) if sha else None

    def _epoch_of_image(self, image_ref: str | None) -> int:
        """The volume epoch of the image release ``image_ref`` names; unknown is 1 (volume_epoch.of)."""
        return volume_epoch.of(self.store.release_for_image(image_ref) if image_ref else None)

    def _rollback_refusal(self, release: dict[str, Any], from_image: str) -> str:
        """Why the seat must NOT be put back on ``from_image`` ("" when it may be).

        Two rules, either one halts with the seat left on the failing image and
        its snapshot kept: the release was built not-volume-forward-compatible,
        or the from-image's volume epoch is lower than the release's (ADR 0103:
        an older-layout image boots on a moved volume as if it were empty). The
        second covers what the flag cannot: a LATER release failing on a seat
        already moved. A from-image that cannot be looked up counts as epoch 1.
        """
        if not int(release.get("volume_forward_compatible", 1) or 0):
            return "release is not volume-forward-compatible"
        to_epoch = volume_epoch.of(release)
        if to_epoch == volume_epoch.BASE_EPOCH:
            return ""  # nothing is lower than the base epoch: no lookup needed
        try:
            back_epoch = self._epoch_of_image(from_image)
        except ToolingError as exc:
            return f"the from-image's volume epoch could not be read ({exc}), and the release is epoch {to_epoch}"
        if back_epoch < to_epoch:
            return f"the from-image is volume epoch {back_epoch}, older than this release's epoch {to_epoch}"
        return ""

    def _epoch_refusal(self, seat: str, release: dict[str, Any]) -> str:
        """Why a FORWARD deploy of ``release`` would put the seat on an older volume layout ("" when not)."""
        ref = (self.store.seat_status(seat) or {}).get("image_ref")
        now = self._epoch_of_image(str(ref) if ref else None)
        to_epoch = volume_epoch.of(release)
        if now > to_epoch:
            return f"the seat runs volume epoch {now}; this release is epoch {to_epoch}, an older volume layout"
        return ""

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
        pre = self.preflight.run(
            seat, plan.release, toml_text, self._current_digest(seat), plan.accept_lane_inputs, self._current_sha(seat)
        )
        for note in pre.notes:
            self.log(f"{seat}: note: {note}")
        if not pre.ok:
            return self._finish(rid, plan, SeatOutcome(seat, "failed", "preflight refused: " + "; ".join(pre.problems)))
        older = self._epoch_refusal(seat, plan.release)
        if older:
            return self._finish(rid, plan, SeatOutcome(seat, "failed", f"refused, nothing deployed: {older}"))
        al = self.align.prepare(seat, plan.release) if self.align else None
        if al is not None and al.wait:
            return self._finish(rid, plan, SeatOutcome(seat, "tooling_error", f"{al.wait}; nothing was deployed"))
        idle = self._gate(seat, rid, plan)
        if idle is not None:
            return idle
        if plan.dry_run:
            would = f"; {al.note}" if al else ""
            return SeatOutcome(
                seat, "pending", f"dry run: preflight passed and the seat is idle; would deploy now{would}"
            )
        return self._deploy_and_verify(seat, rid, plan, toml_text, al)

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

    def _point(self, seat: str, rid: str, plan: ImagePlan, al: Alignment | None) -> SeatOutcome | None:
        """Write the seat's bundle pointer before its deploy; an outcome when that failed (no deploy)."""
        if self.align is None or al is None:
            return None
        self.log(f"{seat}: {al.note}")
        try:
            self.align.point(al)
        except ToolingError as exc:
            undone = self._unpoint(seat, al)
            return self._finish(
                rid, plan,
                SeatOutcome(seat, "tooling_error", f"jobs bundle pointer not written ({exc}); {undone}; "
                            "the image was not deployed"),
            )  # fmt: skip
        return None

    def _unpoint(self, seat: str, al: Alignment | None) -> str:
        """Put the bundle pointer back as it was; never raises (what happened is the return)."""
        if self.align is None or al is None or al.target is None:
            return "bundle pointer untouched"
        try:
            said = self.align.restore(al)
        except ToolingError as exc:
            said = f"bundle pointer NOT restored ({exc}); it names {al.target['version']}"
        self.log(f"{seat}: {said}")
        return said

    def _deploy_and_verify(
        self, seat: str, rid: str, plan: ImagePlan, toml_text: str, al: Alignment | None = None
    ) -> SeatOutcome:
        app = ids.app_name(seat)
        from_image = self.fly.machine_image(app)
        try:
            from_toml = self.fly.saved_config(app)
        except ToolingError as exc:
            self.log(f"{seat}: fly config save failed ({exc}); recording the current render as the rollback toml")
            from_toml = toml_text
        snap = self.fly.snapshot(app, self.fly.volume_id(app))
        self.store.update_rollout(rid, "deploying", from_image=from_image, from_toml=from_toml, snapshot_id=snap)
        refused = self._point(seat, rid, plan, al)
        if refused is not None:
            return refused
        since = ids.iso(ids.utc_now())
        try:
            deployed, said = self._deploy_saying(seat, plan.release["image_ref"], toml_text)
            if deployed == "tooling_error":
                # Most likely the seat still runs the from-image, so it gets its
                # old pointer back; if the new image did land, its self-test
                # refuses the older bundle (jobs-bundle-built-for-image).
                undone = self._unpoint(seat, al)
                return self._finish(
                    rid,
                    plan,
                    SeatOutcome(
                        seat,
                        "tooling_error",
                        "fly deploy failed twice without saying the release was unhealthy; the seat was not "
                        f"rolled back ({undone}). Fly said: {said}",
                    ),
                )
            self.store.update_rollout(rid, "smoking")
            check = (
                Check("fail", "fly deploy reported the new release would not come up")
                if deployed == "fail"
                else self._verify(seat, plan, since)
            )
        finally:
            # The marker lives on the volume (/run/smd-jobs is a bind mount),
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
        return self._rollback(seat, rid, plan, (from_image, from_toml), check, snap, al)

    def _rollback(
        self, seat: str, rid: str, plan: ImagePlan, back_to: tuple[str, str], check: Check, snap: str,
        al: Alignment | None,
    ) -> SeatOutcome:  # fmt: skip
        from_image, from_toml = back_to
        refused = self._rollback_refusal(plan.release, from_image)
        if refused:
            return self._finish(
                rid,
                plan,
                SeatOutcome(
                    seat,
                    "failed",
                    f"smoke FAIL: {check.detail}. NOT rolled back: {refused}; volume snapshot {snap}. Page the Captain.",
                ),
                smoke_class="fail",
            )
        self.log(f"{seat}: smoke FAIL ({check.detail}); rolling back to {from_image}")
        # Before the redeploy: the from-image's boot must mount the bundle it ran.
        undone = self._unpoint(seat, al)
        since = ids.utc_now()
        back = self._deploy(seat, from_image, from_toml)
        again = (
            self._resmoke(seat, plan, from_image, since) if back == "ok" else Check("fail", f"rollback deploy {back}")
        )
        detail = (
            f"smoke FAIL: {check.detail}. Rolled back to {from_image} ({undone}); re-smoke {again.cls}: {again.detail}"
        )
        return self._finish(rid, plan, SeatOutcome(seat, "rolled_back", detail), smoke_class="fail")
