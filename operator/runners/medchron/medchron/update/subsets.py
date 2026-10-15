"""A new document that is a re-filed extract of another new document. $0.

The firm can file a record and, beside it, extracts of it: two new documents
were page-for-page copies of a third, their printed footers numbering page N
where the whole one numbered N+280 (2026-10-13 review, fix 17). Read as three
exhibits, every visit was composed two or three times. An extract is not an
exhibit: the whole document is, and every visit the extract holds is cited
there. ``update_select`` records each extract (``selection.json``
"subsets") so Records Reviewed names it as a re-filed extract.

A document is an extract of a LARGER new document (strictly more text pages:
a whole re-filed copy of the same size stays with ``select.Earlier``, which
reads the first copy and excludes the second page by page) when SUBSET_FRAC of
its text pages
are each a page of it, and the rest state no date and no clinical value the
whole one lacks. A page counts only when ONE page of the other document carries
SUBSET_FRAC of its digit-stripped shingles and every date, every word and every
clinical value it states (footer, fax and print lines aside). Two visits on one
template differ in a date or a word and never match, and pages of one document
are never compared with each other, so a run of template visits is never
excluded (review fix 1). A SCANNED extract is found by its pictures before
the vision pass (``update/pageid``); this rule is the words-side signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .select import _FURNITURE, FAILED_MARKS, MIN_CHARS, _dates, shingles
from .values import clinical_values

SUBSET_FRAC = 0.9
#: A page-number footer line ("Page 281", "- 12 -"): an extract renumbers its
#: pages, so its footer differs from the whole record's on every page.
_FOOTER = re.compile(r"(?im)^\W*(?:page|pg\.?|p\.)?\s*\d+\W*$")


@dataclass(frozen=True)
class Subset:
    doc: str
    parent: str
    pages: int
    matched: int


@dataclass(frozen=True)
class _P:
    shingles: frozenset[str]
    dates: frozenset[str]
    words: frozenset[str]
    values: frozenset[str]


def _page(text: str) -> _P:
    body = _FOOTER.sub("", _FURNITURE.sub("", text))
    return _P(
        frozenset(shingles(body)),
        frozenset(_dates(body)),
        frozenset(re.findall(r"[a-z]+", re.sub(r"\d", " ", body.lower()))),
        frozenset(clinical_values(body)),
    )


def _is_page_of(me: _P, theirs: list[_P]) -> bool:
    if not me.shingles:
        return False
    return any(
        len(me.shingles & o.shingles) >= SUBSET_FRAC * len(me.shingles)
        and me.dates <= o.dates
        and me.words <= o.words
        and me.values <= o.values
        for o in theirs
    )


def _matched(mine: list[_P], theirs: list[_P]) -> int | None:
    """How many of `mine` are pages of `theirs` when that makes `mine` an
    extract of `theirs`; None when it does not."""
    hits = [_is_page_of(p, theirs) for p in mine]
    if not mine or sum(hits) < SUBSET_FRAC * len(mine):
        return None
    dates = frozenset().union(*(o.dates for o in theirs))
    values = frozenset().union(*(o.values for o in theirs))
    rest = [p for p, h in zip(mine, hits, strict=True) if not h]
    if any(not (p.dates <= dates and p.values <= values) for p in rest):
        return None  # a page outside the whole states a date or value of its own: the document is read
    return sum(hits)


def refiled_subsets(docs: list[tuple[str, dict[int, str]]], firm: set[str] | None = None) -> dict[str, Subset]:
    """{doc id: Subset} for each new document that is an extract of a larger
    one, given (doc id, {page: text}) in pull order. A firm-numbered file
    (`firm`) is never an extract: the firm holds it under its own number.
    A chain (an extract of an extract) resolves to the outermost document.
    A document with a page the transcription could not read is never an
    extract: what that page says is unknown, so it is read (and holds)."""
    keep_firm = (firm or set()) | {d for d, pg in docs if any((t or "").strip() in FAILED_MARKS for t in pg.values())}
    texts = {d: [_page(t) for _, t in sorted(pg.items()) if len((t or "").strip()) >= MIN_CHARS] for d, pg in docs}
    rank = {d: len(texts[d]) for d, _ in docs}
    found: dict[str, Subset] = {}
    for d, _ in docs:
        if d in keep_firm or not texts[d]:
            continue
        bigger = sorted((o for o, _ in docs if rank[o] > rank[d]), key=lambda o: -rank[o])  # stable: pull order
        for o in bigger:
            n = _matched(texts[d], texts[o])
            if n is not None:
                found[d] = Subset(d, o, len(texts[d]), n)
                break
    return {d: Subset(d, _root(found, s.parent), s.pages, s.matched) for d, s in found.items()}


def _root(found: dict[str, Subset], parent: str) -> str:
    seen: set[str] = set()
    while parent in found and parent not in seen:  # rank strictly rises along a chain; `seen` is a backstop
        seen.add(parent)
        parent = found[parent].parent
    return parent
