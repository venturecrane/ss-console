"""`operator/bin/fleet` entry point. See the package docstring for the contract.

  fleet build [--notes TEXT] [--declare-secret NAME ...] [--not-volume-forward-compatible]
  fleet release <release-id> [--only SLUG] [--parallel N] [--force-mid-job]
                [--dry-run] [--accept-lane-inputs] [--max-idle-wait SECONDS]
  fleet release --skills <version> [--only SLUG] [--parallel N] [--dry-run]
  fleet status [--json]
  fleet releases [--json] [--limit N]
  fleet hold SLUG --reason TEXT
  fleet unhold SLUG
  fleet busy SLUG [--wait] [--max-wait SECONDS]     exit 0 idle, 1 busy, 3 could not tell
  fleet unquiesce SLUG                                remove a quiesce marker a fleet run left
  fleet latest-image                                  print the newest completed release image

Exit codes: 0 success; 1 a seat failed / was refused / rollout halted; 2 usage;
3 tooling error (nothing decided); 4 refused before anything ran (lease held,
unknown release, source guard).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import ids, status
from .build import BuildOptions, Builder, ImageExtractor
from .busy import BusyGate
from .d1 import D1, FleetStore
from .fly import Fly
from .preflight import PreflightChecker
from .r2 import R2, image_key
from .rings import load_rings
from .rollout import ImagePlan, ImageRollout
from .runner import CommandRunner, Refusal, Runner, ToolingError
from .skills import SkillsRollout
from .smoke import SmokeRunner
from .waves import Lease, Waves, holder_id

REPO = Path(__file__).resolve().parents[4]


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

    @classmethod
    def default(cls, repo: Path = REPO) -> "Ctx":
        runner = CommandRunner()
        return cls(repo, runner, FleetStore(D1(runner, str(repo))), R2(runner), Fly(runner, str(repo)))

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
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    rl = sub.add_parser("releases")
    rl.add_argument("--json", action="store_true")
    rl.add_argument("--limit", type=int, default=20)
    h = sub.add_parser("hold")
    h.add_argument("slug")
    h.add_argument("--reason", required=True)
    u = sub.add_parser("unhold")
    u.add_argument("slug")
    bz = sub.add_parser("busy")
    bz.add_argument("slug")
    bz.add_argument("--wait", action="store_true")
    bz.add_argument("--max-wait", type=float, default=1800.0)
    uq = sub.add_parser("unquiesce")
    uq.add_argument("slug")
    sub.add_parser("latest-image")
    return ap


def cmd_build(ctx: Ctx, a: argparse.Namespace) -> int:
    opts = BuildOptions(a.notes, tuple(a.declare_secret), not a.not_volume_forward_compatible)
    rel = Builder(ctx.repo, ctx.runner, ctx.fly, ctx.store, ctx.r2, ImageExtractor(ctx.runner), ctx.log).build(opts)
    print(rel["release_id"])
    return 0


def cmd_artifacts(ctx: Ctx, a: argparse.Namespace) -> int:
    """Re-read an image release's artifacts out of the registry and re-upload them to R2."""
    release = ctx.store.release(ids.release_id(a.release_id))
    if not release or release.get("kind") != "image":
        raise Refusal(f"no image release {a.release_id} in fleet_releases (fleet releases lists them)")
    meta = ctx.r2.get_json(image_key(release["image_digest"], "release.json")) or {}
    builder = Builder(ctx.repo, ctx.runner, ctx.fly, ctx.store, ctx.r2, ImageExtractor(ctx.runner), ctx.log)
    builder.upload_artifacts(release, {"hermes_sha": meta.get("hermes_sha") or ""})
    return 0


def _summary(ctx: Ctx, outcomes: list[Any], halted: bool, completed: bool) -> int:
    for o in outcomes:
        ctx.log(f"  {o.seat:<18} {o.state:<14} {o.detail}")
    ctx.log(
        "HALTED" if halted else ("completed every ring" if completed else "finished (partial: --only or --dry-run)")
    )
    return 1 if halted else 0


def cmd_release_image(ctx: Ctx, a: argparse.Namespace) -> int:
    release = ctx.store.release(ids.release_id(a.release_id))
    if not release or release.get("kind") != "image":
        raise Refusal(f"no image release {a.release_id} in fleet_releases (fleet releases lists them)")
    meta = ctx.r2.get_json(image_key(release["image_digest"], "release.json")) or {}
    holder = holder_id()
    gate = BusyGate(ctx.fly, holder, ctx.sleep, ctx.clock)
    roll = ImageRollout(
        ctx.store,
        ctx.fly,
        gate,
        PreflightChecker(ctx.fly, ctx.r2),
        SmokeRunner(ctx.runner, str(ctx.repo)),
        ctx.render,
        ctx.log,
        ctx.sleep,
        ctx.clock,
    )
    plan = ImagePlan(release, meta.get("hermes_sha"), a.force_mid_job, a.accept_lane_inputs, a.max_idle_wait, a.dry_run)
    lease = None if a.dry_run else Lease(ctx.store, holder, release["release_id"])
    return _run_waves(ctx, a, release, lambda seat, ring: roll.run(seat, ring, plan), lease)


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
    holder = holder_id()
    lease = None if a.dry_run else Lease(ctx.store, holder, release["release_id"])
    return _run_waves(
        ctx, a, release, lambda seat, ring: roll.run(seat, ring, release, manifest, key, sha, a.dry_run), lease
    )


def _run_waves(
    ctx: Ctx, a: argparse.Namespace, release: dict[str, Any], do_seat: Callable[[str, int], Any], lease: Lease | None
) -> int:
    waves = Waves(ctx.store, ctx.log)
    if lease is None:
        result = waves.run(
            ctx.rings(), do_seat, release=release, only=a.only, parallel=a.parallel, dry_run=a.dry_run, lease=None
        )
    else:
        with lease:
            result = waves.run(
                ctx.rings(), do_seat, release=release, only=a.only, parallel=a.parallel, dry_run=a.dry_run, lease=lease
            )
    if result.completed:
        ctx.store.mark_completed(release["release_id"])
    return _summary(ctx, result.outcomes, result.halted, result.completed)


def cmd_busy(ctx: Ctx, a: argparse.Namespace) -> int:
    seat = ids.slug(a.slug)
    if not ctx.fly.app_exists(ids.app_name(seat)):
        ctx.log(f"{seat}: no app yet; nothing can be running")
        return 0
    gate = BusyGate(ctx.fly, holder_id(), ctx.sleep, ctx.clock)
    verdict = gate.wait_idle(seat, a.max_wait, ctx.log) if a.wait else gate.probe(seat)
    print(json.dumps({"seat": seat, "busy": verdict.busy, "reasons": verdict.reasons}))
    return 1 if verdict.busy else 0


def cmd_latest_image(ctx: Ctx, _a: argparse.Namespace) -> int:
    rel = ctx.store.latest_completed("image")
    if not rel:
        print("no image release has completed its last ring", file=sys.stderr)
        return 1
    print(rel["image_ref"])
    return 0


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - a username only labels the hold row, never a reason to stop
        return os.environ.get("USER", "unknown")


def dispatch(ctx: Ctx, a: argparse.Namespace) -> int:
    if a.cmd == "build":
        return cmd_build(ctx, a)
    if a.cmd == "artifacts":
        return cmd_artifacts(ctx, a)
    if a.cmd == "release":
        if bool(a.skills) == bool(a.release_id):
            raise Refusal("give exactly one of <release-id> or --skills <version>")
        return cmd_release_skills(ctx, a) if a.skills else cmd_release_image(ctx, a)
    if a.cmd == "status":
        rows = status.seat_rows(ctx.store, ctx.rings())
        print(json.dumps(rows, indent=1, default=str) if a.json else status.render_table(rows))
        return 0
    if a.cmd == "releases":
        print(status.render_releases(ctx.store.recent_releases(a.limit), a.json))
        return 0
    if a.cmd == "hold":
        ctx.store.hold(ids.slug(a.slug), a.reason, _who())
        ctx.log(f"{a.slug}: held ({a.reason})")
        return 0
    if a.cmd == "unhold":
        ctx.store.unhold(ids.slug(a.slug))
        ctx.log(f"{a.slug}: hold removed")
        return 0
    if a.cmd == "busy":
        return cmd_busy(ctx, a)
    if a.cmd == "unquiesce":
        ctx.log(f"{a.slug}: {BusyGate(ctx.fly, holder_id()).clear_any_fleet(ids.slug(a.slug))}")
        return 0
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
