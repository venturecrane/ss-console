"""The proofs an update must pass before anything is filed. $0.

 1. every Medical Summary line of the delivered document is in the updated
    one at least as often as it was delivered (the rewritten Prior Medical
    History block is the one delivered text not required: it is new text,
    audited under proof 4), and no delivered sentence is carried MORE often
    (a visit already itemized, added again as new);
 2. the pre-incident omission note is absent from the updated document;
 3. ZERO delivered claims were audited: no audit row is on a delivered
    claim the audit document does not itself carry (a rewritten Prior
    Medical History sentence may repeat a delivered one word for word, and
    then the audit read it in the audit document, as new text);
 4. every claim the update adds is finally audited and SUPPORTED;
 5. no planted control came back SUPPORTED, and a control was planted when
    the audit had enough claims to plant one;
 6. no ADDED line starts with a bare '.' (what a dropped sentence leaves);
 7. no cited sentence appears more often than in the delivery (or more than
    once, when the delivery never had it);
 8. no ADDED entry cites only delivered exhibits (an update reads new
    documents; a re-cut of a delivered visit is not new);
 9. no ADDED line carries pipeline language (chunk / excerpt / provided text);
10. NO VALUE WAS DROPPED: every value an entry composed from a new exhibit
    states is in the updated document on that entry's date, or was named by
    the audit as unsupported. A dedupe, a merge or a repair that loses one
    fails the update; the audit verifies only what remains, so a value that
    silently disappears is never seen again.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import body as B
from .values import numbers

NOTE_LINE = re.compile(r"pre-incident encounters?.*not itemized")
WINDOW = re.compile(r"\b(this|the|a) chunk\b|\bin this excerpt\b|\bnot provided in this\b|\bprovided text\b", re.I)
CITED = re.compile(r"\(Exhibit \d+(?: - p\. [0-9,\s\-]+)?\)")
CONTROL_EVERY = 8


@dataclass
class Inputs:
    """Everything the proofs read, gathered by the stage."""

    delivered_lines: list[str]  # Medical Summary lines of the delivered docx
    updated_lines: list[str]  # Medical Summary lines of the updated docx
    base_md: str
    updated_md: str
    base_claims: list[dict[str, Any]]
    updated_claims: list[dict[str, Any]]
    audit_rows: list[dict[str, Any]]
    composed: list[str]  # every entry compose produced, in the final exhibit numbers
    audit_claims: list[dict[str, Any]]  # the claims of the AUDIT document (the new text)
    new_exhibits: set[int]
    pmh_replaced: bool = False
    gate_passed: bool = True


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)

    def check(self, n: str, ok: bool, line: str) -> None:
        self.lines.append(f"{n}. {line}")
        if not ok:
            self.fails.append(n)


def added_lines(before: list[str], after: list[str]) -> list[str]:
    """Lines of `after` beyond those `before` already had, counted."""
    left = Counter(before)
    out = []
    for ln in after:
        if left[ln]:
            left[ln] -= 1
        else:
            out.append(ln)
    return out


def cited_sentences(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        prev = 0
        for m in CITED.finditer(ln):
            seg = ln[prev : m.end()].strip()
            if len(seg) > len(m.group(0)):
                out.append(seg)
            prev = m.end()
    return out


def pmh_lines(base_md: str) -> set[str]:
    """The delivered Prior Medical History block's lines, as the document reads them."""
    pre, _ = B.entries_of(base_md)
    m = B.PMH_HEAD.search(pre)
    if not m:
        return set()
    return {re.sub(r"\s+", " ", ln.replace("**", "")).strip() for ln in pre[m.start() :].splitlines() if ln.strip()}


def dropped_values(composed: list[str], updated_entries: list[str], new_ex: set[int], named: set[str]) -> list[tuple]:
    """[(entry head, [values])] for entries composed from a NEW exhibit whose
    values the updated document does not carry on that date and the audit did
    not name."""
    by_date: dict[str, set[str]] = {}
    for e in updated_entries:
        by_date.setdefault(B.iso(e), set()).update(numbers(e))
    out = []
    for e in composed:
        if not (B.cited(e) & new_ex):
            continue
        gone = sorted(numbers(e) - by_date.get(B.iso(e), set()) - named)
        if gone:
            out.append((e.splitlines()[0], gone))
    return out


def _delivered_text(x: Inputs, r: Result) -> None:
    a = x.delivered_lines
    if x.pmh_replaced:
        old = pmh_lines(x.base_md)
        a = [ln for ln in a if ln not in old]
    want = Counter(ln for ln in a if not NOTE_LINE.search(ln))
    have = Counter(x.updated_lines)
    missing = [ln for ln, n in want.items() if have[ln] < n]
    dup = [ln for ln, n in Counter(ln for ln in a if len(ln) > 80).items() if have[ln] > n]
    r.check("1", not missing and not dup, f"delivered lines {len(a)}; missing {len(missing)}; duplicated {len(dup)}")
    note = [ln for ln in x.updated_lines if NOTE_LINE.search(ln)]
    r.check("2", not note, f"omission note in the update: {'PRESENT' if note else 'absent'}")


def _audit(x: Inputs, r: Result) -> None:
    base_keys = {c["key"] for c in x.base_claims}
    in_audit_doc = {c["key"] for c in x.audit_claims}
    aud = [row for row in x.audit_rows if row.get("key") in base_keys - in_audit_doc]
    r.check("3", not aud, f"delivered claims {len(base_keys)}; audited {len(aud)}")
    latest: dict[str, Any] = {}
    for row in x.audit_rows:
        if row.get("kind") == "real" and row.get("key"):
            latest[row["key"]] = row.get("verdict")
    new = [c for c in x.updated_claims if c["key"] not in base_keys]
    unsup = [c for c in new if latest.get(c["key"]) != "SUPPORTED"]
    r.check("4", not unsup and x.gate_passed, f"new claims {len(new)}; not finally SUPPORTED {len(unsup)}")
    ctl = [row for row in x.audit_rows if str(row.get("kind", "")).startswith("control")]
    bad = [row for row in ctl if row.get("verdict") == "SUPPORTED"]
    need = len(new) >= CONTROL_EVERY
    r.check("5", not bad and (bool(ctl) or not need), f"controls planted {len(ctl)}; wrongly SUPPORTED {len(bad)}")


def _added(x: Inputs, r: Result) -> None:
    md_lines, base_lines = B.chronology_lines(x.updated_md), B.chronology_lines(x.base_md)
    bare = [ln for ln in added_lines(x.delivered_lines, x.updated_lines) if ln.lstrip().startswith(".")]
    bare += [ln for ln in added_lines(base_lines, md_lines) if ln.lstrip().startswith(".")]
    r.check("6", not bare, f"added lines starting with a bare '.': {len(bare)}")
    cd, cu = Counter(cited_sentences(x.delivered_lines)), Counter(cited_sentences(x.updated_lines))
    rep = [s for s, n in cu.items() if n > max(cd[s], 1)]
    r.check("7", not rep, f"cited sentences carried more often than delivered: {len(rep)}")
    _, be = B.entries_of(x.base_md)
    _, ue = B.entries_of(x.updated_md)
    stale = [e.splitlines()[0] for e in added_lines(be, ue) if not (B.cited(e) & x.new_exhibits)]
    r.check("8", not stale, f"added entries citing no new exhibit: {len(stale)} {stale[:3]}")
    win = [ln for ln in added_lines(base_lines, md_lines) if WINDOW.search(ln)]
    r.check("9", not win, f"added lines carrying pipeline language: {len(win)}")
    named: set[str] = set()
    for row in x.audit_rows:
        named |= numbers(" ".join((row.get("unsupported_assertions") or []) + (row.get("contradictions") or [])))
    lost = dropped_values(x.composed, ue, x.new_exhibits, named)
    from_new = sum(1 for e in x.composed if B.cited(e) & x.new_exhibits)
    head = f"entries composed from new exhibits {from_new}; with a value the update does not carry: {len(lost)}"
    r.check("10", not lost, head + "".join(f" | {d}: {v[:12]}" for d, v in lost[:10]))


def run(x: Inputs) -> Result:
    """Every proof, in order; `Result.fails` empty only when all hold."""
    r = Result()
    _delivered_text(x, r)
    _audit(x, r)
    _added(x, r)
    return r
