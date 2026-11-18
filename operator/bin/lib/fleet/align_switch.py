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

  * from this run's environment when it names the switch at all. That is CI:
    release-tick.yml passes the repository variable into every run, the urgent
    one-seat path included, and an unset variable arrives as "", which is off,
    exactly as the tick reads it;
  * otherwise from the repository variable itself, with ``gh variable get``
    on the operator's own GitHub login (no new credential). "Not found" is a
    readable answer: the variable is unset, so off.

Then, before anything is deployed, it logs one line or refuses:

  on                    "bundle alignment: on (<where it was read>)", and every
                        lane seat's pointer is written before its deploy, as in
                        the tick.
  readably off          someone turned the switch off, and that is a decision:
                        the run does what the tick does, deploying the image
                        with the pointer untouched, and says so. It logs
                        "bundle alignment: OFF (switch off): image released
                        without moving the jobs bundle", and each lane seat's
                        fleet_rollouts detail begins with the same words.
  cannot be read        refused (exit 4) before anything runs, for a run that
                        holds a lane seat, saying how to run it with the switch
                        read. ``--no-bundle-align`` deploys anyway, marked
                        "OFF (--no-bundle-align)" the same way. This is the
                        2026-11-17 case: "could not tell" must never read as
                        "off".

The tick reads its environment alone, as before (bundle_align_for with no
``enabled``); when that says off it logs and records the same OFF line
(:func:`tick_off`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from . import bundles
from .artifact_tick import BUNDLE_SWITCH
from .rollout import off_note
from .runner import Refusal, Runner, ToolingError

# Where the switch lives: a repository variable of this repo.
REPO_SLUG = "venturecrane/ss-console"
GH_READ_TIMEOUT_S = 30
# Why a lane seat rolled without alignment, as its rollout row says it.
SWITCH_OFF = "switch off"
FORCED_OFF = "--no-bundle-align"


@dataclass(frozen=True)
class Switch:
    on: bool
    # Where the value came from, or why it could not be read: for the log line.
    source: str
    readable: bool


@dataclass(frozen=True)
class Decision:
    on: bool
    # Lane seats this run deploys with their pointer untouched, and why (SWITCH_OFF or FORCED_OFF).
    unaligned: frozenset[str]
    why: str = ""

    def plan_kw(self) -> dict[str, Any]:
        """The rollout.ImagePlan fields this decision sets."""
        return {"unaligned": self.unaligned, "unaligned_why": self.why}


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
        said = ((res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"])[0][:200]
        if "not found" in said.lower():
            return Switch(False, f"repository variable {BUNDLE_SWITCH} is not set ({said})", True)
        return Switch(False, f"the repository variable could not be read (gh rc={res.rc}: {said})", False)
    value = res.stdout.strip()
    return Switch(value == "true", f"repository variable {BUNDLE_SWITCH} is {value!r}", True)


def _refusal(sw: Switch, lane: set[str], release_id: str) -> str:
    return (
        f"the jobs bundle switch could not be read ({sw.source}), and {', '.join(sorted(lane))} authors a job "
        "lane, so this run cannot tell whether to move its jobs bundle with the image (2026-11-17: deployed "
        "with the pointer untouched, a seat whose bundle floor moved refuses the image at its self-test and is "
        "rolled back). Nothing was deployed. Read the switch with "
        f"`gh variable get {BUNDLE_SWITCH} --repo {REPO_SLUG}` (with a stale token cleared from the shell) and run again "
        f"with its value in front: `{BUNDLE_SWITCH}=<true|false> operator/bin/fleet release {release_id} "
        "--only <seat>` (inside the same `infisical run` as before). To deploy with the pointer untouched "
        f"anyway, add {FORCED_OFF}"
    )


def add_argument(parser: Any) -> None:
    """``fleet release --no-bundle-align``."""
    parser.add_argument(
        FORCED_OFF,
        action="store_true",
        help="deploy an image to a seat that authors a job lane even though the jobs bundle switch could not be "
        "read: its jobs bundle pointer is left as it is, and the run and the seat's rollout record say so "
        "(align_switch.py)",
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
    if sw.readable:
        why = SWITCH_OFF
    elif allow_off:
        why = FORCED_OFF
    else:
        raise Refusal(_refusal(sw, lane, release_id))
    ctx.log(f"WARNING {off_note(why)} ({sw.source}; lane seats: {', '.join(sorted(lane))})")
    return Decision(False, frozenset(lane), why)


def tick_off(ctx: Any, seats: list[str]) -> Decision:
    """The tick with its switch off: the same OFF line, logged once and recorded on each lane seat's row.

    Which seats author a lane is read from the repo as the bundle step reads it;
    a seat whose customer.yaml cannot be read is left unmarked rather than
    stopping the tick (the tick deployed it before this note existed).
    """
    try:
        lane = bundles.authoring_seats(ctx.repo, seats)
    except ToolingError as exc:
        ctx.log(f"tick: {off_note(SWITCH_OFF)} (which seats author a lane could not be read: {exc})")
        return Decision(False, frozenset(), SWITCH_OFF)
    if lane:
        ctx.log(f"tick: WARNING {off_note(SWITCH_OFF)} (lane seats: {', '.join(sorted(lane))})")
    return Decision(False, frozenset(lane), SWITCH_OFF)
