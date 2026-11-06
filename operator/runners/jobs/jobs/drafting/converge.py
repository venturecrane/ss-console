"""Which findings a served set's full read reports AGAIN.

The audit's full read is a model reading, and two readings of unchanged text
do not return the same findings: on 2026-11-05 a later full read found as many
findings as the one before it, every one of them new and on requests no repair
had touched, and the count rule ("no fewer than the last full read") failed a
set whose documents were sound. Counts measure the reader as much as the text.

A finding is known here by what it is about, never by its wording: the section
it sits in, the request it names (``compose.row_item``; None when it names
none, the set as a whole), and its verdict. A quotation the mechanical check
could not find is known by the quotation. ``served`` fails a set when a full
read reports again a finding the last full read reported: the repair between
them did not clear it, which is the defect a served set must not carry. A
finding that is new is repaired like any other, and ``finish.REPAIR_CEILING``
bounds a reader that keeps finding new ones.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NoReturn

from ..demand import draft as d
from . import compose
from .outcome import DraftingFailed

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


def served(dr: Drafter, cls: str, label: str, digest: str, corpus: str, ceiling: int) -> int:
    """A served set's audit and repairs, to clean or to ``audit_unrepaired``;
    the version of the clean draft.

    Repair rounds re-audit only the requests a repair changed (the delta
    audit, ``compose.delta``), so a round's findings are the repair's. A set
    clean on a delta read then gets a FULL read before it passes: a defect in
    a request no repair touched (2026-10-14: an interrogatory stating the other
    side's information-and-belief allegation as fact, first seen on the fourth
    read) is found there, repaired, and checked again. The budget stops on
    non-convergence, not on a round count: a repair that changes nothing, two
    delta rounds without fewer findings, or a full read that reports again a
    finding the last full read reported (by identity, never by count: two
    reads of unchanged text differ, 2026-11-05). ``ceiling`` is the runaway
    guard behind those."""
    version, full, stalled = 1, True, 0
    result = dr.audit(version, digest, corpus)
    last_full = finding_ids(dr._read(f"audit-v{version}.md"))

    def left() -> int:
        return compose.blocking_findings(result, cls, dr._read(f"audit-v{version}.md"))

    def fail(why: str) -> NoReturn:
        # Counts only, never the claims or a heading (a title names the
        # parties): a reason reaches SMD's alerts. audit-v<n>.md has the rows.
        raise DraftingFailed(
            f"audit_unrepaired: {left()} audit finding(s) in the {label} {why}; see audit-v{version}.md"
        )

    n = left()
    while True:
        if n == 0 and full:
            return version
        if n == 0:
            result, full = dr.audit(version, digest, corpus, full=True), True
            n = left()
            now = finding_ids(dr._read(f"audit-v{version}.md"))
            again = persisting(last_full, now)
            if n and again:
                fail(f"on a full read after {version - 1} repair(s), {again} of them the last full read also found")
            last_full = now
            continue
        if version > ceiling:
            fail(f"after {ceiling} repairs")
        before = dr._read(f"draft-v{version}.md")
        if dr.repair(digest, version) == before:
            fail("that a repair could not place")
        version += 1
        result, full = dr.audit(version, digest, corpus), False
        m = left()
        stalled = stalled + 1 if m >= n else 0
        n = m
        if stalled >= 2:
            fail("after two repairs that did not reduce them")
