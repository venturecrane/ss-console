"""When the audit's controls make a round INVALID: a rate, not a single row.

A control re-runs a claim against a page from a DIFFERENT exhibit, and must
come back unsupported; a reverse control re-reads a text-SUPPORTED claim on
its page images, and must agree. Until 2026-10-24 one wrong row of either
kind made the round INVALID, which failed the job for SMD to read by hand.
On real records one wrong row is usually not a broken verifier:

* a control page that carries the claim (a diagnosis index, a scanned
  summary) is retired by rule (``retired.retire_if_carried``, which reads the
  vision transcript when the page has no text layer);
* a SUPPORTED control that is not retired is RE-TESTED once against the next
  exhibit of yet another institution (``run.Round.retest_controls``). When the
  re-test comes back unsupported, the verifier discriminated and the first
  control page was badly chosen: the original is retired by rule. A control
  SUPPORTED twice is "bad", and is more likely a generic claim ("cervical
  strain") that really appears in many records than a rubber stamp;
* a reverse control that disagrees hands its claim the image verdict, so the
  repair rounds correct or drop the claim (``run.Round.run_cluster``).

So a round is INVALID only when the bad rows reach a rate no generic claim
explains: ``max(3, ceil(1% of the rows run))`` (2026-10-24: Curtis's shape,
about 76 controls with 2 bad, must pass; 3 bad must not). For controls only,
also when EVERY control run is bad (a matter with one or two controls, all
SUPPORTED twice: the rubber-stamp signature itself); a reverse control's
disagreement already hands its claim to the repair, so it never needs that
clause. Controls and reverse controls are counted separately. Every bad row and the arithmetic are logged and written to
``audit-controls.json`` beside the results, so a replay can measure the rule.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from . import retired

FILE = "audit-controls.json"
FLOOR = 3
RATE = 0.01
#: A re-test clears its control only on a verdict that says the page does not
#: carry the claim; an error or an unreadable page is no evidence either way.
CLEARS = ("UNSUPPORTED", "PARTIAL")


def threshold(n: int) -> int:
    """The number of bad rows, out of `n` run, that makes a round INVALID."""
    return max(FLOOR, math.ceil(n * RATE))


def is_invalid(bad: int, n: int, *, every: bool = True) -> bool:
    """`every`: also INVALID when every row run is bad (controls only; a
    reverse control's disagreement already hands its claim to the repair)."""
    return n > 0 and bad > 0 and (bad >= threshold(n) or (every and bad >= n))


def is_control(r: dict[str, Any]) -> bool:
    return str(r.get("kind", "")).startswith("control")


def originals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every first control row (the re-tests are not counted as controls run)."""
    return [r for r in rows if is_control(r) and not r.get("retest_of")]


def assess(rows: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    """The controls' and reverse controls' standing over every row on file."""
    gone = retired.keys(out)
    first = originals(rows)
    retests: dict[str, dict[str, Any]] = {str(r["retest_of"]): r for r in rows if is_control(r) and r.get("retest_of")}
    bad = []
    for r in first:
        if r.get("verdict") != "SUPPORTED" or r.get("key") in gone:
            continue
        t = retests.get(str(r.get("key"))) or {}
        bad.append(
            {
                "key": r.get("key"),
                "exhibit": r.get("exhibit"),
                "claim": str(r.get("claim") or "")[:200],
                "control": r.get("kind"),
                "retest": t.get("kind"),
                "retest_verdict": t.get("verdict"),
            }
        )
    rev = [r for r in rows if r.get("kind") == "reverse-control"]
    rev_bad = [
        {
            "key": r.get("key"),
            "exhibit": r.get("exhibit"),
            "claim": str(r.get("claim") or "")[:200],
            "image_verdict": r.get("verdict"),
        }
        for r in rev
        if not r.get("agree")
    ]
    n, nr = len(first), len(rev)
    ctl_invalid, rev_invalid = is_invalid(len(bad), n), is_invalid(len(rev_bad), nr, every=False)
    return {
        "controls": n,
        "controls_retired": sum(1 for r in first if r.get("key") in gone),
        "retests": len(retests),
        "bad": bad,
        "threshold": threshold(n),
        "controls_invalid": ctl_invalid,
        "reverse": nr,
        "reverse_bad": rev_bad,
        "reverse_threshold": threshold(nr),
        "reverse_invalid": rev_invalid,
        "invalid": ctl_invalid or rev_invalid,
    }


def describe(a: dict[str, Any], log: Callable[[str], None]) -> None:
    """The arithmetic and every bad row, in the round's log."""
    nb, nrb = len(a["bad"]), len(a["reverse_bad"])
    log(
        f"CONTROLS: {a['controls']} run, {a['controls_retired']} retired as badly chosen, {a['retests']} re-tested; "
        f"{nb} still SUPPORTED after the re-test; INVALID at >= max({FLOOR}, ceil({RATE:.0%} x {a['controls']})) = "
        f"{a['threshold']} or all {a['controls']} -> {'!! INVALID' if a['controls_invalid'] else 'DISCRIMINATES'}"
    )
    for b in a["bad"]:
        log(
            f"  control SUPPORTED twice: {b['key']} Ex{b['exhibit']} {b['control']} / "
            f"{b['retest'] or 'no other institution to re-test against'} {b['retest_verdict'] or ''}: "
            f"{b['claim'][:90]}"
        )
    if a["reverse"]:
        log(
            f"REVERSE CONTROLS: {a['reverse']} re-audited in image mode, {nrb} disagree (each claim takes the image "
            f"verdict); INVALID at >= max({FLOOR}, ceil({RATE:.0%} x {a['reverse']})) = {a['reverse_threshold']} "
            f"-> {'!! INVALID' if a['reverse_invalid'] else 'agrees'}"
        )
        for b in a["reverse_bad"]:
            log(f"  reverse disagrees: {b['key']} Ex{b['exhibit']} image {b['image_verdict']}: {b['claim'][:90]}")


def write(a: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / FILE).write_text(json.dumps({**a, "floor": FLOOR, "rate": RATE}, indent=1), encoding="utf-8")
