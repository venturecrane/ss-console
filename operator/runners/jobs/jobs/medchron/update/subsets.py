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
    #: ((page, the parent's page that covers it), ...): each page of the
    #: extract a page of the whole one carries (``refiled_subsets``), so a
    #: set-aside page names the page that covers it (``update/repeat_check``).
    #: Empty for an extract found by its pictures (every page pixel-equal).
    at: tuple[tuple[int, int], ...] = ()


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


def _page_of(me: _P, theirs: list[tuple[int, _P]]) -> int | None:
    """The page of `theirs` that `me` is a page of, or None."""
    if not me.shingles:
        return None
    return next(
        (
            n
            for n, o in theirs
            if len(me.shingles & o.shingles) >= SUBSET_FRAC * len(me.shingles)
            and me.dates <= o.dates
            and me.words <= o.words
            and me.values <= o.values
        ),
        None,
    )


def _matched(mine: list[tuple[int, _P]], theirs: list[tuple[int, _P]]) -> dict[int, int] | None:
    """{my page: the page of `theirs` it is} when that makes `mine` an
    extract of `theirs`; None when it does not."""
    hits = {n: _page_of(p, theirs) for n, p in mine}
    found = {n: q for n, q in hits.items() if q is not None}
    if not mine or len(found) < SUBSET_FRAC * len(mine):
        return None
    dates = frozenset().union(*(o.dates for _, o in theirs))
    values = frozenset().union(*(o.values for _, o in theirs))
    rest = [p for n, p in mine if n not in found]
    if any(not (p.dates <= dates and p.values <= values) for p in rest):
        return None  # a page outside the whole states a date or value of its own: the document is read
    return found


def refiled_subsets(docs: list[tuple[str, dict[int, str]]], firm: set[str] | None = None) -> dict[str, Subset]:
    """{doc id: Subset} for each new document that is an extract of a larger
    one, given (doc id, {page: text}) in pull order. A firm-numbered file
    (`firm`) is never an extract: the firm holds it under its own number.
    A chain (an extract of an extract) resolves to the outermost document.
    A document with a page the transcription could not read is never an
    extract: what that page says is unknown, so it is read (and holds)."""
    keep_firm = (firm or set()) | {d for d, pg in docs if any((t or "").strip() in FAILED_MARKS for t in pg.values())}
    texts = {d: [(n, _page(t)) for n, t in sorted(pg.items()) if len((t or "").strip()) >= MIN_CHARS] for d, pg in docs}
    rank = {d: len(texts[d]) for d, _ in docs}
    found: dict[str, Subset] = {}
    for d, _ in docs:
        if d in keep_firm or not texts[d]:
            continue
        bigger = sorted((o for o, _ in docs if rank[o] > rank[d]), key=lambda o: -rank[o])  # stable: pull order
        for o in bigger:
            got = _matched(texts[d], texts[o])
            if got is not None:
                found[d] = Subset(d, o, len(texts[d]), len(got), tuple(sorted(got.items())))
                break
    return {d: rooted(found, s) for d, s in found.items()}


def rooted(found: dict[str, Subset], s: Subset) -> Subset:
    """`s` cited to the outermost document of its chain (an extract of an
    extract), each page's cover carried along the chain; a page whose cover
    a link does not carry (a link found by pictures carries none) has none."""
    parent, at, seen = s.parent, dict(s.at), {s.doc}
    while parent in found and parent not in seen:  # rank strictly rises along a chain; `seen` is a backstop
        seen.add(parent)
        link = found[parent]
        nxt = dict(link.at)
        at = {p: nxt[q] for p, q in at.items() if q in nxt}
        parent = link.parent
    return Subset(s.doc, parent, s.pages, s.matched, tuple(sorted(at.items())))
