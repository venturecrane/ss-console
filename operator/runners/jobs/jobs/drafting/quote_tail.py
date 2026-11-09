"""Words beside a quotation that copy the record but change it.

WHY (2026-11-09, A&P, served set 3, Nos. 46-48). Each request quoted a
pleaded defense verbatim ("did not own, lease, ... control the premises")
and then carried the pleading's own next words on outside the quote marks
with one word dropped: the pleading says "where the alleged incident
occurred", the draft said "where the INCIDENT occurred". The mechanical
quotation check (``demand/draft.mechanical_checks``) reads only text inside
quote marks, so it passed; the model audit missed it for four rounds; a hand
pass restored the word before delivery. Dropping "alleged" turns a defense the
other side pleaded into a fact it never admitted.

The check, all code, free: for every quotation in the document, find it in
the record, take the record's next words, and compare them with the draft's
words after the closing quote (to the end of its clause). A draft that starts
the same way as the record (two words alike) and then departs from it, only to
rejoin it within a few words, has edited the record's words while presenting
them as the record's continuation: a dropped, added or swapped word. That is a
finding. A draft that simply goes on to its own words (", and state all
facts ...") never rejoins and is not one.

The finding is a line of the audit's MECHANICAL section, so the lane's
existing scoped repair, convergence and hold handle it like a quotation not
found: repaired, or held, never delivered.
"""

from __future__ import annotations

import re

#: the quotation shape ``demand/draft.mechanical_checks`` reads, but from 20
#: characters: a short pleading clause is exactly where this happens.
_QUOTE = re.compile(r'(?:(?<=\s)|(?<=\()|^)["“]([^"“”\n|]{20,400})["”]', re.M)
_CLAUSE_END = re.compile(r"[.;:?!\n()]")
_TOKEN = re.compile(r"[a-z0-9]+")
TAG = "TAIL"
#: how far past the departure the draft must rejoin the record (in words)
_RESYNC = 3
_TAIL_WORDS = 10


def _tokens(s: str) -> list[str]:
    return [t for t in _TOKEN.findall(s.lower()) if not t.isdecimal()]


def _departs(dt: list[str], st: list[str]) -> bool:
    """The draft's tail starts as the record's, departs, and rejoins it."""
    if len(dt) < 3 or dt[:2] != st[:2]:
        return False
    k = 2
    while k < len(dt) and k < len(st) and dt[k] == st[k]:
        k += 1
    if k >= len(dt):  # every draft word is the record's own continuation
        return False
    for j in range(k, min(k + _RESYNC, len(dt) - 1)):
        for i in range(k, min(k + _RESYNC + 1, len(st) - 1)):
            if (i, j) != (k, k) and dt[j : j + 2] == st[i : i + 2]:
                return True
    # a swapped word that rejoins right after it
    return k + 2 <= len(dt) and k + 2 <= len(st) and dt[k + 1 : k + 3] == st[k + 1 : k + 3] and dt[k] != st[k]


def findings(corpus_text: str, document: str) -> list[dict[str, str]]:
    """One row per quotation whose following words edit the record's."""
    ct = _tokens(corpus_text)
    first: dict[str, list[int]] = {}
    for i, t in enumerate(ct):
        first.setdefault(t, []).append(i)
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _QUOTE.finditer(document):
        qt = _tokens(m.group(1))
        if len(qt) < 4:
            continue
        rest = document[m.end() :]
        cut = _CLAUSE_END.search(rest)
        raw_tail = " ".join((rest[: cut.start()] if cut else rest).split())
        dt = _tokens(raw_tail)[:_TAIL_WORDS]
        if len(dt) < 3:
            continue
        hits = [i for i in first.get(qt[0], []) if ct[i : i + len(qt)] == qt]
        tails = [ct[i + len(qt) : i + len(qt) + _TAIL_WORDS + _RESYNC] for i in hits]
        if not tails or any(t[: len(dt)] == dt for t in tails):
            continue
        bad = [t for t in tails if _departs(dt, t)]
        if not bad:
            continue
        quote = " ".join(m.group(1).split())[:120]
        key = (quote, raw_tail)
        if key in seen:
            continue
        seen.add(key)
        out.append({"quote": quote, "draft": raw_tail, "record": " ".join(bad[0][: len(dt) + 1])})
    return out


def lines(rows: list[dict[str, str]]) -> list[str]:
    """The MECHANICAL-section lines: the quotation first (it locates the
    request), then what the draft and the record say after it."""
    return [
        f'- {r["quote"]} | {TAG} | the draft goes on "{r["draft"]}" after this quotation; '
        f'the record goes on "{r["record"]}"'
        for r in rows
    ]


def anchor(line_text: str) -> str:
    """What locates a MECHANICAL line's request: the quotation, never the
    explanation after it."""
    return line_text.split(" | ", 1)[0][:60]
