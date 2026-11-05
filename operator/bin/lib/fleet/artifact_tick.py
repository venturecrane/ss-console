"""The artifact half of a `latest` tick: skills and the jobs bundle, and the git questions they share.

Split out of cli.py (2026-10-31, ADR 0100 W3) when the bundle step pushed that
module past the 500-line ceiling. cli.py re-exports every name here, so
``cli._skills_defer`` and friends are unchanged for callers and tests.

``ctx`` is cli.Ctx; it is typed ``Any`` here because cli imports this module.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Callable, Mapping

from . import bundles, ids, ordering
from .busy import BusyGate
from .rings import Ring
from .runner import Refusal, ToolingError
from .skills import SkillsRollout, deferrals
from .tick import TICK_WAITING, Ticker, combine
from .waves import holder_id

# How many image releases a `latest` tick weighs at once (tick.Ticker.run_promoting).
# The real bound is the newest completed release (older ones are moot); this cap
# only limits a tick's reads. It was 6, and on 2026-10-26 seven merges in two
# hours pushed the one release ready for the client ring (1e696594) out of the
# window for a tick. 20 covers a full working day of merges at that pace.
IN_FLIGHT_MAX = 20


# What "main" is when asking whether an image was built from it. Never HEAD: on
# a laptop HEAD is whatever branch the worktree is on, and from a hotfix-lane
# worktree a hotfix image IS an ancestor of HEAD, so `latest` would pick it and
# roll it to every ring. origin/main is main on CI (checkout fetch-depth: 0
# writes refs/remotes/origin/*) and on a laptop (stale at worst, which only
# makes `latest` older, never a hotfix).
MAIN_REF = "refs/remotes/origin/main"


def _known_commit(ctx: Any, sha: str | None) -> bool:
    """Whether ``sha`` is a commit this clone holds (main or any fetched branch)."""
    if not sha or not ids.SHA_RE.match(sha):
        return False
    present = ctx.runner.run(["git", "-C", str(ctx.repo), "cat-file", "-e", f"{sha}^{{commit}}"], timeout=60)
    return present.rc == 0


def fetch_main(ctx: Any) -> bool:
    """Bring ``refs/remotes/origin/main`` up to date in the run's checkout, so a commit merged
    since the run started counts as built from main. 2026-11-05: run 41985907116 was checked out
    at 07:15Z; #3777 merged 07:26Z and its image was recorded 07:34Z, but ``_built_from_main``
    asked a clone that had never seen that commit, so the refresh after each ring kept naming the
    old release and the run rolled it through every ring until 08:03Z while the newer image
    waited. A failed fetch is logged and the stale answer stands (never a halt)."""
    res = ctx.runner.run(
        ["git", "-C", str(ctx.repo), "fetch", "-q", "origin", f"+refs/heads/main:{MAIN_REF}"], timeout=120
    )
    if res.rc != 0:
        ctx.log("tick: git fetch origin main failed; an image merged since this run started may not count as latest")
    return res.rc == 0


def refreshed(ctx: Any, read: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The mid-run hand-off question, asked of main as it is NOW: fetch, then ``read()`` (cli.py's
    releases in flight, newest first). Here, not in cli.py, because cli.py sits at its size ceiling."""
    fetch_main(ctx)
    return read()


def _built_from_main(ctx: Any, sha: str | None) -> bool:
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


def _at_or_after(ctx: Any, older: str, newer: str) -> bool:
    """Whether commit ``older`` is ``newer`` or one of its ancestors (any commits this clone holds).

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


def _skills_in_flight(ctx: Any) -> list[dict[str, Any]]:
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


class _Reads:
    """One tick's cached reads for the ordering rule: each image's commit, each
    ancestry question and each base(S) asked once."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.shas: dict[str, str | None] = {}
        self.pairs: dict[tuple[str, str], bool] = {}
        self.bases: dict[str, str | None] = {}

    def image_sha_of(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ref not in self.shas:
            sha = (self.ctx.store.release_for_image(ref) or {}).get("ss_console_sha")
            # Any recorded image whose commit this clone holds, main or a hotfix
            # branch: the ancestry check then asks whether that image carries
            # base(S) (2026-10-31, ADR 0100: A&P on a hotfix image that contained
            # the skills' code waited ~15 h per release, #3657).
            self.shas[ref] = sha if _known_commit(self.ctx, sha) else None
        return self.shas[ref]

    def at_or_after(self, older: str, newer: str) -> bool:
        if (older, newer) not in self.pairs:
            self.pairs[(older, newer)] = _at_or_after(self.ctx, older, newer)
        return self.pairs[(older, newer)]

    def base_of(self, sha: str) -> str | None:
        """base(S): ordering.base_commit, the last commit at or before S touching an image path."""
        if sha not in self.bases:
            self.bases[sha] = ordering.base_commit(self.ctx.runner, self.ctx.repo, sha)
        return self.bases[sha]


def _skills_defer(ctx: Any, seats: list[str]) -> Callable[[dict[str, Any]], dict[str, str]]:
    """skills.deferrals bound to this tick's reads: fleet_status once (after the
    image step, so a seat it just moved counts), each image's commit, each
    ancestry question and each base(S) once."""
    status = ctx.store.fleet_status()
    reads = _Reads(ctx)
    return lambda rel: deferrals(rel, seats, status, reads.image_sha_of, reads.at_or_after, reads.base_of)


def _bundles_defer(ctx: Any, seats: list[str]) -> Callable[[dict[str, Any]], dict[str, str]]:
    """bundles.deferrals bound to this tick's reads, as _skills_defer."""
    status = ctx.store.fleet_status()
    reads = _Reads(ctx)
    return lambda rel: bundles.deferrals(rel, seats, status, reads.image_sha_of, reads.at_or_after, reads.base_of)


def newer_image_since(ctx: Any) -> Callable[[], bool]:
    """The hand-off signal for the skills and bundle steps of a `latest` tick: True once an image
    release newer than the newest recorded when this was made exists, so the step ends with
    progress and the run it starts takes that image from ring 0 (tick.run_promoting ``yield_when``).
    2026-11-05: the bundle step carried bnd-1991f54c through every ring, busy-gate waits included,
    while 6db0f014's image waited 18 minutes for the lane."""

    def images() -> set[str]:
        return {str(r["release_id"]) for r in ctx.store.recent_releases(20) if r.get("kind") == "image"}

    start = images()
    # Any image row that was not there at the start: order-free, so a fake or a
    # D1 read that lists oldest first answers the same.
    return lambda: bool(images() - start)


def _isolated(ctx: Any, what: str, step: Callable[[], int]) -> tuple[int, Exception | None]:
    """Run one step of a `latest` tick; a refusal or tooling error is kept for after the later steps."""
    try:
        return step(), None
    except (Refusal, ToolingError) as exc:
        ctx.log(f"tick: the {what} step stopped ({exc}); stepping the rest before reporting it")
        return TICK_WAITING, exc


def latest_tick(ctx: Any, a: argparse.Namespace, image_tick: Callable[[int | None], int]) -> int:
    """A `latest` tick. Paying clients' seats take everything before SMD's own seats take anything.

    Pass 1 holds the last ring (SMD's own seats): the image, then the skills, then the jobs bundle
    each step the rings before it. Pass 2 runs the three again with no hold, which is the last
    ring's turn. 2026-11-05, run 42041885945: ashton-price took the image at 14:23Z, the run then
    deployed that image to our own four seats one at a time until 14:52Z, and the client's jobs
    bundle only started after that (15:02Z): 34 minutes merge-to-seat for the image, 74 for the bundle.

    Image first within a pass, so a seat the image step just moved can take skills and a bundle
    written at that commit in the same run (2026-10-28). A refusal or tooling error in one step
    does not starve the others: the rest still step, then the first error is raised. Pass 2 is
    skipped when the image step raised (it would raise again) and when a newer image was published
    during pass 1: the run that takes that image starts from staging, and rolling our own seats
    on the old one first would put the client behind them again.
    """
    rings = [r for r in ctx.rings() if r.seats]
    last = rings[-1].number if len(rings) > 1 else None
    landed = newer_image_since(ctx)
    codes: list[int] = []
    errors: list[Exception] = []

    def step(what: str, run: Callable[[], int]) -> bool:
        code, err = _isolated(ctx, what, run)
        codes.append(code)
        if err is not None:
            errors.append(err)
        return err is None

    image_ok = step("image", lambda: image_tick(last))
    step("skills", lambda: _skills_tick(ctx, a, last))
    step("bundle", lambda: _bundles_tick(ctx, a, last))
    if last is not None and image_ok:
        if landed():
            ctx.log(f"tick: a newer image was published during this run; ring {last} is left for the run that takes it")
        else:
            ctx.log(f"tick: the rings before ring {last} have had every release; now ring {last}")
            step("image", lambda: image_tick(None))
            step("skills", lambda: _skills_tick(ctx, a))
            step("bundle", lambda: _bundles_tick(ctx, a))
    if errors:
        raise errors[0]
    return combine(*codes)


def _skills_tick(ctx: Any, a: argparse.Namespace, hold_ring: int | None = None) -> int:
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
    return ticker.run_promoting(
        rings, candidates, do_seat_for, holder_id(), dry_run=a.dry_run, yield_when=newer_image_since(ctx),
        hold_ring=hold_ring,
    )  # fmt: skip


def _bundles_in_flight(ctx: Any, name: str = bundles.JOBS) -> list[dict[str, Any]]:
    """The bundle releases a tick may move, newest first: published from main, back
    to the newest completed one (anything older is superseded by it)."""
    out: list[dict[str, Any]] = []
    for rel in ctx.store.recent_releases(IN_FLIGHT_MAX * 3):
        if rel.get("kind") != "bundle" or rel.get("artifact_name") != name:
            continue
        if rel.get("completed_last_ring_at"):
            break
        version = rel.get("artifact_version") or ""
        if ids.BUNDLE_VERSION_RE.match(version) and _built_from_main(ctx, rel.get("ss_console_sha")):
            out.append(rel)
        if len(out) >= IN_FLIGHT_MAX:
            break
    return out


# The off switch for the tick's bundle step (repo variable, passed into the Tick
# step's env by release-tick.yml). Absent or anything but 'true': the tick rolls
# no bundle. CI still publishes candidates, and the urgent one-seat path
# (`fleet release --bundle ... --only SEAT`) still runs: that is how the first
# bundle goes staging -> pilot (a real job) -> ashton-price while the rings run
# staging -> ashton-price -> pilot.
BUNDLE_SWITCH = "BUNDLE_RELEASE_ENABLED"


def bundle_releases_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return (os.environ if environ is None else environ).get(BUNDLE_SWITCH) == "true"


def bundle_rings(ctx: Any) -> list[Ring]:
    """The rings with only the seats whose customer.yaml authors a job lane."""
    every = ctx.rings()
    authoring = bundles.authoring_seats(ctx.repo, [s for ring in every for s in ring.seats])
    return bundles.authoring_rings(every, authoring)


def bundle_seat_runner(
    ctx: Any, holder: str, max_idle_wait: float, dry_run: bool
) -> Callable[[dict[str, Any]], Callable[[str, int], Any]]:
    """``do_seat_for(release)``: one seat's bundle rollout (bundles.BundleRollout), manifests read once."""
    roll = bundles.BundleRollout(
        ctx.store, ctx.r2, ctx.fly, BusyGate(ctx.fly, holder, ctx.sleep, ctx.clock), ctx.log, ctx.sleep, ctx.clock,
        max_idle_wait,
    )  # fmt: skip
    manifests: dict[str, tuple[dict[str, Any], str, str]] = {}

    def do_seat_for(rel: dict[str, Any]) -> Callable[[str, int], Any]:
        version = rel["artifact_version"]
        if version not in manifests:
            manifests[version] = roll.load_manifest(rel["artifact_name"], version)
        manifest, key, sha = manifests[version]
        return lambda seat, ring: roll.run(seat, ring, rel, manifest, key, sha, dry_run)

    return do_seat_for


def bundle_release_named(ctx: Any, version: str, name: str = bundles.JOBS) -> dict[str, Any]:
    """A recorded bundle release by version, or ``latest``: the newest one published from main."""
    if version == "latest":
        for rel in ctx.store.recent_releases(IN_FLIGHT_MAX * 3):
            if (
                rel.get("kind") == "bundle"
                and rel.get("artifact_name") == name
                and ids.BUNDLE_VERSION_RE.match(rel.get("artifact_version") or "")
                and _built_from_main(ctx, rel.get("ss_console_sha"))
            ):
                return rel
        raise Refusal(f"no {name} bundle release published from main is recorded in fleet_releases")
    rel = ctx.store.bundle_release(name, ids.bundle_version(version))
    if not rel:
        raise Refusal(f"no {name} bundle release {version} in fleet_releases (fleet releases lists them)")
    return rel


def _bundles_tick(ctx: Any, a: argparse.Namespace, hold_ring: int | None = None) -> int:
    """The jobs bundle step of a `latest` tick: rings FILTERED to the seats that author a job lane.

    Filtered, not deferred: a deferred seat keeps a release incomplete for ever
    (tick.plan_tick), and a seat that authors no lane never takes a jobs bundle,
    so it must neither be touched nor block completion (bundles.py). Off unless
    BUNDLE_RELEASE_ENABLED is 'true' (:data:`BUNDLE_SWITCH`).
    """
    if not bundle_releases_enabled():
        ctx.log(f"tick: bundle releases not enabled ({BUNDLE_SWITCH} is not 'true'); no bundle rolls")
        return TICK_WAITING
    candidates = _bundles_in_flight(ctx)
    if not candidates:
        ctx.log("tick: no bundle release in flight (the newest published from main has completed)")
        return TICK_WAITING
    holder = holder_id()
    rings = bundle_rings(ctx)
    seats = [seat for ring in rings for seat in ring.seats]
    if not seats:
        ctx.log("tick: no ringed seat authors a job lane; no seat takes a jobs bundle")
        return TICK_WAITING
    ticker = Ticker(ctx.store, ctx.log, ctx.clock, defer=_bundles_defer(ctx, seats))
    do_seat_for = bundle_seat_runner(ctx, holder, a.max_idle_wait, a.dry_run)
    return ticker.run_promoting(
        rings, candidates, do_seat_for, holder, dry_run=a.dry_run, yield_when=newer_image_since(ctx),
        hold_ring=hold_ring,
    )  # fmt: skip


def cmd_bundle_base(ctx: Any, a: argparse.Namespace) -> int:
    """The image a bundle built at ``--sha`` is built inside: the image that commit
    built, else the newest completed image built from main. Prints its ref."""
    sha = a.sha
    if not ids.SHA_RE.match(sha):
        raise ValueError(f"--sha must be a 40-hex commit: {sha!r}")
    rel = ctx.store.release_for_sha("image", sha)
    if not rel:
        for cand in ctx.store.recent_releases(IN_FLIGHT_MAX * 3):
            if (
                cand.get("kind") == "image"
                and cand.get("completed_last_ring_at")
                and _built_from_main(ctx, cand.get("ss_console_sha"))
            ):
                rel = cand
                break
    if not rel or not rel.get("image_ref"):
        raise Refusal(f"no image built at {sha[:8]} and no completed image built from main to build a bundle in")
    ref = ids.image_ref(rel["image_ref"])
    digest = rel.get("image_digest")
    print(f"{ref.split('@', 1)[0]}@{ids.digest(digest)}" if digest and "@" not in ref else ref)
    return 0


def cmd_bundle_record(ctx: Any, a: argparse.Namespace) -> int:
    """Record a jobs bundle CI just published (kind 'bundle', release id bnd-<version>)."""
    version = ids.bundle_version(a.version)
    if not ids.SHA_RE.match(a.sha):
        raise ValueError(f"--sha must be a 40-hex commit: {a.sha!r}")
    rid = ids.release_id(f"bnd-{version}")
    if ctx.store.release(rid):
        ctx.log(f"{rid} is already recorded")
        return 0
    ctx.store.insert_release({
        "release_id": rid, "kind": "bundle", "artifact_name": bundles.JOBS, "artifact_version": version,
        "ss_console_sha": a.sha, "image_ref": ids.image_ref(a.image_ref), "notes": a.notes,
    })  # fmt: skip
    ctx.log(f"recorded fleet_releases {rid}")
    return 0
