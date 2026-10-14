"""Which composed entries an update keeps, and in what shape. $0, no model.

An entry restating a visit the chronology already holds is kept only for what
it ADDS, and what it adds is decided on two things:

* VALUES: every number it states outside citations (``values.numbers``, the
  same notion proof 10 holds the document to, so a decision here can never
  drop a value that proof then fails on);
* TERMS: the diagnosis and impression words it states (``values.terms``), so a
  qualitative finding ("examination revealed paraspinal spasm") is no longer
  dropped for carrying no number.

A restatement of a DELIVERED visit that adds something becomes an "Additional
findings" row: only the sentences carrying a new value or term, each with its
citation, cut deterministically from the composed text (no model rewrite), so
each is audited as a new claim and nothing the firm already reads is said
twice. Between NEW entries, one with the date and provider of another whose
values and terms it does not exceed is dropped (two copies of one record).
"""

from __future__ import annotations

import re
from typing import Callable

from . import body as B
from .values import numbers, terms, words

ADDITIONAL = "Additional findings"
SAME_VISIT = "dropped: same visit as a delivered entry, no value or finding it lacks"
REPEAT_NEW = "dropped: repeats another new entry of that date and provider"
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
Canon = Callable[[str], str]


def _same(e: str, others: list[str], canon: Canon) -> list[str]:
    d = B.iso(e)
    return [
        b
        for b in others
        if B.iso(b) == d and (canon(B.provider_of(b)) == canon(B.provider_of(e)) or B.score(e, b) >= 0.5)
    ]


def _new_in(text: str, values: set[str], vocab: set[str]) -> bool:
    return bool(numbers(text) - values) or bool(terms(text) - vocab)


def _sentences(e: str) -> list[tuple[str, str]]:
    """(sentence, citation) for every cited sentence of the entry's body: a
    sentence takes the citation that closes its span."""
    out: list[tuple[str, str]] = []
    for ln in e.splitlines()[2:]:
        prev = 0
        for m in B.CITE.finditer(ln):
            seg = ln[prev : m.start()].strip()
            prev = m.end()
            out.extend((s.strip(), m.group(0)) for s in SENTENCE.split(seg) if len(s.strip()) > 3)
    return out


def additional(e: str, same_day: list[str]) -> str | None:
    """The "Additional findings" row: `e`'s sentences that carry a value or a
    term the delivered entries of that day lack, or None when no single
    sentence does (then the whole entry is kept: what it adds is spread)."""
    have_v = set().union(*(numbers(b) for b in same_day)) if same_day else set()
    have_w = set().union(*(words(b) for b in same_day)) if same_day else set()
    keep = [f"{s} {c}" for s, c in _sentences(e) if _new_in(s, have_v, have_w)]
    if not keep or numbers(e) - have_v - set().union(*(numbers(k) for k in keep)):
        return None  # a value outside every cited sentence would be lost: keep the entry whole
    lines = e.splitlines()
    return "\n".join([lines[0], f"{B.provider_of(e)} | {ADDITIONAL}", "", ADDITIONAL, *keep])


def keep_decision(e: str, base_ents: list[str], canon: Canon) -> tuple[str, str | None]:
    """('keep', entry) | ('keep: additional findings', row) | (reason, None)."""
    if not _same(e, base_ents, canon):
        return "keep", e
    same_day = [b for b in base_ents if B.iso(b) == B.iso(e)]
    have_v = set().union(*(numbers(b) for b in same_day))
    have_w = set().union(*(words(b) for b in same_day))
    if not _new_in(e, have_v, have_w):
        return SAME_VISIT, None
    row = additional(e, same_day)
    return ("keep: additional findings", row) if row else ("keep", e)


def _covered(e: str, by: str) -> bool:
    return numbers(e) <= numbers(by) and terms(e) <= words(by)


def dedupe_new(kept: list[str], canon: Canon) -> tuple[list[str], int]:
    """New entries less any whose date and provider another new entry shares
    and whose values and terms that entry covers (the longer one stays)."""
    order = sorted(range(len(kept)), key=lambda i: -len(kept[i]))
    out: list[int] = []
    dropped = 0
    for i in order:
        e = kept[i]
        twin = [
            kept[j]
            for j in out
            if B.iso(kept[j]) == B.iso(e) and canon(B.provider_of(kept[j])) == canon(B.provider_of(e))
        ]
        if any(_covered(e, t) for t in twin):
            dropped += 1
            continue
        out.append(i)
    return [kept[i] for i in sorted(out)], dropped
