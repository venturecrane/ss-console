"""`operator/bin/fleet` entry point. See the package docstring for the contract.

  fleet build [--notes TEXT] [--declare-secret NAME ...] [--not-volume-forward-compatible]
  fleet release <release-id> [--only SLUG] [--parallel N] [--force-mid-job]
                [--dry-run] [--accept-lane-inputs] [--max-idle-wait SECONDS]
                [--verify bash|selftest] [--no-ssh-fallback]
  fleet release <release-id>|latest --tick [--max-idle-wait SECONDS] [--dry-run]
                                                      one scheduler step (release-tick.yml, ADR 0096):
                                                      at most one ring, after the bake; self-test decides.
                                                      `latest` then steps the skills releases the same
                                                      way, each seat only once its image has the
                                                      skills' commit
  fleet selftest --plant CHECK_ID SLUG [--window SECONDS]
                                                      staging only: plant one real violation, prove
                                                      the self-test alert opens and resolves
  fleet release --skills <version> [--only SLUG] [--parallel N] [--dry-run]
  fleet status [--json]
  fleet releases [--json] [--limit N]
  fleet hold SLUG --reason TEXT --until TEXT          records the session that set it (owner.py)
  fleet unhold SLUG
  fleet busy SLUG [--wait] [--max-wait SECONDS]     exit 0 idle, 1 busy, 3 could not tell
  fleet unquiesce SLUG                                remove a quiesce marker a fleet run left
  fleet latest-image                                  print the newest completed release image
  fleet seat-image SLUG                               print the image SLUG's Machine runs now;
                                                      exit 1 = no Machine yet (a new seat)
  fleet with-seat-lease SLUG -- CMD ...               run CMD holding SLUG's seat lease
                                                      (exit = CMD's; 4 if another holder deploys SLUG)

Exit codes: 0 success; 1 a seat failed / was refused / rollout halted, or the
--only seat did not end done (still busy after the idle wait, or held); 2 usage;
3 tooling error (nothing decided); 4 refused before anything ran (lease held,
unknown release, source guard). A tick adds 5 (waiting: bake running, lease
held by another rollout, nothing to do) and 6 (an earlier halt stands; a person
must act); its 1 means THIS tick halted the release.

Leases: a full release holds the fleet lease; every seat deploy (a release's,
an --only, a reprovision through with-seat-lease) holds that seat's lease. So
fixing one seat never queues behind a rollout of the others.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import ids, owner, status
from .build import BuildOptions, Builder, ImageExtractor
from .busy import BusyGate
from .d1 import D1, FleetStore
from .fly import Fly, FlyTokens
from .ledger import VerifyLedger
from .preflight import PreflightChecker
from .r2 import R2, image_key
from .rings import load_rings
from .rollout import ImagePlan, ImageRollout
from .runner import CommandRunner, Refusal, Runner, ToolingError
from .selftest_plant import SelftestPlant
from .skills import SkillsRollout, deferrals
from .smoke import FleetSideChecks, SmokeRunner
from .tick import TICK_WAITING, Ticker, combine
from .waves import Lease, SeatLease, Waves, holder_id

REPO = Path(__file__).resolve().parents[4]
# How many image releases a `latest` tick weighs at once (tick.Ticker.run_promoting).
# The real bound is the newest completed release (older ones are moot); this cap
# only limits a tick's reads. It was 6, and on 2026-10-26 seven merges in two
# hours pushed the one release ready for the client ring (1e696594) out of the
# window for a tick. 20 covers a full working day of merges at that pace.
IN_FLIGHT_MAX = 20


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [fleet] {msg}", flush=True)


@dataclass
class Ctx:
    repo: Path
    runner: Runner
    store: FleetStore
    r2: R2
    fly: Fly
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    log: Callable[[str], None] = log
    ledger: VerifyLedger | None = None

    @classmethod
    def default(cls, repo: Path = REPO) -> "Ctx":
        runner = CommandRunner()
        fly = Fly(runner, str(repo), FlyTokens.from_environ())
        return cls(repo, runner, FleetStore(D1(runner, str(repo))), R2(runner), fly, ledger=VerifyLedger.from_environ())

    def extractor(self) -> ImageExtractor:
        return ImageExtractor(self.runner, self.sleep, self.log, getattr(self.fly, "tokens", None))

    def rings(self) -> list[Any]:
        return load_rings(self.repo / "operator/fleet/rings.yaml", self.repo / "operator/customers")

    def render(self, seat: str) -> str:
        out = self.repo / "operator/.rendered" / ids.slug(seat) / "fly.toml"
        res = self.runner.run(
            ["bash", str(self.repo / "operator/bin/lib/render-fly-toml.sh"), seat, str(out)], timeout=120
        )
        if not res.ok:
            raise ToolingError(f"rendering fly.toml for {seat} failed: {res.text.strip()[-300:]}")
        return out.read_text(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fleet", description="Build one Operator image and roll releases out in rings.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build and push one image for every seat; record the release")
    b.add_argument("--notes", default="")
    b.add_argument(
        "--declare-secret", action="append", default=[], metavar="NAME", help="a staged secret this release may apply"
    )
    b.add_argument(
        "--not-volume-forward-compatible", action="store_true", help="a failing seat is halted, never auto-rolled back"
    )
    art = sub.add_parser("artifacts", help="re-read an image release's manifest/capabilities into R2")
    art.add_argument("release_id")
    r = sub.add_parser("release", help="roll a release out through the rings")
    r.add_argument("release_id", nargs="?")
    r.add_argument("--skills", metavar="VERSION")
    r.add_argument("--only", metavar="SLUG")
    r.add_argument("--parallel", type=int, default=1)
    r.add_argument("--force-mid-job", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--accept-lane-inputs", action="store_true")
    r.add_argument("--max-idle-wait", type=float, default=1800.0)
    r.add_argument(
        "--verify",
        choices=("bash", "selftest"),
        # selftest since 2026-10-26: the cross-check release (img-202610232220-4118de5f)
        # agreed on every seat but one, where the self-test was right (the pilot's
        # missing demand inputs), and both paths now judge lanes alike (#3448).
        default="selftest",
        help="who decides a seat: the boot smoke over ssh (the self-test's verdict is recorded beside it), "
        "or the seat's heartbeat self-test (the boot smoke decides when no verdict arrives)",
    )
    r.add_argument(
        "--no-ssh-fallback",
        action="store_true",
        help="with --verify selftest: a self-test that gives no verdict halts as a tooling error instead of "
        "running the ssh boot smoke, and a rollback is re-verified by the self-test",
    )
    r.add_argument(
        "--tick",
        action="store_true",
        help="one resumable scheduler step (release-tick.yml): advance at most one ring, after the bake; "
        "release id 'latest' means the newest image release. Exit 0 progress, 1 halt, 5 waiting, 6 standing halt",
    )
    st = sub.add_parser("selftest", help="staging only: re-prove the self-test alert path with a planted violation")
    st.add_argument("--plant", required=True, metavar="CHECK_ID")
    st.add_argument("slug")
    st.add_argument("--window", type=float, default=15 * 60.0)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    rl = sub.add_parser("releases")
    rl.add_argument("--json", action="store_true")
    rl.add_argument("--limit", type=int, default=20)
    h = sub.add_parser("hold")
    h.add_argument("slug")
    h.add_argument("--reason", required=True)
    h.add_argument("--until", required=True, help="the condition that ends the hold, in words (recorded with it)")
    u = sub.add_parser("unhold")
    u.add_argument("slug")
    bz = sub.add_parser("busy")
    bz.add_argument("slug")
    bz.add_argument("--wait", action="store_true")
    bz.add_argument("--max-wait", type=float, default=1800.0)
    uq = sub.add_parser("unquiesce")
    uq.add_argument("slug")
    sub.add_parser("latest-image")
    si = sub.add_parser("seat-image", help="the image a seat's Machine runs now")
    si.add_argument("slug")
    wl = sub.add_parser("with-seat-lease", help="run a command holding one seat's lease")
    wl.add_argument("slug")
    wl.add_argument("command", nargs=argparse.REMAINDER)
    return ap


def cmd_build(ctx: Ctx, a: argparse.Namespace) -> int:
    opts = BuildOptions(a.notes, tuple(a.declare_secret), not a.not_volume_forward_compatible)
    rel = Builder(ctx.repo, ctx.runner, ctx.fly, ctx.store, ctx.r2, ctx.extractor(), ctx.log).build(opts)
    print(rel["release_id"])
    return 0


def cmd_artifacts(ctx: Ctx, a: argparse.Namespace) -> int:
    """Re-read an image release's artifacts out of the registry and re-upload them to R2."""
    release = ctx.store.release(ids.release_id(a.release_id))
    if not release or release.get("kind") != "image":
        raise Refusal(f"no image release {a.release_id} in fleet_releases (fleet releases lists them)")
    meta = ctx.r2.get_json(image_key(release["image_digest"], "release.json")) or {}
    builder = Builder(ctx.repo, ctx.runner, ctx.fly, ctx.store, ctx.r2, ctx.extractor(), ctx.log)
    builder.upload_artifacts(release, {"hermes_sha": meta.get("hermes_sha") or ""})
    return 0


def _summary(
    ctx: Ctx, outcomes: list[Any], halted: bool, completed: bool, dry_run: bool, only: str | None = None
) -> int:
    """Log the outcomes and return the exit code.

    Under ``--only`` the one seat asked for is the whole question, so the exit is
    0 only when that seat ended ``done``. A seat still busy after the idle wait
    (``waiting_idle``) or held (``skipped_held``) halts nothing, and before
    2026-10-27 that exited 0, which release-tick.yml reports as "is on the
    release and passed its self-test" about a seat that was never deployed.
    """
    for o in outcomes:
        ctx.log(f"  {o.seat:<18} {o.state:<14} {o.detail}")
    # A dry run reads no completion (it records nothing), so it must not claim
    # anything about the release: on 2026-10-21 it said "not yet done on every
    # seat" of a release that was done on every seat.
    if dry_run:
        ctx.log("dry run: nothing deployed, nothing recorded")
        return 1 if halted else 0
    if halted:
        ctx.log("HALTED")
        return 1
    stuck = [o.seat for o in outcomes if o.state == "tooling_error"]
    if stuck:
        # No longer a halt (the rollout went on past them), but a person who ran
        # this must not read 0 as "it worked" on those seats.
        ctx.log(f"NOT on the release, nothing decided (tooling_error): {', '.join(stuck)}; see the detail above")
        return 1
    if only is not None:
        mine = [o for o in outcomes if o.seat == only]
        state = mine[-1].state if mine else "not reached"
        if state != "done":
            ctx.log(f"{only} is NOT on the release: it ended {state} (fleet status)")
            return 1
        ctx.log(f"{only} is done on the release")
    ctx.log(
        "release done on every seat" if completed else "finished; release not yet done on every seat (fleet status)"
    )
    return 0


# What "main" is when asking whether an image was built from it. Never HEAD: on
# a laptop HEAD is whatever branch the worktree is on, and from a hotfix-lane
# worktree a hotfix image IS an ancestor of HEAD, so `latest` would pick it and
# roll it to every ring. origin/main is main on CI (checkout fetch-depth: 0
# writes refs/remotes/origin/*) and on a laptop (stale at worst, which only
# makes `latest` older, never a hotfix).
MAIN_REF = "refs/remotes/origin/main"


def _built_from_main(ctx: Ctx, sha: str | None) -> bool:
    """Whether ``sha`` is an ancestor of origin/main (MAIN_REF).

    A commit this clone has never seen is not on main: the tick checks out main
    with full history, so main's commits are all present, and an image built
    from an unpushed or hotfix commit names one that is absent. merge-base exits
    128 on such a commit, which halted every tick for an hour on 2026-10-26 once
    `latest` began weighing several releases (one was a drill image built from a
    local commit). So presence is asked first; 128 after that is still a tooling
    error (a broken clone, or no origin/main), never a guess.
    """
    if not sha or not ids.SHA_RE.match(sha):
        return False
    present = ctx.runner.run(["git", "-C", str(ctx.repo), "cat-file", "-e", f"{sha}^{{commit}}"], timeout=60)
    if present.rc != 0:
        return False
    res = ctx.runner.run(["git", "-C", str(ctx.repo), "merge-base", "--is-ancestor", sha, MAIN_REF], timeout=60)
    if res.rc not in (0, 1):
        raise ToolingError(f"git merge-base --is-ancestor failed (rc={res.rc})")
    return res.rc == 0


def _image_release(ctx: Ctx, release_id: str) -> dict[str, Any]:
    if release_id == "latest":
        # The newest image built from main. A hotfix-lane image (built on purpose
        # from a divergent tree for one seat) is never "latest": the controller
        # would otherwise roll it to every ring.
        for rel in ctx.store.recent_releases(20):
            if rel.get("kind") == "image" and _built_from_main(ctx, rel.get("ss_console_sha")):
                return rel
        raise Refusal("no image release built from main is recorded in fleet_releases")
    release = ctx.store.release(ids.release_id(release_id))
    if not release or release.get("kind") != "image":
        raise Refusal(f"no image release {release_id} in fleet_releases (fleet releases lists them)")
    return release


def _image_rollout(ctx: Ctx, holder: str) -> ImageRollout:
    return ImageRollout(
        ctx.store, ctx.fly, BusyGate(ctx.fly, holder, ctx.sleep, ctx.clock), PreflightChecker(ctx.fly, ctx.r2),
        SmokeRunner(ctx.runner, str(ctx.repo)), ctx.render, ctx.log, ctx.sleep, ctx.clock,
        FleetSideChecks(ctx.fly, str(ctx.repo), ctx.sleep, ctx.clock), ctx.ledger,
    )  # fmt: skip


def cmd_release_image(ctx: Ctx, a: argparse.Namespace) -> int:
    if a.tick:
        return cmd_release_tick(ctx, a)
    release = _image_release(ctx, a.release_id)
    meta = ctx.r2.get_json(image_key(release["image_digest"], "release.json")) or {}
    holder = holder_id()
    roll = _image_rollout(ctx, holder)
    capable = _image_has_selftest(ctx, release)
    if a.verify == "selftest" and not capable:
        ctx.log(f"{release['release_id']}: the image carries no self-test; every seat is verified by the boot smoke")
    if a.no_ssh_fallback and not (a.verify == "selftest" and capable):
        raise Refusal("--no-ssh-fallback needs --verify selftest on an image that carries the self-test")
    plan = ImagePlan(
        release, meta.get("hermes_sha"), a.force_mid_job, a.accept_lane_inputs, a.max_idle_wait, a.dry_run,
        verify=a.verify, selftest_capable=capable, ssh_fallback=not a.no_ssh_fallback,
    )  # fmt: skip
    return _run_waves(ctx, a, release, lambda seat, ring: roll.run(seat, ring, plan), holder)


def cmd_release_tick(ctx: Ctx, a: argparse.Namespace) -> int:
    """One scheduler step (tick.py). Never forces a busy seat, never accepts lane inputs.

    ``latest`` steps the image releases, then the skills releases (2026-10-28:
    five skills candidates were published and none reached the fleet, because
    only a person running ``--skills`` ever rolled one). Image first, so a seat
    the image step just moved can take skills written at that commit on the
    same tick. A refusal or tooling error on the image side does not starve the
    skills side: the skills step still runs, then the image error is raised.
    """
    if a.only or a.force_mid_job or a.accept_lane_inputs or a.parallel != 1:
        raise ValueError("--tick takes none of --only, --force-mid-job, --accept-lane-inputs, --parallel")
    if a.release_id != "latest":
        return _image_tick(ctx, a)
    image_error: Exception | None = None
    image_code = TICK_WAITING
    try:
        image_code = _image_tick(ctx, a)
    except (Refusal, ToolingError) as exc:
        ctx.log(f"tick: the image step stopped ({exc}); stepping the skills releases before reporting it")
        image_error = exc
    skills_code = _skills_tick(ctx, a)
    if image_error is not None:
        raise image_error
    return combine(image_code, skills_code)


def _image_tick(ctx: Ctx, a: argparse.Namespace) -> int:
    release = _image_release(ctx, a.release_id)
    if release.get("completed_last_ring_at"):
        ctx.log(f"{release['release_id']}: already completed on every seat; nothing to do")
        return TICK_WAITING
    if not _image_has_selftest(ctx, release):
        raise Refusal(f"{release['release_id']}: the image carries no self-test, and a tick never runs the ssh smoke")
    meta = ctx.r2.get_json(image_key(release["image_digest"], "release.json")) or {}
    if a.verify != "selftest":
        ctx.log("tick: the seat's self-test decides every seat (no ssh boot smoke in a tick)")
    holder = holder_id()
    roll = _image_rollout(ctx, holder)

    def do_seat_for(rel: dict[str, Any]) -> Callable[[str, int], Any]:
        rel_meta = meta if rel["release_id"] == release["release_id"] else (
            ctx.r2.get_json(image_key(rel["image_digest"], "release.json")) or {}
        )  # fmt: skip
        plan = ImagePlan(rel, rel_meta.get("hermes_sha"), max_idle_wait=a.max_idle_wait, dry_run=a.dry_run,
                         verify="selftest", selftest_capable=True, ssh_fallback=False)  # fmt: skip
        return lambda seat, ring: roll.run(seat, ring, plan)

    ticker = Ticker(ctx.store, ctx.log, ctx.clock)
    if a.release_id == "latest":
        return ticker.run_promoting(ctx.rings(), _in_flight(ctx, release), do_seat_for, holder, dry_run=a.dry_run)
    return ticker.run(ctx.rings(), release, do_seat_for(release), holder, dry_run=a.dry_run)


def _in_flight(ctx: Ctx, newest: dict[str, Any]) -> list[dict[str, Any]]:
    """The image releases a `latest` tick may move, newest first: built from main,
    not yet completed, carrying the self-test, back to the newest completed one."""
    out = [newest]
    # Skills releases share the table, so read well past the cap of images.
    for rel in ctx.store.recent_releases(IN_FLIGHT_MAX * 3):
        if rel.get("kind") != "image" or rel["release_id"] == newest["release_id"]:
            continue
        if (rel.get("created_at") or "") > (newest.get("created_at") or ""):
            continue
        if rel.get("completed_last_ring_at"):
            break
        if _built_from_main(ctx, rel.get("ss_console_sha")) and _image_has_selftest(ctx, rel):
            out.append(rel)
        if len(out) >= IN_FLIGHT_MAX:
            break
    return out


def _at_or_after(ctx: Ctx, older: str, newer: str) -> bool:
    """Whether commit ``older`` is ``newer`` or one of its ancestors (both on main).

    A commit this clone has never seen is not on main (see _built_from_main), so
    it answers no rather than raising; any other git failure is a tooling error.
    """
    if older == newer:
        return True
    for sha in (older, newer):
        if not ids.SHA_RE.match(sha):
            return False
        present = ctx.runner.run(["git", "-C", str(ctx.repo), "cat-file", "-e", f"{sha}^{{commit}}"], timeout=60)
        if present.rc != 0:
            return False
    res = ctx.runner.run(["git", "-C", str(ctx.repo), "merge-base", "--is-ancestor", older, newer], timeout=60)
    if res.rc not in (0, 1):
        raise ToolingError(f"git merge-base --is-ancestor failed (rc={res.rc})")
    return res.rc == 0


def _skills_in_flight(ctx: Ctx) -> list[dict[str, Any]]:
    """The skills releases a tick may move, newest first: published from main, back
    to the newest completed one (anything older is superseded by it)."""
    out: list[dict[str, Any]] = []
    # Image releases share the table (one per substrate merge), so read past the cap.
    for rel in ctx.store.recent_releases(IN_FLIGHT_MAX * 3):
        if rel.get("kind") != "skills":
            continue
        if rel.get("completed_last_ring_at"):
            break
        version = rel.get("skills_version") or ""
        if ids.SKILLS_VERSION_RE.match(version) and _built_from_main(ctx, rel.get("ss_console_sha")):
            out.append(rel)
        if len(out) >= IN_FLIGHT_MAX:
            break
    return out


def _skills_defer(ctx: Ctx, seats: list[str]) -> Callable[[dict[str, Any]], dict[str, str]]:
    """skills.deferrals bound to this tick's reads: fleet_status once (after the
    image step, so a seat it just moved counts), each image's commit and each
    ancestry question once."""
    status = ctx.store.fleet_status()
    shas: dict[str, str | None] = {}
    pairs: dict[tuple[str, str], bool] = {}

    def image_sha_of(ref: str | None) -> str | None:
        if not ref:
            return None
        if ref not in shas:
            sha = (ctx.store.release_for_image(ref) or {}).get("ss_console_sha")
            shas[ref] = sha if _built_from_main(ctx, sha) else None
        return shas[ref]

    def at_or_after(older: str, newer: str) -> bool:
        if (older, newer) not in pairs:
            pairs[(older, newer)] = _at_or_after(ctx, older, newer)
        return pairs[(older, newer)]

    return lambda rel: deferrals(rel, seats, status, image_sha_of, at_or_after)


def _skills_tick(ctx: Ctx, a: argparse.Namespace) -> int:
    """The skills half of a `latest` tick: the same promotion an image gets, no restart."""
    candidates = _skills_in_flight(ctx)
    if not candidates:
        ctx.log("tick: no skills release in flight (the newest published from main has completed)")
        return TICK_WAITING
    roll = SkillsRollout(ctx.store, ctx.r2, ctx.log, ctx.sleep, ctx.clock)
    manifests: dict[str, tuple[dict[str, Any], str, str]] = {}

    def do_seat_for(rel: dict[str, Any]) -> Callable[[str, int], Any]:
        version = rel["skills_version"]
        if version not in manifests:
            manifests[version] = roll.load_manifest(version)
        manifest, key, sha = manifests[version]
        return lambda seat, ring: roll.run(seat, ring, rel, manifest, key, sha, a.dry_run)

    rings = ctx.rings()
    seats = [seat for ring in rings for seat in ring.seats]
    ticker = Ticker(ctx.store, ctx.log, ctx.clock, defer=_skills_defer(ctx, seats))
    return ticker.run_promoting(rings, candidates, do_seat_for, holder_id(), dry_run=a.dry_run)


def _image_has_selftest(ctx: Ctx, release: dict[str, Any]) -> bool:
    """Whether the release image carries the in-image self-test (its baked
    capabilities say so). Unreadable reads as no: the boot smoke decides."""
    digest = release.get("image_digest")
    if not digest:
        return False
    try:
        caps = ctx.r2.get_json(image_key(digest, "image-capabilities.json")) or {}
    except ToolingError as exc:
        ctx.log(f"could not read image-capabilities.json ({exc}); verifying with the boot smoke")
        return False
    return bool(caps.get("selftest"))


def cmd_selftest(ctx: Ctx, a: argparse.Namespace) -> int:
    """Plant one real violation on staging and prove the alert opens and resolves."""

    def alert_status(seat: str) -> str | None:
        rows = ctx.store.d1.query(
            "SELECT status FROM fleet_alert_state WHERE customer_slug = "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"{ids.sql_str(ids.slug(seat))} AND condition = 'selftest_failing'"
        )
        return rows[0].get("status") if rows else None

    report = SelftestPlant(ctx.fly, ctx.store.seat_status, alert_status, ctx.sleep, ctx.clock).run(
        a.slug, a.plant, a.window
    )
    for line in report.steps:
        ctx.log(f"selftest --plant {a.plant} {a.slug}: {line}")
    return 0 if report.ok else 1


def cmd_release_skills(ctx: Ctx, a: argparse.Namespace) -> int:
    version = ids.skills_version(a.skills)
    roll = SkillsRollout(ctx.store, ctx.r2, ctx.log, ctx.sleep, ctx.clock)
    manifest, key, sha = roll.load_manifest(version)
    release = ctx.store.skills_release(version)
    if release is None:
        release = {
            "release_id": f"skl-{version}",
            "kind": "skills",
            "skills_version": version,
            "ss_console_sha": manifest.get("source_sha"),
            "notes": "recorded by fleet release (CI did not)",
        }
        if not a.dry_run:
            ctx.store.insert_release(release)
    return _run_waves(
        ctx, a, release, lambda seat, ring: roll.run(seat, ring, release, manifest, key, sha, a.dry_run), holder_id()
    )


def _run_waves(
    ctx: Ctx, a: argparse.Namespace, release: dict[str, Any], do_seat: Callable[[str, int], Any], holder: str
) -> int:
    """Walk the rings. Only a full real run takes the fleet lease; every seat takes its own."""
    waves = Waves(ctx.store, ctx.log)
    seat_holder = None if a.dry_run else holder

    def walk(lease: Lease | None) -> Any:
        return waves.run(ctx.rings(), do_seat, release=release, only=a.only, parallel=a.parallel,
                         dry_run=a.dry_run, lease=lease, holder=seat_holder)  # fmt: skip

    if a.dry_run or a.only:
        result = walk(None)
    else:
        with Lease(ctx.store, holder, release["release_id"]) as lease:
            result = walk(lease)
    # Stamp once. Re-stamping an already-completed release would move it to the
    # front of latest_completed and hand a NEW seat an older image.
    if result.completed and not release.get("completed_last_ring_at"):
        ctx.store.mark_completed(release["release_id"])
        ctx.log(f"{release['release_id']}: every seat is done; release marked completed")
    return _summary(ctx, result.outcomes, result.halted, result.completed, a.dry_run, a.only)


def cmd_busy(ctx: Ctx, a: argparse.Namespace) -> int:
    seat = ids.slug(a.slug)
    if not ctx.fly.app_exists(ids.app_name(seat)):
        ctx.log(f"{seat}: no app yet; nothing can be running")
        return 0
    gate = BusyGate(ctx.fly, holder_id(), ctx.sleep, ctx.clock)
    verdict = gate.wait_idle(seat, a.max_wait, ctx.log) if a.wait else gate.probe(seat)
    print(json.dumps({"seat": seat, "busy": verdict.busy, "reasons": verdict.reasons}))
    return 1 if verdict.busy else 0


def cmd_seat_image(ctx: Ctx, a: argparse.Namespace) -> int:
    """The image a seat's Machine runs now, read from Fly (the rollback target's source).

    Exit 1 means there is no Machine yet: a new seat, which provision-customer.sh
    deploys on the newest completed release instead. Anything Fly cannot answer
    is a ToolingError (exit 3): "could not tell" must never read as "new seat".
    """
    app = ids.app_name(ids.slug(a.slug))
    if not ctx.fly.app_exists(app) or not ctx.fly.machines(app):
        print(f"{a.slug}: no Machine yet", file=sys.stderr)
        return 1
    print(ctx.fly.machine_image(app))
    return 0


def cmd_with_seat_lease(ctx: Ctx, a: argparse.Namespace) -> int:
    """Run a command (a reprovision) holding one seat's lease; its exit code is ours."""
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        raise ValueError("with-seat-lease needs a command after --")
    seat = ids.slug(a.slug)
    with SeatLease(ctx.store, holder_id(), seat, "reprovision"):
        ctx.log(f"{seat}: seat lease held; running {command[0]}")
        return subprocess.call(command)  # noqa: S603 - the Captain's own command line, run as given


def cmd_latest_image(ctx: Ctx, _a: argparse.Namespace) -> int:
    rel = ctx.store.latest_completed("image")
    if not rel:
        print("no image release has completed its last ring", file=sys.stderr)
        return 1
    print(rel["image_ref"])
    return 0


def cmd_status(ctx: Ctx, a: argparse.Namespace) -> int:
    rings = ctx.rings()
    seats = [seat for ring in rings for seat in ring.seats]
    rows = status.seat_rows(ctx.store, rings, status.lever_flags(ctx.fly, seats))
    if a.json:
        print(json.dumps(rows, indent=1, default=str))
        return 0
    print(status.render_table(rows))
    print(status.render_holds(ctx.store.holds()))
    return 0


def dispatch(ctx: Ctx, a: argparse.Namespace) -> int:
    if a.cmd == "build":
        return cmd_build(ctx, a)
    if a.cmd == "artifacts":
        return cmd_artifacts(ctx, a)
    if a.cmd == "release":
        if bool(a.skills) == bool(a.release_id):
            raise Refusal("give exactly one of <release-id> or --skills <version>")
        if a.tick and a.skills:
            raise ValueError("--tick takes no --skills: `fleet release latest --tick` steps skills releases too")
        return cmd_release_skills(ctx, a) if a.skills else cmd_release_image(ctx, a)
    if a.cmd == "status":
        return cmd_status(ctx, a)
    if a.cmd == "releases":
        print(status.render_releases(ctx.store.recent_releases(a.limit), a.json))
        return 0
    if a.cmd == "hold":
        # The reason carries when to lift it, so whoever finds the hold can tell
        # whether it is still needed without finding the session that set it.
        reason = f"{a.reason.rstrip('. ')}. Lift when: {a.until}"
        by = owner.held_by()
        ctx.store.hold(ids.slug(a.slug), reason, by)
        ctx.log(f"{a.slug}: held by {by} ({reason})")
        return 0
    if a.cmd == "unhold":
        ctx.store.unhold(ids.slug(a.slug))
        ctx.log(f"{a.slug}: hold removed")
        return 0
    if a.cmd == "busy":
        return cmd_busy(ctx, a)
    if a.cmd == "selftest":
        return cmd_selftest(ctx, a)
    if a.cmd == "unquiesce":
        ctx.log(f"{a.slug}: {BusyGate(ctx.fly, holder_id()).clear_any_fleet(ids.slug(a.slug))}")
        return 0
    if a.cmd == "seat-image":
        return cmd_seat_image(ctx, a)
    if a.cmd == "with-seat-lease":
        return cmd_with_seat_lease(ctx, a)
    return cmd_latest_image(ctx, a)


def main(argv: list[str] | None = None, ctx: Ctx | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        return dispatch(ctx or Ctx.default(), a)
    except Refusal as exc:
        log(f"REFUSED: {exc}")
        return 4
    except ValueError as exc:
        log(f"REFUSED: {exc}")
        return 2
    except ToolingError as exc:
        log(f"TOOLING ERROR (nothing decided): {exc}")
        return 3
    except subprocess.SubprocessError as exc:
        log(f"TOOLING ERROR (nothing decided): {exc}")
        return 3
