#!/usr/bin/env python3
"""run.py -- drive the shadow firm against a rig seat and score it mechanically.

WHY THIS EXISTS (ss#2389). We rehearse once and then go live. Every incident we
have had -- an unaudited direct-API send (ss#2258), cross-matter content
(ss#2167), a fabricated matter number under failure (ss#2168) -- was found by a
person noticing, after the fact, on a seat that had already passed its one
rehearsal. This is the standing version: the same hostile acts, replayed on
demand against a rig, scored from artifacts rather than from prose, and citable
by a release gate.

THE HARD LINE. No scenario may target a client seat, a client-visible address,
or a production tenant. That is enforced in ``scope.py`` and checked before any
scenario is loaded: an unallowlisted address anywhere in a scenario (including
inside a message body) or a seat whose own descriptor is not a rig aborts the
run with exit 2. There is no flag that relaxes it.

ARMING IS EXPLICIT. Without ``--drive`` the runner prints the plan and sends
nothing. That is not politeness: the credentials this suite needs are ambient in
any shell that has run ``infisical run``, and during development an invocation
meant as a dry run put three live probes into the rig seat within a minute. A
tool that fires adversarial mail the moment it is typed is one shell environment
away from firing it at something that matters.

USAGE
    infisical run --env=prod --path=/ss -- \\
        operator/rehearsal/run.py --seat pilot-smokeball --drive

    --drive                actually perform the hostile acts; without it, plan only
    --list                 print the registry and exit; drives nothing
    --only ID[,ID...]      run a subset
    --overlay-ref REF      the candidate ref this run certifies; the rig must
                           already be RUNNING it or the run is refused
                           (default: ARG OVERLAY_REF in operator/templates/Dockerfile)
    --inject NAME          declare a fault you have already injected on the rig
    --out DIR              where the report lands (default .stitch/shadow-firm/)

THE RIG MUST ALREADY BE RUNNING THE CANDIDATE. A run stamps the candidate ref
into its own id (report.py), so before ss#2531 an id reading "green against ref
X" could have been produced by a rig sitting on the previous release: the ref
was a label the runner was handed, never a fact it checked. With --drive the
runner now reads the rig's RUNNING ref off the runtime-read seam
(``drivers.read_running_ref``; since the 2026-10-22 fold that ref is the
ss-console commit the seat's image was built from) and refuses unless it equals
the candidate. A ref it cannot read refuses too, because "cannot evaluate" must
not read as "permitted". The consequence for the release order is that the rig
has the candidate RELEASED to it (``fleet release ... --only <rig>``) BEFORE the
suite runs, not after.

EXIT CODES
    0  green: every scenario PASSED. Only this run may be cited by a release gate.
    1  at least one scenario FAILED.
    3  nothing failed but something was SKIPPED, so the suite is incomplete.
       Deliberately not 0: a skipped scenario proves nothing and must never
       read as a pass.
    2  refused before driving anything (scope violation, bad registry, no seat,
       or the rig is not running the candidate ref).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from rehearsal import drivers, registry, report, scope
from rehearsal.scoring import FAIL, PASS, SKIPPED, LegObservation, score_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]

EXIT_GREEN = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_INCOMPLETE = 3


def pinned_overlay_ref() -> str:
    """The ref a run defaults to certifying: this checkout's commit.

    Since the fold (ADR 0095) the seat runtime is built from this tree and a
    seat's running ref IS an ss-console sha, so the candidate is the commit the
    image under test was built from, never a value someone types in. "unknown"
    when this is not a git checkout; the gate then refuses to drive.
    """
    try:
        out = subprocess.run(  # noqa: S603 - literal git argv against this checkout
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"


def make_seam_client(slug: str):
    """The live runtime-read seam factory for one seat.

    A module-level seam on purpose. It is the single line of the running-ref
    gate that touches a network, so a test can substitute a fake client and
    still exercise the real gate rather than a copy of it.
    """
    from bin.lib import seam_pull

    return seam_pull.seam_client_from_env(slug)


def rig_running_ref(seat: str, candidate: str) -> str | None:
    """Refuse unless the rig is RUNNING the ref this run claims to certify.

    Returns the observed running ref, or None when the run must be refused. The
    refusal is printed here, in the caller's words, because the person who
    typed --drive is the person who has to act on it.

    Fail-closed in both directions. A mismatch refuses, and so does a ref that
    cannot be read at all: an unreachable seat is not an excuse to assume the
    seat is current, it is the absence of the only evidence this gate exists to
    collect.
    """
    observed = drivers.read_running_ref(seat, make_seam_client)
    if observed.status != "read" or not observed.value:
        print(
            f"REFUSED: cannot read the overlay ref `{seat}` is running "
            f"({observed.status}: {observed.detail or 'no detail'}). Driving now would "
            f"stamp {candidate[:12]} into a run id with no evidence the rig is on it, "
            "so nothing was driven.",
            file=sys.stderr,
        )
        return None
    if not drivers.refs_match(candidate, observed.value):
        print(
            f"REFUSED: `{seat}` is running {observed.value[:12]}, and this run would "
            f"certify {candidate[:12]}. Release {candidate[:12]} to {seat} first: "
            f"operator/bin/fleet release <img id built from {candidate[:12]}> --only {seat}",
            file=sys.stderr,
        )
        return None
    return observed.value


def _print_registry(scenarios: list[dict]) -> None:
    for scenario in scenarios:
        requires = ", ".join(scenario.get("requires") or []) or "nothing"
        print(f"{scenario['id']}  [{scenario['incident_class']}]  replays {', '.join(scenario['replays'])}")
        print(f"    {' '.join(str(scenario['title']).split())}")
        print(f"    requires: {requires}")
        for leg in scenario["legs"]:
            kinds = ", ".join(str(e["kind"]) for e in leg["expect"])
            print(f"      - {leg['id']}: {leg['drive']['kind']} -> expects {kinds}")
    print(f"\n{len(scenarios)} scenario(s). Nothing driven.")


def run_suite(args: argparse.Namespace) -> int:
    try:
        scenarios = registry.load_scenarios()
    except (registry.SchemaError, scope.ScopeViolation) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - {s["id"] for s in scenarios}
        if unknown:
            print(f"REFUSED: no such scenario(s): {sorted(unknown)}", file=sys.stderr)
            return EXIT_REFUSED
        scenarios = [s for s in scenarios if s["id"] in wanted]

    if args.just_list:
        _print_registry(scenarios)
        return EXIT_GREEN

    try:
        config = drivers.load_seat_config(args.seat)
    except FileNotFoundError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        scope.assert_seat_drivable(args.seat, config)
    except scope.ScopeViolation as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    if not args.drive:
        print(f"PLAN ONLY. Nothing sent. Target seat: {args.seat} (seat.kind={scope.seat_kind(config)})")
        _print_registry(scenarios)
        print(
            "Rig running-ref check: not performed in plan mode (it reads the live seam). "
            "With --drive the runner refuses unless the rig is already running the candidate ref."
        )
        print("Re-run with --drive to perform these acts against the seat.")
        return EXIT_REFUSED

    candidate = args.overlay_ref or pinned_overlay_ref()
    running_ref = rig_running_ref(args.seat, candidate)
    if running_ref is None:
        return EXIT_REFUSED

    capabilities = drivers.probe_capabilities(args.seat, config, inject=args.inject)
    audit = drivers.AuditReader(capabilities.audit_seam) if capabilities.audit_seam else None

    run = report.Run(
        seat=args.seat,
        overlay_ref=candidate,
        running_ref=running_ref,
        started_at=report.now_stamp(),
    )
    for name, reason in sorted(capabilities.reasons.items()):
        run.notes.append(f"capability `{name}` unavailable: {reason}")

    for scenario in scenarios:
        print(f"== {scenario['id']} ({scenario['incident_class']})")
        observations: dict[str, LegObservation] = {}
        # The scenario window, opened before its first leg. A console-side leg
        # reconciles over THIS window, not its own: the sends it must account for
        # were provoked by an earlier leg and would fall outside a window that
        # opened when the reconcile leg started.
        scenario_start = datetime.now(timezone.utc)
        scenario_mark: str | None = None
        if audit is not None:
            try:
                scenario_mark = audit.snapshot()
            except Exception as exc:  # noqa: BLE001 -- unreadable ledger is UNKNOWN, never empty
                run.notes.append(f"{scenario['id']}: audit baseline read failed: {exc}")
        for leg in scenario["legs"]:
            print(f"   - {leg['id']} ...", flush=True)
            console_side = str((leg.get("drive") or {}).get("kind")) == "console_reconcile"
            observations[str(leg["id"])] = drivers.drive_leg(
                leg,
                capabilities=capabilities,
                requirements=list(scenario.get("requires") or []),
                audit=audit,
                settle_s=args.settle,
                window_start=scenario_start if console_side else None,
                audit_mark=scenario_mark if console_side else None,
            )
        result = score_scenario(scenario, observations)
        run.results.append(result)
        print(f"   {result.outcome}: {' '.join(str(result.reason).split())[:160]}")

    json_path, markdown_path = report.write(run, {s["id"]: s for s in scenarios}, Path(args.out) if args.out else None)
    counts = run.counts
    print(f"\nrun id: {run.run_id}")
    print(f"report: {markdown_path}")
    print(f"json:   {json_path}")
    print(f"{counts.get(PASS, 0)} pass / {counts.get(FAIL, 0)} fail / {counts.get(SKIPPED, 0)} skipped")
    if run.is_green:
        print("GREEN. This id may be cited by an OVERLAY_REF bump PR.")
        return EXIT_GREEN
    if counts.get(FAIL, 0):
        print("NOT GREEN: a scenario failed. Do not bump OVERLAY_REF on this ref.")
        return EXIT_FAILED
    print(
        "NOT GREEN: scenarios were skipped, so the suite is incomplete. A skipped scenario "
        "did not run and proves nothing."
    )
    return EXIT_INCOMPLETE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=str(__doc__).split("\n")[0])
    parser.add_argument("--seat", help="the rig seat slug to play hostile against")
    parser.add_argument("--only", default=None, help="comma-separated scenario ids")
    parser.add_argument(
        "--drive",
        action="store_true",
        help="actually perform the hostile acts; without it the runner plans and sends nothing",
    )
    parser.add_argument("--list", action="store_true", dest="just_list")
    parser.add_argument("--overlay-ref", default=None, dest="overlay_ref")
    parser.add_argument("--inject", default=None, help="name a fault already injected on the rig")
    parser.add_argument("--out", default=None)
    parser.add_argument("--settle", type=int, default=30, help="seconds a console-side leg waits")
    args = parser.parse_args(argv)
    if not args.just_list and not args.seat:
        parser.error("--seat is required (or --list, which drives nothing)")
    return run_suite(args)


if __name__ == "__main__":
    sys.exit(main())
