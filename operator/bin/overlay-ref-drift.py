#!/usr/bin/env python3
"""overlay-ref-drift.py — detect Machines running a stale overlay.

WHY THIS EXISTS: an ``OVERLAY_REF`` bump only reaches a customer Machine when
someone manually reprovisions it. Nothing tracked which live Machines were
behind, so a bump could silently leave Machines on an old overlay and the only
"reminder" was a human remembering. This closes that gap: it reads each
Machine's RUNNING overlay commit from the live ``config`` runtime-read seam and
compares it to the ref pinned in ``operator/templates/Dockerfile`` (the desired
state), so drift is a surfaced fact, not something to remember.

Run on demand or on a schedule. Exit code is gate-friendly:
  0  every reachable Machine is on the pinned ref (no drift)
  1  at least one Machine is DRIFTED (or reachable but its ref is unknown)
  2  --strict and at least one Machine was unreachable/unconfigured

Reachability needs the seam master key, so run under Infisical:
  infisical run --env=prod --path=/ss --silent -- operator/bin/overlay-ref-drift.py

Usage:
  overlay-ref-drift.py [SLUG ...] [--strict] [--dockerfile PATH] [--customers-dir PATH]

With no SLUG args it checks every customer dir under operator/customers/
(excluding _template). An undeployed/paused customer shows as "unreachable" —
that is informational, not drift, unless --strict.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import seam_pull

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DOCKERFILE = _REPO_ROOT / "operator" / "templates" / "Dockerfile"
_DEFAULT_CUSTOMERS_DIR = _REPO_ROOT / "operator" / "customers"

_OVERLAY_REF_RE = re.compile(r'^\s*ARG\s+OVERLAY_REF="([0-9a-fA-F]{7,40})"', re.MULTILINE)


@dataclass
class DriftResult:
    slug: str
    status: str  # current | drift | unknown | unreachable | unconfigured
    running_ref: Optional[str]
    source: Optional[str]
    detail: str = ""


def desired_ref_from_dockerfile(path: Path) -> str:
    """Parse the pinned ``ARG OVERLAY_REF`` from the Dockerfile (desired state)."""
    text = path.read_text(encoding="utf-8")
    m = _OVERLAY_REF_RE.search(text)
    if not m:
        raise ValueError(f'no ARG OVERLAY_REF="..." found in {path}')
    return m.group(1)


def discover_slugs(customers_dir: Path) -> list[str]:
    """Every customer dir under ``customers_dir`` except the template."""
    if not customers_dir.is_dir():
        return []
    return sorted(
        p.name for p in customers_dir.iterdir() if p.is_dir() and p.name != "_template" and not p.name.startswith(".")
    )


def refs_match(desired: str, running: Optional[str]) -> bool:
    """Match exact, or by common prefix so a short ref equals its full SHA."""
    if not running:
        return False
    a, b = desired.lower(), running.lower()
    return a == b or a.startswith(b) or b.startswith(a)


@dataclass
class RunningRef:
    """One Machine's RUNNING overlay ref, as read off the live runtime seam.

    ``status`` is ``read`` only when a value actually came back. Every other
    status means the running ref is UNKNOWN, and a caller gating on it must
    refuse rather than assume current: a check that cannot fail measured
    nothing.
    """

    slug: str
    status: str  # read | unknown | unreachable | unconfigured
    value: Optional[str]
    source: Optional[str] = None
    detail: str = ""


def read_running_ref(
    slug: str,
    make_client: Callable[[str], Optional["seam_pull.SeamClient"]],
) -> RunningRef:
    """Resolve one slug's running overlay ref through the runtime-read seam.

    The single transport path for "what is this Machine actually running". The
    drift report below and the shadow-firm release gate
    (``operator/rehearsal/run.py``) both call it, so neither can drift from the
    other's idea of how the running ref is read, or of what counts as having
    read it.
    """
    try:
        client = make_client(slug)
    except Exception as exc:  # noqa: BLE001 - defensive; a factory error is unreachable
        return RunningRef(slug, "unreachable", None, None, f"client init: {exc}")
    if client is None:
        return RunningRef(slug, "unconfigured", None, None, "seam env not set")
    try:
        snap = client.read_config()
    except Exception as exc:  # noqa: BLE001 - paused/undeployed/unreachable Machine
        return RunningRef(slug, "unreachable", None, None, f"{type(exc).__name__}: {exc}")
    ref_obj = snap.get("overlay_ref") if isinstance(snap, dict) else None
    value = ref_obj.get("value") if isinstance(ref_obj, dict) else None
    source = ref_obj.get("source") if isinstance(ref_obj, dict) else None
    if value is None:
        return RunningRef(slug, "unknown", None, source, "snapshot has no overlay_ref.value")
    return RunningRef(slug, "read", value, source)


def classify(
    desired: str,
    slugs: list[str],
    make_client: Callable[[str], Optional["seam_pull.SeamClient"]],
) -> list[DriftResult]:
    """Pure core: resolve each slug's running ref and compare to ``desired``.

    ``make_client(slug)`` returns a SeamClient or None (unconfigured). Any
    transport error reading the seam is classified ``unreachable`` (a paused or
    not-yet-deployed Machine), never a crash — drift detection must be total.
    The read itself is :func:`read_running_ref`; this function only compares.
    """
    results: list[DriftResult] = []
    for slug in slugs:
        observed = read_running_ref(slug, make_client)
        if observed.status != "read":
            results.append(DriftResult(slug, observed.status, None, observed.source, observed.detail))
        elif refs_match(desired, observed.value):
            results.append(DriftResult(slug, "current", observed.value, observed.source))
        else:
            results.append(DriftResult(slug, "drift", observed.value, observed.source, "running ref != pinned ref"))
    return results


def _short(ref: Optional[str]) -> str:
    return ref[:12] if ref else "-"


def render(desired: str, results: list[DriftResult]) -> str:
    lines = [f"Desired overlay ref (Dockerfile): {desired[:12]}", ""]
    width = max((len(r.slug) for r in results), default=4)
    for r in results:
        mark = {
            "current": "OK   ",
            "drift": "DRIFT",
            "unknown": "?    ",
            "unreachable": "unrch",
            "unconfigured": "uncfg",
        }.get(r.status, "?    ")
        line = f"  [{mark}] {r.slug.ljust(width)}  running={_short(r.running_ref)}"
        if r.detail:
            line += f"  ({r.detail})"
        lines.append(line)
    drifted = [r.slug for r in results if r.status in ("drift", "unknown")]
    unreachable = [r.slug for r in results if r.status in ("unreachable", "unconfigured")]
    lines.append("")
    if drifted:
        lines.append(f"DRIFT: {len(drifted)} Machine(s) behind the pinned ref: {', '.join(drifted)}")
        lines.append("  → reprovision each: yes s | operator/bin/reprovision.sh <slug>")
    else:
        lines.append("No drift: every reachable Machine is on the pinned overlay ref.")
    if unreachable:
        lines.append(f"Unreachable/unconfigured (not assessed): {', '.join(unreachable)}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detect Machines running a stale overlay ref.")
    parser.add_argument("slugs", nargs="*", help="customer slugs to check (default: all)")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on unreachable/unconfigured too")
    parser.add_argument("--dockerfile", default=str(_DEFAULT_DOCKERFILE))
    parser.add_argument("--customers-dir", default=str(_DEFAULT_CUSTOMERS_DIR))
    args = parser.parse_args(argv)

    desired = desired_ref_from_dockerfile(Path(args.dockerfile))
    slugs = args.slugs or discover_slugs(Path(args.customers_dir))
    if not slugs:
        print("no customer slugs to check", file=sys.stderr)
        return 0

    results = classify(desired, slugs, seam_pull.seam_client_from_env)
    print(render(desired, results))

    has_drift = any(r.status in ("drift", "unknown") for r in results)
    has_unreachable = any(r.status in ("unreachable", "unconfigured") for r in results)
    if has_drift:
        return 1
    if args.strict and has_unreachable:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
