"""Paragraph-level repeats in the update's final set. $0, no model.

WHY (2026-11-19). On a real delivered 509-page update 38 of 144 added entries
repeated a visit, and the final set dropped only 7: its repeat rule was all or
nothing, so one value the other entry lacked (a clock time, an order number, a
later stop date) kept the WHOLE repeat. Here an added entry that is one
encounter with another entry, and is not covered whole, loses each CITED
PARAGRAPH (a body line carrying a citation) that ONE of the entries it matches
covers on its own, and keeps every other paragraph verbatim:

* covered: every content value (``values.content_numbers``: a time of day and
  a bare year are not care), every finding term (``values.terms`` against
  ``values.words``) and every date and date range the paragraph writes; a
  paragraph with no value and no term goes only when its words are, in order,
  inside the keeper's (``repeats._restates``'s normal form);
* a drop that creates a claim key is not made (the paragraph directly above
  another under one heading closes that heading's span: removing it changes
  the next claim's text), and is counted;
* a drop that would leave an exhibit cited by no added entry is not made, so
  of an exhibit's paragraphs the last stays;
* a subsection heading left with nothing under it goes with its paragraph.

The keeper is never edited. A delivered entry is never edited (it is only
ever a keeper). Each drop records the values its paragraph states that its
keeper does not (``numbers`` less the keeper's: times and years only, by the
rule), so proof 10 reads them as removed on purpose, never as lost.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import body as B, repeats as R, uncited as U
from .values import content_numbers, numbers, terms, words

Keys = Callable[[str], set[str]]
Dated = Callable[[str, str], bool]


@dataclass
class Shortened:
    text: str
    dropped: int = 0
    left_key: int = 0
    left_uncited: int = 0
    lost_keys: set[str] = field(default_factory=set)
    values: set[str] = field(default_factory=set)
    merged: int = 0  # paragraphs whose drop the independent falsifier says lost something


def cited_paragraphs(e: str) -> list[str]:
    """The entry's body lines that carry a citation and some text."""
    return [ln for ln in e.split("\n")[2:] if B.CITE.search(ln) and not U.cite_only(ln)]


def as_entry(p: str) -> str:
    """A paragraph as an entry body, for the entry-shaped value checks."""
    return "\n\n" + p


def covers(keeper: str, p: str, dated: Dated) -> bool:
    """`keeper` states everything paragraph `p` states, on its own."""
    if not content_numbers(p) and not terms(p):
        body = R._body(as_entry(p))
        return bool(body) and f" {body} " in f" {R._body(keeper)} "  # whole words, in order
    return content_numbers(p) <= content_numbers(keeper) and terms(p) <= words(keeper) and dated(as_entry(p), keeper)


def lost(p: str, keeper: str) -> bool:
    """The falsifier, read apart from ``covers``: a content value or term of `p` the keeper lacks."""
    return bool(content_numbers(p) - content_numbers(keeper)) or bool(terms(p) - words(keeper))


def without(e: str, k: int, headings: set[str]) -> str:
    """`e` without line `k`, and without the heading lines of its block when
    nothing but headings is left there."""
    lines = e.split("\n")
    del lines[k]
    lo = k
    while lo > 2 and lines[lo - 1].strip():
        lo -= 1
    hi = k
    while hi < len(lines) and lines[hi].strip():
        hi += 1
    block = lines[lo:hi]
    if block and all(ln.strip() in headings for ln in block):
        del lines[lo:hi]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip("\n")


def shorten(
    e: str, keepers: list[str], headings: set[str], keys: Keys, dated: Dated, still: Callable[[str], bool]
) -> Shortened:
    """`e` less every cited paragraph one of `keepers` covers. `still(text)`:
    every exhibit the added entries cite stays cited with `text` in place of `e`."""
    s = Shortened(e)
    for p in cited_paragraphs(e):
        by = next((k for k in keepers if covers(k, p, dated)), None)
        lines = s.text.split("\n")
        if by is None or p not in lines:
            continue
        cand = without(s.text, lines.index(p), headings)
        before, after = keys(s.text), keys(cand)
        if after - before:
            s.left_key += 1
            continue
        if not still(cand):
            s.left_uncited += 1
            continue
        s.dropped += 1
        s.lost_keys |= before - after
        s.values |= numbers(p) - numbers(by)
        s.merged += int(lost(p, by))
        s.text = cand
    return s
