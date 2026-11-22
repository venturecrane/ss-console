"""Which added entries restate another visit, known by the pages they cite. $0.

An update reads every page a rule only suspects of copying a page already
covered (``update/select``, ``SUSPECTED``), so a visit already itemized comes
back from a second copy of its record, often under a provider spelled
another way. The page record (``runs/<unit>/pages.jsonl``) says, for each
such page, the page it may copy (``evidence.cover``: a delivered exhibit's
page, or a page of another new document; a run of pages names its first and
last). ``Suspects`` holds that, each cover followed through a cover that is
itself a suspected copy (a chain: B copies A's page, A copies a delivered
page), and the new pages set aside by picture.

``copies(e, other, s)``: added entry `e` is `other`'s visit when every page
it cites is a suspected copy (a page set aside, or a delivered page, cited
beside them changes nothing) of a page in ONE exhibit, inside the span
`other` cites in that exhibit, one page either side (an exhibit's notes run
onto the next page). A page `e` cites that was read on its own account means
it says something of its own: not a copy (bug review 2026-11-22, F3, F5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import pages_record as PR
from .encounter import cited_pages

#: Covers followed before a chain is left where it stands.
MAX_HOPS = 4
#: (exhibit, first page, last page) of the page or run a page may copy.
Span = tuple[int, int, int]


@dataclass
class Suspects:
    covers: dict[tuple[int, int], Span] = field(default_factory=dict)
    aside: set[tuple[int, int]] = field(default_factory=set)
    new: set[int] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.covers)


def _span(cov: dict[str, Any], exhibit_of: dict[str, int]) -> Span | None:
    p, last = cov.get("page"), cov.get("last")
    n = cov.get("exhibit") if isinstance(cov.get("exhibit"), int) else exhibit_of.get(str(cov.get("doc")))
    if not isinstance(n, int) or not isinstance(p, int):
        return None
    return (n, p, last if isinstance(last, int) and last >= p else p)


def from_rows(rows: list[dict[str, Any]], exhibit_of: dict[str, int]) -> Suspects:
    """``Suspects`` from the update's page record. `exhibit_of`: {document id:
    its new exhibit number as composed}."""
    s = Suspects(new=set(exhibit_of.values()))
    for r in rows:
        ex, p, ev = exhibit_of.get(str(r.get("doc"))), r.get("page"), r.get("evidence")
        if ex is None or not isinstance(p, int) or not isinstance(ev, dict):
            continue
        if r.get("status") == PR.EXCLUDED:
            s.aside.add((ex, p))
        elif ev.get(PR.SUSPECTED) and isinstance(ev.get("cover"), dict):
            span = _span(ev["cover"], exhibit_of)
            if span is not None:
                s.covers[(ex, p)] = span
    for k in list(s.covers):
        s.covers[k] = _follow(s.covers[k], s.covers)
    return s


def _follow(span: Span, covers: dict[tuple[int, int], Span]) -> Span:
    for _ in range(MAX_HOPS):
        n, first, last = span
        if first != last or (n, first) not in covers:
            break
        span = covers[(n, first)]
    return span


def copies(e: str, other: str, s: Suspects) -> bool:
    """Every page `e` cites is a suspected copy of a page in one exhibit,
    inside `other`'s cited span there, one page either side."""
    spans: list[Span] = []
    for x in cited_pages(e):
        if x in s.covers:
            spans.append(s.covers[x])
        elif x not in s.aside and x[0] in s.new:
            return False  # a page read on its own account: `e` says something of its own
    if not spans or len({n for n, _, _ in spans}) != 1:
        return False
    theirs = [p for n, p in cited_pages(other) if n == spans[0][0]]
    if not theirs:
        return False
    lo, hi = min(theirs) - 1, max(theirs) + 1
    return all(first <= hi and last >= lo for _, first, last in spans)
