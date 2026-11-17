"""One encounter, however its entry is headed. $0, no model.

WHY (2026-11-17). On a real 799-page update about 34 repeated visit rows
reached the filed document. The same-visit rule ran once, at collect, over
the composed entries, and matched two entries only when their providers
canonicalised alike; the update review and the finisher add entries later
that it never saw, and two exports of one clinic head one visit
"Surname, Given (M.D.)" and "Clinic Department, Given Surname".

This module is the one home of WHICH entries are one encounter and WHEN one
may go in favour of the other; collect (``repeats.dedupe_new``) and the
update's final set (``final_set``) both call it.

* ``key``: the entry's date and the words of its provider that can name a
  person (facility, credential, specialty and office words aside,
  ``name_tokens``). Two entries are ONE ENCOUNTER (``same_encounter``) when
  they share the date and their providers canonicalise alike, or their name
  words overlap by at least NAME_MIN (Jaccard), or they cite a page whose
  picture is the very same page (``page_ids``: only an exact picture match,
  ``update/pageid``; a near one may say another thing).
* ``held``: what one entry says, every run of RUN consecutive words and
  numbers of its body (citations aside, the order of ``update/contained``),
  is in the other entry's body. A body with fewer than RUN tokens is never
  held: when in doubt both stay.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from . import body as B
from .contained import RUN, runs_of, stream_of

#: The share of two providers' name words (Jaccard) that makes them one.
NAME_MIN = 0.5
#: Words of a provider line that name no person.
_NOT_A_NAME = {"the", "and", "for", "office", "visit", "practice", "physician", "provider", "doctor", "staff", "inc"}
#: A specialty or service word a facility heading carries ("Orthopedics", "Pain Management").
_SPECIALTY = re.compile(
    r"(?i)^(?:(?:orthop|neurolog|neurosurg|physiat|chiropr|radiolog|pediatr|cardiolog|rehabilit|occupational|"
    r"pulmon|gastroent|dermatol|psychiat|podiat|urolog|obstet|gynec)[a-z]*|(?:family|internal|spine|pain|sports|"
    r"primary|general|management|physical|therapy|physicians))$"
)
Canon = Callable[[str], str]
#: (exhibit, page) -> the page's identity: the same string for two pages whose
#: pictures are one page, its own "exhibit:N:P" otherwise.
PageId = Callable[[int, int], str]


def name_tokens(provider: str) -> frozenset[str]:
    """The words of a provider line that can name a person, lowercase."""
    from ...stages.group import CRED_SEG, FACILITY_WORD

    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", provider or ""):
        if FACILITY_WORD.search(w) or CRED_SEG.match(w) or w.lower() in _NOT_A_NAME or _SPECIALTY.match(w):
            continue
        out.add(w.lower())
    return frozenset(out)


def cited_pages(e: str) -> list[tuple[int, int]]:
    """Every (exhibit, page) the entry cites, in order, each once."""
    from ..audit.claims import parse_pages

    out: list[tuple[int, int]] = []
    for m in B.CITE.finditer(e):
        for p in parse_pages(m.group(2)) or [1]:
            out.append((int(m.group(1)), p))
    return list(dict.fromkeys(out))


def page_ids(e: str, page_id: PageId | None) -> frozenset[str]:
    if page_id is None:
        return frozenset()
    return frozenset(page_id(ex, p) for ex, p in cited_pages(e))


def key(e: str) -> str:
    """The encounter key as one printable string: date|name+words."""
    return f"{B.iso(e)}|{'+'.join(sorted(name_tokens(B.provider_of(e))))}"


def name_score(a: str, b: str) -> float:
    ta, tb = name_tokens(B.provider_of(a)), name_tokens(B.provider_of(b))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def same_encounter(a: str, b: str, canon: Canon = str, page_id: PageId | None = None) -> bool:
    """One date, and one provider (canonical, or by name words) or one page."""
    if B.iso(a) != B.iso(b):
        return False
    if canon(B.provider_of(a)) == canon(B.provider_of(b)):
        return True
    if not B.iso(a):  # no date: only the provider itself makes two entries one
        return False
    return name_score(a, b) >= NAME_MIN or bool(page_ids(a, page_id) & page_ids(b, page_id))


def _stream(e: str) -> tuple[str | None, ...]:
    return stream_of("\n".join(e.splitlines()[2:]))


def held(e: str, by: str) -> bool:
    """Every run of RUN tokens of `e`'s body is a run of `by`'s body."""
    mine = runs_of(_stream(e))
    if not mine:
        return False
    have = set(runs_of(_stream(by)))
    return all(r in have for r in mine)


def page_identical(e: str, by: str, page_id: PageId | None) -> bool:
    """Every page `e` cites is, by its picture, a page `by` cites."""
    mine = page_ids(e, page_id)
    return bool(mine) and mine <= page_ids(by, page_id)


def pairs(entries: Iterable[str], canon: Canon = str, page_id: PageId | None = None) -> list[tuple[int, int]]:
    """(i, j), i < j, for every two entries of one encounter."""
    es = list(entries)
    return [
        (i, j) for i in range(len(es)) for j in range(i + 1, len(es)) if same_encounter(es[i], es[j], canon, page_id)
    ]


__all__ = [
    "NAME_MIN",
    "RUN",
    "cited_pages",
    "held",
    "key",
    "name_tokens",
    "page_identical",
    "page_ids",
    "pairs",
    "same_encounter",
]
