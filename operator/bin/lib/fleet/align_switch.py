"""Whether a release run by hand moves a lane seat's jobs bundle with its image (2026-11-17).

THE INCIDENT. A seat that authors a job lane reads its jobs bundle pointer only
at boot, so the image step writes that pointer, before the deploy, at the
newest bundle the new image qualifies for (bundle_align.py). The aligner exists
only while the switch is on (artifact_tick.BUNDLE_SWITCH). The tick reads the
switch from its environment, where release-tick.yml puts the repository
variable. A release run from a laptop had no such environment, so for it the
switch was silently off: on 2026-11-17 an image went to a client lane seat with
its pointer untouched, the seat's self-test refused the older bundle
(jobs-bundle-built-for-image), and the seat was rolled back and sat un-updated
for about an hour and forty minutes.

THE RULE. A release run by hand (``fleet release <id> [--only SEAT]``, never
``--tick``) reads the same switch from the same source:

  * from this run's environment when it names the switch at all (CI's urgent
    one-seat path passes the repository variable, exactly as the tick does);
  * otherwise from the repository variable itself, with ``gh variable get``
    on the operator's own GitHub login (no new credential).

Then, before anything is deployed, it logs one line or refuses:

  on                         "bundle alignment: on (<where it was read>)", and
                             every lane seat's pointer is written before its
                             deploy, exactly as in the tick.
  off or unreadable, no seat in the run authors a lane
                             "bundle alignment: off ...; no seat in this run
                             authors a job lane": nothing a pointer decides.
  off or unreadable, a lane seat in the run
                             refused (exit 4) before anything runs, saying how
                             to run it with alignment on. ``--no-bundle-align``
                             deploys anyway, loudly: the run logs
                             "bundle alignment: OFF" and each such seat's
                             fleet_rollouts detail begins with it.

The tick is unchanged: it never calls this module and still reads its
environment alone (bundle_align_for with no ``enabled``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from . import bundles
from .artifact_tick import BUNDLE_SWITCH
from .rollout import OFF_MARK
from .runner import Refusal, Runner, ToolingError

# Where the switch lives: a repository variable of this repo.
REPO_SLUG = "venturecrane/ss-console"
GH_READ_TIMEOUT_S = 30


@dataclass(frozen=True)
class Switch:
    on: bool
    # Where the value came from, or why it could not be read: for the log line.
    source: str
    readable: bool


@dataclass(frozen=True)
class Decision:
    on: bool
    # Lane seats this run deploys with their pointer untouched (only ever by --no-bundle-align).
    unaligned: frozenset[str]


def read_switch(runner: Runner, environ: Mapping[str, str] | None = None) -> Switch:
    """The switch as the tick would see it: this run's environment, else the repository variable."""
    env = os.environ if environ is None else environ
    if BUNDLE_SWITCH in env:
        value = env[BUNDLE_SWITCH]
        return Switch(value == "true", f"{BUNDLE_SWITCH}={value!r} in this run's environment", True)
    argv = ["gh", "variable", "get", BUNDLE_SWITCH, "--repo", REPO_SLUG]
    try:
        res = runner.run(argv, timeout=GH_READ_TIMEOUT_S)
    except ToolingError as exc:
        return Switch(False, f"the repository variable could not be read ({exc})", False)
    if not res.ok:
        said = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        return Switch(False, f"the repository variable could not be read (gh rc={res.rc}: {said[0][:200]})", False)
    value = res.stdout.strip()
    return Switch(value == "true", f"repository variable {BUNDLE_SWITCH} is {value!r}", True)


def _refusal(sw: Switch, lane: set[str], release_id: str) -> str:
    seats = ", ".join(sorted(lane))
    why = "is not 'true'" if sw.readable else "could not be read"
    return (
        f"bundle alignment is off: the switch {why} ({sw.source}), and {seats} authors a job lane. Deployed "
        "like this, the image boots with the seat's jobs bundle pointer untouched, and a seat whose bundle floor "
        "moved refuses the image at its self-test and is rolled back (2026-11-17). Nothing was deployed. Check "
        f"the switch with `gh variable get {BUNDLE_SWITCH} --repo {REPO_SLUG}`; when it is true, run again with "
        f"`{BUNDLE_SWITCH}=true` in front: `{BUNDLE_SWITCH}=true operator/bin/fleet release {release_id} "
        "--only <seat>` (inside the same `infisical run` as before). To deploy with the pointer untouched "
        "anyway, add --no-bundle-align"
    )


def add_argument(parser: Any) -> None:
    """``fleet release --no-bundle-align``."""
    parser.add_argument(
        "--no-bundle-align",
        action="store_true",
        help="deploy an image to a seat that authors a job lane even though bundle alignment is off: its jobs "
        "bundle pointer is left as it is, and the run and the seat's rollout record say so (align_switch.py)",
    )


def decide(ctx: Any, release_id: str, only: str | None, allow_off: bool) -> Decision:
    """Log whether this hand-run release aligns lane seats' bundle pointers, or refuse (see the module doc).

    The run's seats are the ringed ones (``only`` alone under ``--only``); the switch is read first.
    """
    sw = read_switch(ctx.runner)
    if sw.on:
        ctx.log(f"bundle alignment: on ({sw.source})")
        return Decision(True, frozenset())
    seats = [seat for ring in ctx.rings() for seat in ring.seats if only is None or seat == only]
    lane = bundles.authoring_seats(ctx.repo, seats)
    if not lane:
        ctx.log(f"bundle alignment: off ({sw.source}); no seat in this run authors a job lane")
        return Decision(False, frozenset())
    if not allow_off:
        raise Refusal(_refusal(sw, lane, release_id))
    ctx.log(
        f"WARNING {OFF_MARK}; {sw.source}: {', '.join(sorted(lane))} deploy with the jobs bundle pointer "
        "untouched, and a seat whose bundle floor moved refuses the image at its self-test and is rolled back"
    )
    return Decision(False, frozenset(lane))
