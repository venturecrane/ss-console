"""Which findings a served set's full read reports AGAIN.

The audit's full read is a model reading, and two readings of unchanged text
do not return the same findings: on 2026-11-05 a later full read found as many
findings as the one before it, every one of them new and on requests no repair
had touched, and the count rule ("no fewer than the last full read") failed a
set whose documents were sound. Counts measure the reader as much as the text.

A finding is known here by what it is about, never by its wording: the section
it sits in, the request it names (``compose.row_item``; None when it names
none, the set as a whole), and its verdict. A quotation the mechanical check
could not find is known by the quotation. ``served`` ends a set's repairs when
a full read reports again a finding the last full read reported: the repair
between them did not clear it. A finding that is new is repaired like any
other, and the firm's repair budget (``repair_budget``: rounds and dollars)
bounds a reader that keeps finding new ones. Where repairs end with findings
left, ``repair_budget`` decides by kind whether the set holds or files.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..demand import draft as d
from . import compose, repair_budget, repair_guard

if TYPE_CHECKING:
    from .compose import Drafter

FindingId = tuple[str, int | None, str]

_MECH = re.compile(r"## MECHANICAL:.*?(?=\n## |\Z)", re.S)


def finding_ids(audit_md: str) -> set[FindingId]:
    """Every blocking finding of an ``audit-v<n>.md``, by identity."""
    out: set[FindingId] = set()
    for head, body in d.audit_sections(audit_md).items():
        for ln in body.splitlines():
            m = compose.FINDING.search(ln)
            if m:
                out.add((head, compose.row_item(ln), m.group(1).upper()))
    mech = _MECH.search(audit_md)
    for ln in (mech.group(0) if mech else "").splitlines():
        if ln.startswith("- "):
            out.add(("MECHANICAL", None, " ".join(ln[2:].split())))
    return out


def persisting(last: set[FindingId], now: set[FindingId]) -> int:
    """How many of this read's findings the last full read also reported."""
    return len(last & now)


def served(dr: Drafter, cls: str, label: str, digest: str, corpus: str, plan: repair_budget.Plan) -> int:
    """A served set's audit and repairs, to clean, to filed with open items,
    or to held (``repair_budget``); the version of the draft that goes on.

    Repair rounds re-audit only the requests a repair changed (the delta
    audit, ``compose.delta``), so a round's findings are the repair's. A set
    clean on a delta read then gets a FULL read before it passes: a defect in
    a request no repair touched (2026-10-14: an interrogatory stating the other
    side's information-and-belief allegation as fact, first seen on the fourth
    read) is found there, repaired, and checked again. Repairs stop on
    non-convergence, not on a round count: a repair that changes nothing, two
    delta rounds without fewer findings, or a full read that reports again a
    finding the last full read reported (by identity, never by count: two
    reads of unchanged text differ, 2026-11-05). The firm's repair budget
    (rounds and dollars, ``plan``) is the guard behind those.

    Every repair is kept only where it adds no fact (``repair_guard``,
    2026-11-05): a refused one is put back, never retried, and its findings
    are parked, unresolved. Where the repairs end with anything unresolved,
    ``plan.close`` decides by kind: hold the job for SMD, or file what passed
    with the items in the attorney notes and one alert to SMD."""
    version, full, stalled = 1, True, 0
    dr.audit(version, digest, corpus)
    guard = repair_guard.Guard(dr.data, repair_guard.sources_text(digest, corpus))
    plan.begin()

    def audit_md() -> str:
        return dr._read(f"audit-v{version}.md")

    def left() -> int:
        # The strict count (``compose.blocking_findings``), less what is parked.
        return len(guard.open_rows(audit_md()))

    def end(why: str) -> int:
        return plan.close(guard, audit_md(), dr._read(f"draft-v{version}.md"), version, why)

    last_full = guard.open_ids(audit_md())
    n = left()
    while True:
        if n == 0 and full:
            return end("with a repair refused for adding a fact the file does not hold")
        if n == 0:
            dr.audit(version, digest, corpus, full=True)
            full = True
            n = left()
            now = guard.open_ids(audit_md())
            again = persisting(last_full, now)
            if n and again:
                return end(
                    f"on a full read after {version - 1} repair(s), {again} of them the last full read also found"
                )
            last_full = now
            continue
        why = plan.over(version)
        if why:
            return end(why)
        before = dr._read(f"draft-v{version}.md")
        after, refused = repair_guard.repaired(dr, digest, version, guard)
        parked = {repair_guard.kind_of(f) for f, _ in guard.parked_rows(audit_md())}
        if refused and repair_budget.decide(parked) == "hold":
            return end("after a repair was refused for adding a fact the file does not hold")
        if after == before:
            m = left()
            if m and m >= n:
                return end("that a repair could not place")
            n = m
            continue
        version += 1
        dr.audit(version, digest, corpus)
        full = False
        m = left()
        stalled = stalled + 1 if m >= n else 0
        n = m
        if stalled >= 2:
            return end("after two repairs that did not reduce them")
