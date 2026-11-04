"""Planted controls for the check, judged by the audit's own rule.

A checker that confirms everything looks, from its grades alone, exactly like
a brief with nothing wrong in it. So the check plants items whose right
answer code knows, mixed into the real batches and labelled like real ones:

* ``control``: a real sentence shown with the evidence of a fact from a
  DIFFERENT document (its id label swapped to that fact's). Confirmed is a
  rubber stamp. A confirmed control is re-tested once against a fact from a
  third document (``control-retest``, ``retest_of``), as the audit does.
* ``control-figure``: a sentence stating a dollar figure that is NOT the
  cited bill's (the parsed value moved by more than a third), shown with the
  bill's own excerpt and page. Confirmed is a rubber stamp.
* ``reverse-control``: a sample of confirmed real items re-read by the
  independent second reader; it must agree they are not wrong.

The rows take ``audit/controls.assess_rows``' shape (``kind``, ``key``,
``exhibit``, ``claim``, ``verdict``, ``retest_of``; ``agree`` for a reverse
control) and the audit's threshold decides: INVALID at
``max(3, ceil(1%))`` bad rows, or every control bad. INVALID fails the job
(``ControlsInvalid``): a check that cannot discriminate measured nothing.
"""

from __future__ import annotations

import json
import math
import random
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

from ...audit import controls as audit_controls
from ..evidence.shapes import Fact, Ledger
from . import view
from .items import Item
from .sources import sha

FILE = "check-controls.json"
#: Controls planted: this share of the characterization items, at least MIN.
SHARE = 0.05
MIN = 3
MAX = 40
REVERSE = 3


class ControlsInvalid(RuntimeError):
    """The check's controls say it did not discriminate: the job fails."""

    def __init__(self, assessment: dict[str, Any]) -> None:
        self.assessment = assessment
        super().__init__(
            f"source check controls INVALID: {len(assessment.get('bad') or [])} of {assessment.get('controls')} "
            f"controls confirmed, {len(assessment.get('reverse_bad') or [])} of {assessment.get('reverse')} "
            "reverse controls disagreed"
        )


def how_many(n_items: int) -> int:
    return min(MAX, max(MIN, math.ceil(n_items * SHARE)))


def _other_file(f: Fact, ledger: Ledger, avoid: set[str], rng: random.Random) -> Fact | None:
    pool = [g for g in ledger.usable() if g.file not in avoid and g.file != f.file and g.kind == f.kind]
    pool = pool or [g for g in ledger.usable() if g.file not in avoid and g.file != f.file]
    return rng.choice(pool) if pool else None


def swapped(src: Item, ledger: Ledger, rng: random.Random, n: int, avoid: Iterable[str] = ()) -> Item | None:
    """A ``control`` from real item ``src``: its first fact's evidence replaced
    by a fact of another document, the id label swapped to match."""
    first = next((ledger.get(i) for i in src.ids if ledger.get(i) is not None), None)
    if first is None:
        return None
    other = _other_file(first, ledger, {first.file, *avoid}, rng)
    if other is None:
        return None
    label = {first.id: other.id}
    ids = tuple(other.id if i == first.id else i for i in src.ids)
    key = "ctl-" + sha(f"{src.key}|{other.id}|{n}")[:16]
    return Item(
        key=key,
        part="characterization",
        section=src.section,
        sentence=src.sentence,
        preview=view.preview(src.sentence, ledger, label=label),
        ids=ids,
        control="control",
        note=other.file,
    )


def planted_figure(f: Fact, rng: random.Random, n: int) -> Item | None:
    """A ``control-figure``: the bill's own evidence under a false amount."""
    try:
        v = Decimal(str(f.value))
    except (InvalidOperation, ValueError):
        return None
    if v <= 0:
        return None
    false = (v * Decimal(rng.choice(("1.37", "1.61", "0.52")))).quantize(Decimal("0.01")) + Decimal("13.17")
    what = {"billed": "charged", "paid": "paid", "adjusted": "adjusted"}.get(str(f.role), "recorded")
    sentence = f"The amount {what} on this record is {view.money(false)} [{f.id}]."
    return Item(
        key="ctl-" + sha(f"fig|{f.id}|{n}")[:16],
        part="characterization",
        section="",
        sentence=sentence,
        preview=sentence,
        ids=(f.id,),
        control="control-figure",
        note=f.file,
    )


def plant(items: list[Item], ledger: Ledger, seed: int = 0) -> list[Item]:
    """The controls for ``items`` (real characterization items): about two
    swapped sentences for every planted figure."""
    rng = random.Random(seed)
    real = [i for i in items if i.part == "characterization" and not i.control and i.ids]
    if not real:
        return []
    want = how_many(len(real))
    figures = [f for f in ledger.usable() if f.kind == "figure" and f.unit == "usd" and f.value]
    out: list[Item] = []
    n_fig = min(len(figures), want // 3) if figures else 0
    for k, f in enumerate(rng.sample(figures, n_fig)):
        c = planted_figure(f, rng, k)
        if c:
            out.append(c)
    picks = [rng.choice(real) for _ in range(want - len(out))]
    for k, src in enumerate(picks):
        c = swapped(src, ledger, rng, k)
        if c:
            out.append(c)
    return out


def retest(control: Item, ledger: Ledger, seed: int = 0) -> Item | None:
    """A confirmed swapped control, re-tested against a third document."""
    rng = random.Random(f"{seed}|{control.key}")
    real = Item(control.key, "characterization", control.section, control.sentence, control.preview, control.ids)
    first = ledger.get(control.ids[0]) if control.ids else None
    t = swapped(real, ledger, rng, 0, avoid=[first.file] if first else [])
    if t is None:
        return None
    t.key = control.key + "-r"
    t.control = "control-retest"
    t.second = {"retest_of": control.key}
    return t


def verdict(i: Item) -> str:
    return "SUPPORTED" if i.grade == "confirmed" else "UNSUPPORTED"


def rows(controls: Iterable[Item]) -> list[dict[str, Any]]:
    """The controls as ``audit/controls.assess_rows`` rows."""
    out = []
    for c in controls:
        r: dict[str, Any] = {
            "kind": c.control,
            "key": c.key,
            "exhibit": c.note,
            "claim": c.preview[:200],
            "verdict": verdict(c),
        }
        if c.control == "control-retest" and c.second:
            r["retest_of"] = c.second.get("retest_of")
        out.append(r)
    return out


def reverse_rows(pairs: Iterable[tuple[Item, dict[str, Any] | None]]) -> list[dict[str, Any]]:
    """(a confirmed item, the second reader's answer): agree when the second
    reader found it not wrong; no answer is a disagreement."""
    return [
        {
            "kind": "reverse-control",
            "key": "rev-" + i.key[:16],
            "exhibit": "",
            "claim": i.preview[:200],
            "verdict": "SUPPORTED" if a is not None and not a.get("wrong") else "UNSUPPORTED",
            "agree": a is not None and not a.get("wrong"),
        }
        for i, a in pairs
    ]


def reverse_sample(items: list[Item], seed: int = 0, n: int = REVERSE) -> list[Item]:
    ok = [i for i in items if i.grade == "confirmed" and not i.control and i.part == "characterization"]
    return random.Random(seed).sample(ok, min(n, len(ok)))


def assess(
    rows_: list[dict[str, Any]],
    out_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
    raise_invalid: bool = True,
) -> dict[str, Any]:
    """The audit's rule over the check's control rows; written to
    ``check-controls.json``; ``ControlsInvalid`` when INVALID."""
    # A confirmed control whose re-test came back unsupported was a badly
    # chosen control page, retired by rule as the audit does (``run.Round``).
    gone = {str(r["retest_of"]) for r in rows_ if r.get("retest_of") and r.get("verdict") in audit_controls.CLEARS}
    a = audit_controls.assess_rows(rows_, gone)
    if log:
        audit_controls.describe(a, log)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / FILE).write_text(json.dumps({"assessment": a, "rows": rows_}, indent=2) + "\n", encoding="utf-8")
    if a["invalid"] and raise_invalid:
        raise ControlsInvalid(a)
    return a
