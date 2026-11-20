"""Paragraph-level repeats in the update's final set. $0, no model.

WHY (2026-11-19). On a real delivered 509-page update 38 of 144 added entries
repeated a visit, and the final set dropped only 7: its repeat rule was all or
nothing, so one value the other entry lacked (a clock time, an order number, a
later stop date) kept the WHOLE repeat. Here an added entry that is one
encounter with another entry, and is not covered whole, loses each CITED
PARAGRAPH (a body line carrying a citation) that ONE of the entries it matches
covers on its own, and keeps every other paragraph verbatim:

* covered: the keeper holds every run of four words of the paragraph's body
  (``encounter.held``, the run test of the whole-entry rule) AND every number,
  finding term, date and date range it writes. Only a TRUE time of day may
  differ (``TIME``: "at 10:32", "10:32 am", "10:32:05"); "1:12" alone is a
  ratio, kept as a value. In doubt the paragraph stays: a kept repeat is a
  nuisance, a dropped fact is a wrong document. (2026-11-19 review: comparing
  values and cue terms only dropped "NO disc herniation" against "disc
  herniation", a right knee against a left, a carb ratio and a year.)
* a drop is made only when ``lost``, computed by its own tokenizer, finds
  every number, word and four-word run of the paragraph in the keeper;
* a drop that creates a claim key is not made (the paragraph directly above
  another under one heading closes that heading's span: removing it changes
  the next claim's text), and is counted;
* a drop that would leave an exhibit cited by no added entry is not made, so
  of an exhibit's paragraphs the last stays;
* a subsection heading left with nothing under it goes with its paragraph.

The keeper is never edited. A delivered entry is never edited (it is only
ever a keeper). Each drop records, for its entry (``encounter.key``), the
times of day its paragraph wrote that the keeper did not, so proof 10 excuses
them on that entry alone, never on another.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import body as B, encounter as E, uncited as U
from .values import numbers, terms, words

#: A TRUE time of day: after "at"/"@", or with am/pm, or with seconds. A bare
#: "1:12" is not one (it can be a ratio or a dose), so it stays a value.
TIME = re.compile(
    r"(?i)(?:\b(?:at|@)\s*(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s*[ap]\.?m\b\.?)?"
    r"|\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\s*[ap]\.?m\b\.?"
    r"|\b(?:[01]?\d|2[0-3]):[0-5]\d:[0-5]\d\b)"
)
_TOK = re.compile(r"[a-z]+|\d+(?:[.:/,-]\d+)*")

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
    refused: int = 0  # covered paragraphs the independent falsifier kept


def excuse_key(e: str) -> str:
    """Which entry an excused value belongs to: its encounter key and the
    exhibits it cites (final numbers), read alike off the final set's entry
    and off compose's (``proofs.dropped_values``)."""
    return f"{E.key(e)}|{','.join(str(n) for n in sorted(B.cited(e)))}"


def cited_paragraphs(e: str) -> list[str]:
    """The entry's body lines that carry a citation and some text."""
    return [ln for ln in e.split("\n")[2:] if B.CITE.search(ln) and not U.cite_only(ln)]


def as_entry(p: str) -> str:
    """A paragraph as an entry body, for the entry-shaped value checks."""
    return "\n\n" + p


def untimed(text: str) -> str:
    """`text` with its true times of day blanked."""
    return TIME.sub(" ", text)


def times(text: str) -> set[str]:
    """The numbers of `text`'s true times of day."""
    return set().union(*(numbers(m.group(0)) for m in TIME.finditer(text))) if TIME.search(text) else set()


def covers(keeper: str, p: str, dated: Dated) -> bool:
    """`keeper` states everything paragraph `p` states, on its own: every
    four-word run, every number but a true time of day, every term and date."""
    q, k = untimed(p), untimed(keeper)
    return E.held(as_entry(q), k) and numbers(q) <= numbers(k) and terms(q) <= words(k) and dated(as_entry(p), keeper)


def _tokens(text: str) -> list[str]:
    return _TOK.findall(B.CITE.sub(" ", untimed(text)).lower())


def lost(p: str, keeper: str) -> bool:
    """The falsifier, by its own tokenizer and apart from ``covers``: a word,
    number or four-word run of `p` (its true times aside) not in the keeper."""
    mine, have = _tokens(p), _tokens("\n".join(keeper.split("\n")[2:]))
    runs = {tuple(have[i : i + 4]) for i in range(len(have) - 3)}
    if len(mine) < 4 or not set(mine) <= set(have):
        return True
    return any(tuple(mine[i : i + 4]) not in runs for i in range(len(mine) - 3))


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
        if lost(p, by):
            s.refused += 1
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
        s.values |= (numbers(p) - numbers(by)) & times(p)
        s.text = cand
    return s
