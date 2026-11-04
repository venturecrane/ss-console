"""Two transcriptions of ONE page, in two new documents. $0.

A file can be filed twice: once whole and once as an extract of it, each a
scan of the same paper (a 124-page file was pages 281 to 404 of a 404-page
file, 2026-10-14). The larger is read first (``pageid.reading_order``), and a
page of the smaller is a repeat when its picture and its words say so
(``pageid_io.confirmed``). Each copy was transcribed separately, though, and
two readings of one picture rarely agree to the letter: one missed a value,
the other read a date the first did not. Those pages failed the strict rules,
were composed from both files, and the same visit was itemized twice.

A page of the smaller document is a TWIN of a page of the larger one when its
picture pairs with it (``update/pageid``, any tier), or, with no picture
match, when it sits at the same offset as its neighbours' twins (a run of
paired pages around an unpaired one), AND the two readings share most of
their words in any order (``pageid_io.WORDS_FRAC``). Of two twins still both
read:

* the smaller document's twin whose dates and clinical values are all in
  the larger's is a repeat of it, and is not read;
* when the LARGER document's reading is the one that adds nothing and its
  twin is a PICTURE pair (the same scan), the page stays in the larger
  document and is read from the smaller one's richer reading; the smaller
  page is a repeat. The record is cited once, in the larger exhibit: a
  2-page exhibit once remained only because a copy's reading caught four
  values the larger file's reading of the same scan missed (2026-10-14).
  ``update_select`` writes that reading into the larger document's
  transcript, so compose, the audit and every later check read the same
  words on the cited page;
* a twin found only by its neighbours' offset (no picture) is never given
  another page's reading: the larger page is the repeat and the smaller is
  read;
* when each states a date or value the other lacks, both are read: what the
  two readings disagree on is never settled by dropping one.
"""

from __future__ import annotations

from collections import Counter

from . import pageid_io as PG
from .select import _FURNITURE, EXCLUDED, MIN_CHARS, OVERLAP, READ, _dates
from .values import clinical_values

Decisions = dict[int, tuple[str, str]]


def _facts(text: str) -> tuple[set[str], set[str], Counter[str]]:
    body = _FURNITURE.sub("", text or "")
    return _dates(body), set(clinical_values(body)), PG._words(body)


def aligned(pairs: dict[int, int], npages: int, theirs: int) -> dict[int, int]:
    """{my page: their page}: the picture pairs, plus each unpaired page whose
    neighbour pairs at an offset that places it on a page of theirs (grown
    outward from the pairs until nothing changes)."""
    out = dict(pairs)
    changed = True
    while changed:
        changed = False
        for q in range(1, npages + 1):
            if q in out:
                continue
            for n in (q - 1, q + 1):
                p = out[n] + (q - n) if n in out else 0
                if 1 <= p <= theirs and p not in out.values():
                    out[q] = p
                    changed = True
                    break
    return out


def _within(a: str, b: str) -> bool:
    """`a` states no date and no clinical value `b` does not, and they share
    most of their words."""
    if len((a or "").strip()) < MIN_CHARS or len((b or "").strip()) < MIN_CHARS:
        return False
    da, va, wa = _facts(a)
    db, vb, wb = _facts(b)
    small = min(sum(wa.values()), sum(wb.values()))
    return da <= db and va <= vb and small > 0 and sum((wa & wb).values()) >= PG.WORDS_FRAC * small


def settle(
    mine: tuple[dict[int, str], Decisions],
    theirs: tuple[dict[int, str], Decisions],
    pairs: dict[int, int],
) -> dict[int, int]:
    """Settles, in place, each pair of twins both still read: the twin that
    adds nothing is excluded, and a larger picture twin that adds nothing is
    read from the smaller one's reading instead (its text in `theirs` is
    replaced). Returns those substitutions, {their page: my page}. `mine` is
    the smaller document (pages, decisions), `theirs` the larger, `pairs` its
    picture pairs {my page: their page}."""
    (mp, md), (tp, td) = mine, theirs
    swapped: dict[int, int] = {}
    for q, p in aligned(pairs, len(md), len(td)).items():
        if md.get(q, ("",))[0] != READ or td.get(p, ("",))[0] != READ:
            continue
        if _within(mp.get(q, ""), tp.get(p, "")):
            md[q] = (EXCLUDED, OVERLAP)
        elif _within(tp.get(p, ""), mp.get(q, "")) and pairs.get(q) == p:
            tp[p] = mp[q]  # the same scan, read from the reading that caught more
            md[q] = (EXCLUDED, OVERLAP)
            swapped[p] = q
        elif _within(tp.get(p, ""), mp.get(q, "")):
            td[p] = (EXCLUDED, OVERLAP)
    return swapped
