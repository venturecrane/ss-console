"""What the UPDATE lane does with the entry read's verdict (``stages/entry_audit.py``
is the stage; the full chronology never calls this). $0, no model.

WHY (2026-11-20). In an update the entry read replaced two checking stages
(the per-claim audit and the update review). Its gate passed when every unit
was READ, not when it was right, so a line the read marked wrong, with no
verified correction applied, would have been filed as written. The firm was
told every statement is checked against its page.

THE RULE (version 2, after a bug review of version 1 ran the stage): a line
holds only when a SECOND READER confirms it is wrong and no verified
correction was applied to it. One rule for every class, keyed by FINDING:

* Every finding the read keeps, and every line it marked a problem whose
  finding was dropped (bad quote, a page not shown, an unreadable page, over
  the cap: ``entry_read.Parsed.open_lines``), goes to a second reader. A
  finding with a proposed edit has the verify (``entry_confirm.decide``),
  whose ``original_wrong`` is its confirm; any other finding (a class never
  edited, a header line, a second finding on a line, a quote not found once,
  a partial view, prior history) has a confirm-only call
  (``entry_confirm.confirm_request``).
* not wrong: settled and recorded (``confirmed_not_wrong_by_class``).
  Wrong with ITS verified correction applied: settled. Wrong with no
  correction (a refused edit included), or a second reader that errored
  twice or did not parse: open (``unresolved_by_class``, counted on every
  run). An open finding of a class in HOLD_CLASSES HOLDS the job with the
  document untouched; the reason names the count by class, never the text.
  An edit applied to a line settles only the finding it corrects.
* A DIAGNOSIS CLAIM line with an open finding does not hold: the claim gets
  no table row (``strip_withheld``, read at assemble).
* A provider-name claim's unit (``entry_read.NAME``) is record-only and never
  holds. A finding whose replacement spells the name as the delivered
  chronology does (the spelling the claim was made against) is applied at
  assemble through ``providers.respell`` (``spellings``, ``respell_claims``).
* A sentence SMD corrected by hand (``smd_authored``), or a line SMD kept as
  written on a held job (``medchron/update_settle``, ``smd_settled``), is
  SMD's word: settled, never edited by the reader, still recorded.
* Every applied swap's removed numbers, and what SMD's finish of a hold took
  out (``entry_audit_held``), are written for proof 10 (``removed``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..medchron.update import body as B, entry_confirm as EC, entry_lines as EL, entry_read as ER, icd
from ..medchron.update import providers as P
from ..medchron.update.values import numbers
from . import entry_audit_apply as AP, entry_audit_held as HELD, entry_audit_io as IO

#: THE ONE PLACE the update's hold rule names its classes: a finding a second
#: reader confirmed wrong, in one of these, with no correction applied, holds
#: the job. Every class by default; narrow it here (e.g. to ("unsupported",
#: "another_person")) if a replay shows it holds too often. The record counts
#: ``unresolved_by_class`` over every class on every run either way.
HOLD_CLASSES = (*ER.CLASSES, ER.UNCLASSIFIED)
#: A line SMD kept as written on a held job (``medchron/update_settle``): never
#: held again, never edited by the reader; still read, its finding recorded.
SMD_SETTLED = "smd_settled"
#: A finding settled without a second reader: SMD wrote the sentence by hand,
#: or SMD kept it on a held job.
SETTLED = (AP.SMD_AUTHORED, SMD_SETTLED)
#: The second reader's answer for a finding no second reader is asked about
#: (a provider-name unit, SMD's own sentence).
SKIP = "skip"
#: The update lane's verdict beside the entry read's answers; counts, line
#: hashes and numbers, and the provider spellings to apply.
VERDICT = "update-verdict.json"


@dataclass
class Verdict:
    unresolved: Counter = field(
        default_factory=Counter
    )  # class -> findings confirmed wrong (or unconfirmed), uncorrected
    held: Counter = field(default_factory=Counter)  # the part of `unresolved` in HOLD_CLASSES
    not_wrong: Counter = field(default_factory=Counter)  # class -> findings the second reader found not wrong
    unconfirmed: int = 0  # findings whose second reader errored twice or did not parse
    withheld: set[str] = field(default_factory=set)  # sha256 of each diagnosis claim line given no row
    held_lines: list[tuple[str, str]] = field(default_factory=list)  # (line, class) held (SMD's finish; never recorded)
    spellings: dict[str, str] = field(default_factory=dict)  # name claim sha -> the delivered spelling

    def hold_line(self) -> str:
        n = sum(self.held.values())
        if not n:
            return ""
        by = ", ".join(f"{k} {v}" for k, v in sorted(self.held.items()))
        return f"{n} line(s) found wrong by two readers with no correction applied ({by})"


def _sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def verdict(items: list[AP.Item], decisions: list[EC.Decision], wrong: list[Any]) -> Verdict:
    """Each finding's standing once every edit's outcome is known (after
    ``entry_audit_apply.edit_document``, before anything is written).
    `wrong[i]`: the second reader's answer for item i (True, False, None when
    it errored or did not parse, or SKIP)."""
    v = Verdict()
    for (_name, unit, f), d, w in zip(items, decisions, wrong):
        if isinstance(w, str):  # SKIP: no second reader is asked
            continue
        if w is False:
            v.not_wrong[f.klass] += 1
            continue
        if d[0] == "apply" and d[1].outcome == "applied":
            continue  # its own verified correction applied
        line = unit.lines.get(f.line, "")
        if unit.anchor == ER.PRE and icd.CLAIM.match(line.strip()):
            v.withheld.add(_sha(line))
            continue
        v.unconfirmed += w is None
        v.unresolved[f.klass] += 1
        if f.klass in HOLD_CLASSES:
            v.held[f.klass] += 1
            v.held_lines.append((line, f.klass))
    return v


def spelling(unit: ER.Unit, f: ER.ReadFinding, delivered: dict[str, list[str]]) -> tuple[str, str] | None:
    """(the name claim's sha, the delivered spelling) when the finding's
    replacement spells the claimed name as the delivered chronology does."""
    line = unit.lines.get(f.line, "")
    if not f.replacement.strip() or line.count(f.quote) != 1:
        return None
    m, now = P.CLAIM.match(unit.text.strip()), P.CLAIM.match(line.replace(f.quote, f.replacement, 1).strip())
    if not m or not now:
        return None
    new = now.group(1).strip()
    return (_sha(unit.text), new) if new in delivered.get(m.group(1).strip(), []) else None


def delivered_spellings(compose_report: dict[str, Any]) -> dict[str, list[str]]:
    """{composed name: the delivered names collect judged likely the same person}."""
    out: dict[str, list[str]] = {}
    for r in compose_report.get("provider_spelling") or []:
        if isinstance(r, dict) and r.get("composed"):
            out[str(r["composed"])] = [str(x) for x in r.get("likely_same_person") or []]
    return out


def respell_claims(md: str, spellings: dict[str, str]) -> tuple[str, int]:
    """(`md` with each name claim the read settled spelled as delivered, how
    many). Assemble then applies the spelling through ``providers.respell``."""
    if not spellings:
        return md, 0
    n = 0

    def fix(m: re.Match[str]) -> str:
        nonlocal n
        para = m.group(0)
        c = P.CLAIM.match(para.strip())
        if c is None or _sha(para) not in spellings:
            return para
        n += 1
        return f"Provider named on the record: {spellings[_sha(para)]}. ({c.group(2)})"

    head, sep, body = md.partition("\n## Medical Chronology\n")
    first = B.ENTRY.search(body)
    pre, rest = (body[: first.start()], body[first.start() :]) if first else (body, "")
    pre = re.sub(r"(?m)^Provider named on the record: [^\n]+$", fix, pre)
    return head + sep + pre + rest, n


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
        "held_by_class": dict(v.held),
        "confirmed_not_wrong_by_class": dict(v.not_wrong),
        "unconfirmed": v.unconfirmed,
        "diagnosis_withheld": sorted(v.withheld),
        "removed_values": removed,
        "provider_spellings": v.spellings,
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
    """Proof 10's excused values: the numbers the read's applied corrections
    removed, and those SMD's finish of a hold took out (``entry_audit_held``)."""
    return {str(x) for x in _read(slug_dir, unit).get("removed_values") or []} | HELD.excused(slug_dir, unit)


def withheld(slug_dir: Path, unit: str) -> set[str]:
    return {str(x) for x in _read(slug_dir, unit).get("diagnosis_withheld") or []}


def spellings(slug_dir: Path, unit: str) -> dict[str, str]:
    got = _read(slug_dir, unit).get("provider_spellings") or {}
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


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
