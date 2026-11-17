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

* every date and every clinical value P states (``values.clinical_values``)
  is in the window;
* EVERY token of P, each word and each number (``values.norm_number``
  spelling) counted with its repeats, is in the window's tokens: the page is
  a multiset of normalised tokens, so a line wrap or a column order is no
  difference, and nothing else is allowed to be one. No word may be missing
  (``absent`` is 0): 2026-11-17, an allowance of a few missing words let a
  page through whose window lacked a whole negative finding ("No gross ...
  tear is seen").

A page that states no date is held only beside a held neighbour: its page
before or after was held by a window of that same document within WINDOW
pages of where this one is looked for (a run of one record, re-paginated).
When in doubt a page is read: a page judged held is never composed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .values import CITE, NUM, clinical_values, norm_number

WINDOW = 3
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")


def dates_of(text: str) -> frozenset[str]:
    out = set()
    for m, d, y in _DATE.findall(text):
        out.add(f"{int(m)}/{int(d)}/{(2000 + int(y)) if len(y) == 2 else int(y)}")
    return frozenset(out)


def bag_of(text: str) -> Counter[str]:
    """The text's tokens as a multiset: every word (lowercase letters) and
    every number in one spelling (``values.norm_number``)."""
    t = CITE.sub(" ", text).lower()
    out = Counter(re.findall(r"[a-z]+", re.sub(r"\d", " ", t)))
    out.update("#" + norm_number(n) for n in NUM.findall(t))
    return out


@dataclass(frozen=True)
class Facts:
    doc: int
    dates: frozenset[str]
    values: frozenset[str]
    bag: tuple[tuple[str, int], ...]


def facts(doc: int, text: str) -> Facts:
    """What a page says, its stamps aside (``select.strip_stamps``)."""
    from .select import strip_stamps

    body = strip_stamps(text)
    return Facts(
        doc,
        dates_of(body),
        frozenset(clinical_values(body)),
        tuple(sorted(bag_of(body).items())),
    )


def absent(me: Facts, window: list[Facts]) -> int:
    """How many of `me`'s dates, clinical values and tokens (with repeats)
    the window lacks."""
    dates = frozenset().union(*(w.dates for w in window)) if window else frozenset()
    values = frozenset().union(*(w.values for w in window)) if window else frozenset()
    have: Counter[str] = Counter()
    for w in window:
        have.update(dict(w.bag))
    return len(me.dates - dates) + len(me.values - values) + sum((Counter(dict(me.bag)) - have).values())


def holds(me: Facts, window: list[Facts]) -> bool:
    """Whether the window of pages holds everything `me` says: nothing absent."""
    return bool(window and me.bag) and absent(me, window) == 0


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

    def find(self, me: Facts, skip: int | None = None, near: int | None = None) -> tuple[int, int] | None:
        """(start, end) of the first window holding `me`, or None.
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
                    return s, e
        return None
