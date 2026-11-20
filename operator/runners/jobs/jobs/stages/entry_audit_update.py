"""What the UPDATE lane does with the entry read's verdict (``stages/entry_audit.py``
is the stage; the full chronology never calls this). $0, no model.

WHY (2026-11-20). In an update the entry read replaced two checking stages
(the per-claim audit and the update review). Its gate passed when every unit
was READ, not when it was right, and several of its finding classes are
recorded only, so a line the read marked wrong, with no verified correction
applied, would have been filed as written. The firm was told every statement
is checked against its page. So, after the read and its verifies:

* A line still marked a problem of class value, wrong field, unsupported or
  another person (HOLD_CLASSES), with no correction applied to it, HOLDS the
  job with the document untouched, the reason naming the count by class and
  never the text. A line is settled when an edit to it applied, or when
  every such finding on it was found not wrong by the second reader or is a
  sentence SMD corrected by hand (``smd_authored``).
* A provider-name claim's unit (``entry_read.NAME``) exists only to settle a
  spelling, so a ``provider`` finding on it holds too.
* A DIAGNOSIS CLAIM line ("Diagnosis code ... recorded on ...") with such a
  problem does not hold: the claim gets no table row (``strip_withheld``,
  read at assemble). A wrong code must not reach a table the firm reads.
* Every applied swap's removed numbers are written for proof 10
  (``removed``), numbers only, never text.

SMD finishes a held update by correcting the held line in
``runs/<unit>/final-chronology.md``; the resumed stage reads that entry again
(its request changed) and every other answer comes from file at $0.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..medchron.update import body as B, entry_confirm as EC, entry_lines as EL, entry_read as ER, icd
from ..medchron.update.values import numbers
from . import entry_audit_apply as AP, entry_audit_io as IO

#: THE ONE PLACE the update's hold rule names its classes: a line found wrong
#: in one of these, with no correction applied, holds the job. Narrow it here
#: (e.g. to ("unsupported", "another_person")) if a replay shows it holds too
#: often; the record counts ``unresolved_by_class`` on every run either way.
HOLD_CLASSES = ("value", "wrong_field", "unsupported", "another_person")
#: A recorded finding whose line is settled without an edit.
SETTLED = (EC.NOT_WRONG, AP.SMD_AUTHORED)
#: The update lane's verdict beside the entry read's answers:
#: {"unresolved_by_class": {...}, "diagnosis_withheld": [sha256 of each line],
#:  "removed_values": [...]}; counts, line hashes and numbers, never text.
VERDICT = "update-verdict.json"


@dataclass
class Verdict:
    unresolved: Counter = field(default_factory=Counter)  # class -> lines held
    withheld: set[str] = field(default_factory=set)  # sha256 of each diagnosis claim line given no row

    def hold_line(self) -> str:
        n = sum(self.unresolved.values())
        if not n:
            return ""
        by = ", ".join(f"{k} {v}" for k, v in sorted(self.unresolved.items()))
        return f"{n} line(s) found wrong with no correction applied ({by})"


def _sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _holds(unit: ER.Unit, klass: str) -> bool:
    # A name claim exists only to settle a spelling, so a provider finding on it holds too.
    return klass in HOLD_CLASSES or (unit.anchor.startswith(ER.NAME) and klass == "provider")


def verdict(items: list[AP.Item], decisions: list[EC.Decision]) -> Verdict:
    """The lines still wrong once every edit's outcome is known (after
    ``entry_audit_apply.edit_document``, before anything is written)."""
    applied: set[tuple[str, str]] = set()
    open_: dict[tuple[str, str], tuple[ER.Unit, str]] = {}
    for (_name, unit, f), d in zip(items, decisions):
        at = (unit.anchor, f.line)
        if d[0] == "apply" and d[1].outcome == "applied":
            applied.add(at)
        elif _holds(unit, f.klass) and not (d[0] == "record" and d[1] in SETTLED):
            open_.setdefault(at, (unit, f.klass))
    v = Verdict()
    for at, (unit, klass) in open_.items():
        if at in applied:
            continue
        line = unit.lines.get(at[1], "")
        if unit.anchor == ER.PRE and icd.CLAIM.match(line.strip()):
            v.withheld.add(_sha(line))
        else:
            v.unresolved[klass] += 1
    return v


def removed_values(decisions: list[EC.Decision]) -> list[str]:
    """The numbers each applied swap took out of its line."""
    out: set[str] = set()
    for d in decisions:
        if d[0] == "apply" and d[1].outcome == "applied" and d[1].kind == "swap":
            out |= numbers(d[1].line) - numbers(d[1].new)
    return sorted(out)


def path(slug_dir: Path, unit: str) -> Path:
    return slug_dir / "runs" / unit / IO.DIR / VERDICT


def write(slug_dir: Path, unit: str, v: Verdict, removed: list[str]) -> None:
    p = path(slug_dir, unit)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "unresolved_by_class": dict(v.unresolved),
        "diagnosis_withheld": sorted(v.withheld),
        "removed_values": removed,
    }
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _read(slug_dir: Path, unit: str) -> dict[str, Any]:
    p = path(slug_dir, unit)
    try:
        got = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def removed(slug_dir: Path, unit: str) -> set[str]:
    """The numbers the update's applied corrections removed (proof 10's excused values)."""
    return {str(x) for x in _read(slug_dir, unit).get("removed_values") or []}


def withheld(slug_dir: Path, unit: str) -> set[str]:
    return {str(x) for x in _read(slug_dir, unit).get("diagnosis_withheld") or []}


def strip_withheld(md: str, shas: set[str]) -> tuple[str, int]:
    """(`md` without each diagnosis claim paragraph a withheld line belongs
    to, how many were taken out). Only claim paragraphs ahead of the first
    entry are touched; nothing else changes by a byte."""
    if not shas:
        return md, 0
    head, sep, body = md.partition("\n## Medical Chronology\n")
    if not sep:
        return md, 0
    first = B.ENTRY.search(body)
    pre, rest = (body[: first.start()], body[first.start() :]) if first else (body, "")
    paras = pre.split("\n\n")
    keep = [
        q
        for q in paras
        if not (icd.CLAIM.match(q.strip()) and any(_sha(s) in shas for s in EL.line_sentences(q.strip())))
    ]
    return head + sep + "\n\n".join(keep) + rest, len(paras) - len(keep)
