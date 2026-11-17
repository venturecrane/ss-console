"""A new page whose words a run of pages elsewhere already holds. $0.

WHY (2026-11-17). A firm filed two overlapping exports of one clinic's
records; the second covered a later date range too. A page of one export and
the same page of the other differed only in print stamps and in where the
page broke: the picture hash was far apart (``update/pageid``), the 8-word
shingles of one page were split across two pages of the other
(``select.Delivered``, ``select.Earlier``), so every shared visit was
composed twice and 19 visits were itemized as two new rows each.

CONTAINED: a new page P (at least ``select.MIN_CHARS`` of text) is already
held when a WINDOW of 1 to WINDOW consecutive pages of ONE other document (a
delivered exhibit, or a new document read before it, never its own) holds
what P says, read with print and export stamps aside (``select.strip_stamps``):

* every date P states is in the window;
* every clinical value P states (``values.clinical_values``) and every
  content number (``values.content_numbers``: dates, doses, vitals, labs,
  codes; a time of day and a bare year aside) is in the window;
* every word P uses is in the window but at most MISSING_MAX (and at most
  MISSING_FRAC of its distinct words), and none of those is a negation or
  result word (``GUARD``) or a finding term P states (``values.terms``), so
  "no fracture" is never held by "fracture";
* at least WORDS_FRAC of P's words, counted with repeats, are in the window.

A page that states no date is held only beside a held neighbour: its page
before or after was held by a window of that same document within WINDOW
pages of where this one is looked for (a run of one record, re-paginated).
When in doubt a page is read: a page judged held is never composed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .values import clinical_values, content_numbers, terms

WINDOW = 3
WORDS_FRAC = 0.9
MISSING_MAX = 5
MISSING_FRAC = 0.03
#: Words whose absence from the window changes what a sentence says.
GUARD = frozenset(
    "no not non nor denies denied deny negative positive without absent never none normal abnormal "
    "unremarkable remarkable increased decreased worse worsening improved improving".split()
)
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")


def dates_of(text: str) -> frozenset[str]:
    out = set()
    for m, d, y in _DATE.findall(text):
        out.add(f"{int(m)}/{int(d)}/{(2000 + int(y)) if len(y) == 2 else int(y)}")
    return frozenset(out)


def bag_of(text: str) -> Counter[str]:
    """The text's words as a bag (two letters or more, digits stripped)."""
    return Counter(re.findall(r"[a-z]{2,}", re.sub(r"\d", " ", text.lower())))


@dataclass(frozen=True)
class Facts:
    doc: int
    dates: frozenset[str]
    values: frozenset[str]
    nums: frozenset[str]
    terms: frozenset[str]
    bag: tuple[tuple[str, int], ...]

    @property
    def words(self) -> frozenset[str]:
        return frozenset(w for w, _ in self.bag)


def facts(doc: int, text: str) -> Facts:
    """What a page says, its stamps aside (``select.strip_stamps``)."""
    from .select import strip_stamps

    body = strip_stamps(text)
    return Facts(
        doc,
        dates_of(body),
        frozenset(clinical_values(body)),
        frozenset(content_numbers(body)),
        frozenset(terms(body)),
        tuple(sorted(bag_of(body).items())),
    )


def holds(me: Facts, window: list[Facts]) -> bool:
    """Whether the window of pages holds everything `me` says (the rule above)."""
    if not window or not me.bag:
        return False
    dates = frozenset().union(*(w.dates for w in window))
    values = frozenset().union(*(w.values for w in window))
    nums = frozenset().union(*(w.nums for w in window))
    if not (me.dates <= dates and me.values <= values and me.nums <= nums):
        return False
    have: Counter[str] = Counter()
    for w in window:
        have.update(dict(w.bag))
    mine = Counter(dict(me.bag))
    missing = set(mine) - set(have)
    if len(missing) > min(MISSING_MAX, int(MISSING_FRAC * len(mine))) or missing & (GUARD | me.terms):
        return False
    return sum((mine & have).values()) >= WORDS_FRAC * sum(mine.values())


class Pool:
    """Pages in reading order, each tagged with its document."""

    def __init__(self) -> None:
        self.items: list[Facts] = []
        self.by_date: dict[str, list[int]] = defaultdict(list)

    def add(self, f: Facts) -> None:
        for d in f.dates:
            self.by_date[d].append(len(self.items))
        self.items.append(f)

    def _windows(self, at: int, skip: int | None) -> list[tuple[int, int]]:
        """[(start, end)] of every window of one document holding position `at`."""
        out = []
        doc = self.items[at].doc
        if doc == skip:
            return out
        for n in range(1, WINDOW + 1):
            for s in range(at - n + 1, at + 1):
                e = s + n
                if s >= 0 and e <= len(self.items) and all(self.items[k].doc == doc for k in range(s, e)):
                    out.append((s, e))
        return out

    def find(self, me: Facts, skip: int | None = None, near: int | None = None) -> int | None:
        """The position of the first page of a window holding `me`, or None.
        A dated page is looked for where its rarest date is; an undated one
        only `near` a position (its held neighbour's window), within WINDOW."""
        if me.dates:
            rare = min(me.dates, key=lambda d: len(self.by_date.get(d, [])))
            spots = self.by_date.get(rare, [])
        elif near is not None:
            spots = [k for k in range(near - WINDOW, near + 2 * WINDOW) if 0 <= k < len(self.items)]
            if near < len(self.items):
                spots = [k for k in spots if self.items[k].doc == self.items[near].doc]
        else:
            return None
        for at in spots:
            for s, e in self._windows(at, skip):
                if holds(me, self.items[s:e]):
                    return s
        return None
