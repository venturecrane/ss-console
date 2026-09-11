#!/usr/bin/env python3
"""control-probes.py - the negative-fire probe suite (ss#2387).

WHY THIS EXISTS. operator/contracts/runtime-controls.yaml holds the status of
every runtime safety control, and until now a status rested on a name. An
`enforced` row cited a `live_probe` that was a string with nothing behind it,
and the strongest evidence any row carried was "manual-observed", which proves a
control fired once, not that a regression would be caught. This runner plants a
synthetic violation per control and asks whether the control refuses it, so a
status has something behind it that can come back no.

WHAT IT DOES NOT DO. It wires nothing. An inert control stays inert: its probe
is expected to fail, EXPECTED_FAIL is the recorded outcome, and that is the
honest reading of a control with no dispatch caller. The inverse is a finding:
a control the registry calls inert whose probe FIRES means the registry is
wrong, and the run goes red on it.

OUTCOMES:
  PASS            the planted violation was refused or flagged, as declared.
  FAIL            it was not. The control did not fire. Loud (exit 1).
  EXPECTED_FAIL   the control is declared inert and did not fire. Expected.
  UNEXPECTED_PASS the control is declared inert and DID fire. Registry drift,
                  loud (exit 1).
  HOLD            the probe could not be run at all (no seat, no driver, a
                  transport failure). Loud (exit 2), never a green skip. This is
                  the reconcile-sends lesson inverted: that control sat green for
                  weeks scanning nothing because a missing secret exited 0.

Exit codes: 0 all attempted probes PASS/EXPECTED_FAIL; 1 a finding; 2 a HOLD or
nothing ran; 3 the self-test found a probe that cannot come back red.

USAGE
    operator/bin/control-probes.py                    # local probes (default)
    operator/bin/control-probes.py --kind all --seat smd-staging
    operator/bin/control-probes.py --self-test        # prove the runner can fail
    operator/bin/control-probes.py --emit-seat-script /tmp/seat-probes.sh
    operator/bin/control-probes.py --json
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATOR = REPO_ROOT / "operator"
REGISTRY = OPERATOR / "contracts" / "runtime-controls.yaml"
PROBE_SPECS = OPERATOR / "contracts" / "runtime-control-probes.yaml"
SEAT_PROBE = OPERATOR / "bin" / "seat-probe.sh"

PASS = "PASS"
FAIL = "FAIL"
EXPECTED_FAIL = "EXPECTED_FAIL"
UNEXPECTED_PASS = "UNEXPECTED_PASS"
HOLD = "HOLD"

#: Trees a dispatch caller could plausibly live in, for the caller-search
#: probes. Tests are excluded on purpose: a test calling a circuit breaker is
#: exactly the evidence that fooled everyone about sticky_stop for two months.
CALLER_SEARCH_DIRS = ("adapter", "safety-substrate", "workspace_broker", "bin", "connectors")
CALLER_SEARCH_EXCLUDE_PARTS = {"tests", "__pycache__", ".pytest_cache", ".ruff_cache"}
#: The substrate that DEFINES the arm, and this runner itself, are not callers.
CALLER_SEARCH_EXCLUDE_FILES = {"sticky_stop.py", "control-probes.py"}


class ProbeHold(RuntimeError):
    """The probe could not be evaluated. Never a pass, never a finding."""


@dataclass
class ProbeContext:
    """Everything a probe reaches the world through.

    Every seam a probe touches is on this object so a test can hand it a broken
    one. A probe whose target cannot be swapped cannot be shown to come back
    red, and a check that cannot fail has measured nothing.
    """

    substrate_dir: Path = OPERATOR / "safety-substrate"
    search_root: Path = OPERATOR
    seat: str | None = None
    run_seat: Callable[[str, list[str]], tuple[int, str]] | None = None
    neutered: bool = False


@dataclass
class ProbeResult:
    probe: str
    control: str
    kind: str
    status: str
    detail: str
    falsifier: str = ""

    @property
    def is_finding(self) -> bool:
        return self.status in (FAIL, UNEXPECTED_PASS)

    @property
    def is_hold(self) -> bool:
        return self.status == HOLD


@dataclass
class Suite:
    results: list[ProbeResult] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)  # probe, kind, control


# --------------------------------------------------------------------------- #
# substrate loading (the seam a falsifier swaps)                               #
# --------------------------------------------------------------------------- #


def load_substrate(ctx: ProbeContext, module: str):
    path = ctx.substrate_dir / f"{module}.py"
    if not path.is_file():
        raise ProbeHold(f"substrate module {module}.py not found under {ctx.substrate_dir}")
    spec = importlib.util.spec_from_file_location(f"_probe_{module}", path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


class _DictStore:
    """In-memory StickyStopStore. Structural typing, so no schema to keep."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], object] = {}

    async def get(self, customer: str, persona: str):
        return self.rows.get((customer, persona))

    async def put(self, state) -> None:
        self.rows[(state.customer, state.persona)] = state


class _ListSink:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def write(self, record) -> None:
        self.records.append(record)


def _machine(ctx: ProbeContext):
    """A sticky-stop machine over the substrate this context points at."""
    mod = load_substrate(ctx, "sticky_stop")
    machine = mod.StickyStopMachine(store=_DictStore(), audit_writer=_ListSink())
    if ctx.neutered:
        machine = _neuter(mod, machine)
    return mod, machine


def _neuter(mod, machine):
    """The deliberately broken target: a breaker that records but never stops.

    This is the shape of the defect the suite exists to catch (a control that is
    present, imported, and inert), and it is what the falsifier runs the probes
    against to show them red before they are trusted green.
    """

    class _Neutered:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def assert_allowed(self, **kw):
            return await self._inner.get_state(**kw)

        async def record_cost_cents(self, **kw):
            return await self._inner.get_state(customer=kw["customer"], persona=kw["persona"])

        async def record_tool_failure(self, **kw):
            return await self._inner.get_state(customer=kw["customer"], persona=kw["persona"])

        async def record_refusal(self, **kw):
            return await self._inner.get_state(customer=kw["customer"], persona=kw["persona"])

        async def record_runtime_seconds(self, **kw):
            return await self._inner.get_state(customer=kw["customer"], persona=kw["persona"])

    return _Neutered(machine)


def _await(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# local probe implementations                                                  #
# --------------------------------------------------------------------------- #


def probe_sticky_stop_cost_ladder(spec: dict, ctx: ProbeContext) -> tuple[bool, str]:
    """Spend past the cap, then ask permission. Fired = permission refused."""
    mod, machine = _machine(ctx)
    cap = mod.DEFAULT_THRESHOLDS.cost_daily_cents
    over = cap * mod.DEFAULT_THRESHOLDS.cost_hard_stop_pct // 100 + 1
    state = _await(machine.record_cost_cents(customer="probe", persona="probe", amount_cents=over))
    try:
        _await(machine.assert_allowed(customer="probe", persona="probe"))
    except mod.StickyStopError as exc:
        return True, f"{over}c against a {cap}c cap -> {state.level.value}; refused: {exc}"
    return False, (
        f"{over}c against a {cap}c cap left level={getattr(state.level, 'value', state.level)} "
        "and assert_allowed still granted permission"
    )


def probe_sticky_stop_pause_pin(spec: dict, ctx: ProbeContext) -> tuple[bool, str]:
    """Pin HARD_STOP the way the portal pause does, then ask permission."""
    mod, machine = _machine(ctx)
    store = machine._store if not ctx.neutered else machine._inner._store
    store.rows[("probe", "probe")] = mod.StickyStopState(
        customer="probe",
        persona="probe",
        level=mod.StickyStopLevel.HARD_STOP,
        updated_at="2026-08-17T00:00:00+00:00",
        reason="operator paused (probe)",
    )
    try:
        _await(machine.assert_allowed(customer="probe", persona="probe"))
    except mod.StickyStopError as exc:
        return True, f"pinned HARD_STOP refused a plain read: {exc}"
    return False, "a pinned HARD_STOP row still granted permission to act"


_ARM_DRIVERS = {
    "record_tool_failure": lambda m: [m.record_tool_failure(customer="probe", persona="probe") for _ in range(1)],
    "record_refusal": lambda m: [m.record_refusal(customer="probe", persona="probe")],
    "record_runtime_seconds": lambda m: [m.record_runtime_seconds(customer="probe", persona="probe", seconds=7200)],
}


def probe_sticky_stop_arm_unwired(spec: dict, ctx: ProbeContext) -> tuple[bool, str]:
    """Two questions, and only the second one decides.

    First: does the arm's ladder still climb? A broken ladder is a real finding
    even on an inert control, so it raises rather than quietly reporting inert.
    Second: does anything on a dispatch path call it? That is what `fired` means
    here, because an arm nothing calls cannot fire however good its code is.
    """
    arm = spec.get("arm") or ""
    mod, machine = _machine(ctx)
    if not ctx.neutered:
        _drive_ladder_or_raise(mod, machine, arm)
    callers = find_callers(ctx, arm)
    if callers:
        return True, f"dispatch caller(s) found for {arm}: {', '.join(callers)}"
    return False, (
        f"{arm} has no non-test caller under operator/; the arm cannot fire on a "
        "live turn (ss-console only: an overlay-side wiring is invisible here)"
    )


def _drive_ladder_or_raise(mod, machine, arm: str) -> None:
    """Precondition: the arm's own metering still works, before we ask whether
    anything CALLS it. A broken substrate would otherwise read as a wiring
    finding.

    ``record_runtime_seconds`` is checked differently, and deliberately. The
    two-state collapse (2026-09-02) left it the one arm with no HARD_STOP
    threshold: SOFT_STOP had been its only outcome, SOFT_STOP restricted
    nothing, so it now records the overrun and changes no level. Asserting a
    stop here would demand behaviour the substrate is documented NOT to have;
    asserting nothing would let a genuinely dead arm pass. So it must return a
    state AND leave the level alone.
    """
    calls = 20 if arm == "record_refusal" else (8 if arm == "record_tool_failure" else 1)
    state = None
    for _ in range(calls):
        if arm == "record_runtime_seconds":
            state = _await(machine.record_runtime_seconds(customer="probe", persona="probe", seconds=7200))
        else:
            state = _await(getattr(machine, arm)(customer="probe", persona="probe"))
    if arm == "record_runtime_seconds":
        if state is None or state.level is not mod.StickyStopLevel.OK:
            raise ProbeHold(
                "record_runtime_seconds changed the level "
                f"(level={getattr(state, 'level', None)}); it is documented to record an "
                "overrun and stop nothing, so the substrate and its contract disagree"
            )
        return
    if state is None or state.level is not mod.StickyStopLevel.HARD_STOP:
        raise ProbeHold(
            f"{arm} ladder did not stop after {calls} call(s) "
            f"(level={getattr(state, 'level', None)}); the substrate itself is broken"
        )


def find_callers(ctx: ProbeContext, symbol: str) -> list[str]:
    """Every non-test, non-defining file under the searched tree that CALLS `symbol`.

    Parsed, not grepped. The first grep version of this reported
    safety-substrate/refusal.py as a caller of record_refusal on the strength of
    a docstring sentence about it, which is precisely the false green a probe
    suite must not manufacture: a control declared live because prose mentions
    it. An ast walk sees calls and nothing else.
    """
    if not symbol:
        return []
    hits: list[str] = []
    roots = [ctx.search_root / d for d in CALLER_SEARCH_DIRS]
    roots = [r for r in roots if r.exists()] or [ctx.search_root]
    for root in roots:
        for path in root.rglob("*.py"):
            if set(path.parts) & CALLER_SEARCH_EXCLUDE_PARTS:
                continue
            if path.name in CALLER_SEARCH_EXCLUDE_FILES or path.name.startswith("test_"):
                continue
            if _calls_symbol(path, symbol):
                hits.append(str(path.relative_to(ctx.search_root)))
    return sorted(hits)


def _calls_symbol(path: Path, symbol: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == symbol:
            return True
        if isinstance(func, ast.Name) and func.id == symbol:
            return True
    return False


LOCAL_PROBES: dict[str, Callable[[dict, ProbeContext], tuple[bool, str]]] = {
    "sticky_stop_cost_ladder": probe_sticky_stop_cost_ladder,
    "sticky_stop_pause_pin": probe_sticky_stop_pause_pin,
    "sticky_stop_arm_unwired": probe_sticky_stop_arm_unwired,
}


# --------------------------------------------------------------------------- #
# seat probes                                                                  #
# --------------------------------------------------------------------------- #


def run_seat_command(slug: str, argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run([str(SEAT_PROBE), slug, *argv], capture_output=True, text=True, timeout=180)  # noqa: S603 - argv list under the repo's seat-probe script, no shell; argv is the probe spec's literal
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_seat(spec: dict, ctx: ProbeContext) -> tuple[bool, str]:
    import re

    if not ctx.seat:
        raise ProbeHold("no seat given; rerun with --seat <slug>")
    argv = list(spec.get("seat_command") or [])
    if not argv:
        raise ProbeHold(
            "no driver authored for this probe. The control is overlay-resident and "
            "nothing here can plant the violation, so the run holds rather than "
            "reporting a control it never asked about"
        )
    runner = ctx.run_seat or run_seat_command
    try:
        code, out = runner(ctx.seat, argv)
    except Exception as exc:
        raise ProbeHold(f"seat-probe transport failure: {exc}") from exc
    pattern = spec.get("expect_pattern") or ""
    if not pattern:
        raise ProbeHold("driver authored with no expect_pattern; nothing could be asserted")
    if re.search(pattern, out):
        return True, f"seat driver exit={code}; output matched /{pattern}/"
    return False, f"seat driver exit={code}; output did NOT match /{pattern}/"


# --------------------------------------------------------------------------- #
# the runner                                                                   #
# --------------------------------------------------------------------------- #


def load_specs() -> dict:
    return (yaml.safe_load(PROBE_SPECS.read_text(encoding="utf-8")) or {}).get("probes") or {}


def load_controls() -> dict:
    return (yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}).get("controls") or {}


def run_probe(spec: dict, ctx: ProbeContext) -> ProbeResult:
    name = spec.get("probe", "?")
    kind = spec.get("kind", "?")
    expect = spec.get("expect", "refuse")
    base = {"probe": name, "control": spec.get("control", "?"), "kind": kind}
    falsifier = (
        "the control fires -> UNEXPECTED_PASS" if expect == "expected-fail" else "the control does not fire -> FAIL"
    )
    try:
        if kind == "local":
            impl = LOCAL_PROBES.get(spec.get("runner") or "")
            if impl is None:
                raise ProbeHold(f"no implementation registered for runner {spec.get('runner')!r}")
            fired, detail = impl(spec, ctx)
        elif kind == "seat":
            fired, detail = probe_seat(spec, ctx)
        elif kind == "boot":
            raise ProbeHold(
                "probed on the seat by the gateway:startup activation handler, not from "
                f"here. Observe it with: {spec.get('observe') or '(no command recorded)'}"
            )
        else:
            raise ProbeHold(f"unknown kind {kind!r}")
    except ProbeHold as exc:
        return ProbeResult(**base, status=HOLD, detail=str(exc), falsifier=falsifier)
    except Exception as exc:  # noqa: BLE001 - a local probe has no transport to blame
        return ProbeResult(**base, status=FAIL, detail=f"probe raised: {exc!r}", falsifier=falsifier)
    if expect == "expected-fail":
        status = UNEXPECTED_PASS if fired else EXPECTED_FAIL
    else:
        status = PASS if fired else FAIL
    return ProbeResult(**base, status=status, detail=detail, falsifier=falsifier)


def run_suite(kinds: set[str], ctx: ProbeContext, specs: dict | None = None) -> Suite:
    suite = Suite()
    for name, spec in sorted((specs if specs is not None else load_specs()).items()):
        kind = spec.get("kind", "?")
        if kind not in kinds:
            suite.skipped.append((name, kind, spec.get("control", "?")))
            continue
        suite.results.append(run_probe(spec, ctx))
    return suite


def self_test(ctx: ProbeContext, specs: dict | None = None) -> list[tuple[str, bool, str]]:
    """Run every local probe against a deliberately broken target and require the
    answer to flip. A probe that reports the same thing either way is measuring
    nothing, and would otherwise sit in the suite looking green forever.
    """
    out: list[tuple[str, bool, str]] = []
    for name, spec in sorted((specs if specs is not None else load_specs()).items()):
        if spec.get("kind") != "local":
            continue
        real = run_probe(spec, ctx)
        broken = run_probe(spec, falsify(spec, ctx))
        flipped = real.status != broken.status and broken.status != HOLD
        out.append((name, flipped, f"real={real.status} broken={broken.status}"))
    return out


def falsify(spec: dict, ctx: ProbeContext) -> ProbeContext:
    """The deliberately broken target for a local probe, per runner.

    Two shapes, because the two probe families answer opposite questions: a
    refusal probe is falsified by a breaker that never stops; a caller-search
    probe is falsified by a tree that DOES contain a caller.
    """
    if spec.get("runner") == "sticky_stop_arm_unwired":
        return ProbeContext(
            substrate_dir=ctx.substrate_dir,
            search_root=_planted_caller_tree(spec.get("arm") or "record_tool_failure"),
            seat=ctx.seat,
            run_seat=ctx.run_seat,
        )
    return ProbeContext(substrate_dir=ctx.substrate_dir, search_root=ctx.search_root, neutered=True)


def _planted_caller_tree(arm: str) -> Path:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="control-probe-falsifier-"))
    (root / "adapter").mkdir(parents=True, exist_ok=True)
    (root / "adapter" / "fake_dispatch.py").write_text(
        f"# planted by the falsifier\nmachine.{arm}(customer='c', persona='p')\n",
        encoding="utf-8",
    )
    return root


# --------------------------------------------------------------------------- #
# reporting                                                                    #
# --------------------------------------------------------------------------- #


def render(suite: Suite) -> str:
    lines = ["", "NEGATIVE-FIRE PROBE SUITE", "=" * 72]
    for r in suite.results:
        lines.append(f"  {r.status:<15} {r.probe}  [{r.control}, {r.kind}]")
        lines.append(f"                  {r.detail}")
    if suite.skipped:
        lines.append("")
        lines.append("NOT ATTEMPTED IN THIS RUN (no status is claimed for these):")
        for name, kind, control in suite.skipped:
            lines.append(f"  {kind:<6} {name}  [{control}]")
    findings = [r for r in suite.results if r.is_finding]
    holds = [r for r in suite.results if r.is_hold]
    lines += [
        "",
        "=" * 72,
        f"{len(suite.results)} attempted, {len(findings)} finding(s), {len(holds)} hold(s), "
        f"{len(suite.skipped)} not attempted",
        "A HOLD is not a pass. A control nobody could ask about is a control nobody proved.",
    ]
    return "\n".join(lines)


def emit_seat_script(specs: dict, path: Path) -> int:
    """Write the runnable seat-side script for every seat probe.

    Where a driver is authored it emits the real seat-probe.sh invocation. Where
    one is not, it emits the violation and the expectation as a numbered manual
    step and exits non-zero, because a script that shrugged at an unauthored
    probe would read as a pass.
    """
    seat = [(n, s) for n, s in sorted(specs.items()) if s.get("kind") == "seat"]
    body = [
        "#!/usr/bin/env bash",
        "# GENERATED by operator/bin/control-probes.py --emit-seat-script. Do not edit.",
        "# Runs every seat-kind negative-fire probe against ONE seat.",
        "#",
        "# Usage: from the ss-console root, bash <this-script> <slug>",
        "set -uo pipefail",
        'SLUG="${1:?usage: $0 <customer-slug>}"',
        # Repo-relative on purpose: an absolute path baked in here would name the
        # worktree the script happened to be generated in.
        'PROBE="${SMD_SEAT_PROBE:-operator/bin/seat-probe.sh}"',
        '[ -x "$PROBE" ] || { echo "run me from the ss-console root (no $PROBE here)"; exit 2; }',
        "rc=0",
        "",
    ]
    for i, (name, spec) in enumerate(seat, 1):
        argv = " ".join(spec.get("seat_command") or [])
        body += [
            f"echo '--- {i}. {name} [{spec.get('control')}]'",
            f"echo 'violation: {_one_line(spec.get('violation'))}'",
            f"echo 'expect:    {spec.get('expect')}'",
        ]
        if argv:
            body += [
                f'"$PROBE" "$SLUG" {argv} || rc=1',
                f"echo 'match against: {spec.get('expect_pattern')}'",
            ]
        else:
            body += [
                "echo 'HOLD: no driver authored for this probe; it cannot be run here.'",
                "rc=2",
            ]
        body.append("")
    body += [
        'if [ "$rc" -ne 0 ]; then echo "seat probe suite did not complete clean (rc=$rc)"; fi',
        'exit "$rc"',
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return len(seat)


def _one_line(text) -> str:
    return " ".join(str(text or "").split()).replace("'", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kind", default="local", choices=["local", "seat", "boot", "all"])
    ap.add_argument("--seat", help="customer slug; required for seat-kind probes")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    ap.add_argument("--emit-seat-script", type=Path, help="write the seat-side script and exit")
    args = ap.parse_args(argv)

    specs = load_specs()

    if args.emit_seat_script:
        count = emit_seat_script(specs, args.emit_seat_script)
        print(f"wrote {args.emit_seat_script} ({count} seat probe(s))")
        return 0

    ctx = ProbeContext(seat=args.seat)

    if args.self_test:
        rows = self_test(ctx, specs)
        for name, flipped, detail in rows:
            print(f"  {'CAN FAIL' if flipped else 'CANNOT FAIL':<12} {name}  ({detail})")
        broken = [n for n, ok, _ in rows if not ok]
        if not rows:
            print("self-test ran zero probes")
            return 3
        print(f"\n{len(rows) - len(broken)}/{len(rows)} local probe(s) shown red before green")
        return 3 if broken else 0

    kinds = {"local", "seat", "boot"} if args.kind == "all" else {args.kind}
    suite = run_suite(kinds, ctx, specs)

    if args.as_json:
        print(
            json.dumps(
                {
                    "results": [r.__dict__ for r in suite.results],
                    "not_attempted": [{"probe": n, "kind": k, "control": c} for n, k, c in suite.skipped],
                },
                indent=2,
            )
        )
    else:
        print(render(suite))

    if not suite.results:
        print("HOLD: zero probes ran. A suite that asked nothing proved nothing.", file=sys.stderr)
        return 2
    if any(r.is_finding for r in suite.results):
        return 1
    if any(r.is_hold for r in suite.results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
