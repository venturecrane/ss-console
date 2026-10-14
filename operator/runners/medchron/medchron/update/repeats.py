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

import difflib
import re
from typing import Callable

from . import body as B
from .values import numbers, terms, words

ADDITIONAL = "Additional findings"
SAME_VISIT = "dropped: same visit as a delivered entry, no value or finding it lacks"
REPEAT_NEW = "dropped: repeats another new entry of that date and provider"
NEAR = 0.95  # how alike two texts read to count as one restated
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
        prev, last = 0, None
        for m in B.CITE.finditer(ln):
            seg = ln[prev : m.start()].strip()
            prev, last = m.end(), m.group(0)
            out.extend((s.strip(), last) for s in SENTENCE.split(seg) if len(s.strip()) > 3)
        rest = ln[prev:].strip().lstrip(".;, ")
        if rest and last:  # text after the line's last citation travels with it, never dropped
            out.extend((s.strip(), last) for s in SENTENCE.split(rest) if len(s.strip()) > 3)
    return out


def additional(e: str, same_day: list[str]) -> str | None:
    """The "Additional findings" row: `e`'s sentences that carry a value or a
    term the delivered entries of that day lack, or None when no single
    sentence does (then the whole entry is kept: what it adds is spread)."""
    have_v = set().union(*(numbers(b) for b in same_day)) if same_day else set()
    have_w = set().union(*(words(b) for b in same_day)) if same_day else set()
    keep = [f"{s} {c}" for s, c in _sentences(e) if _new_in(s, have_v, have_w)]
    if not keep:
        return None
    lost_v = numbers(e) - have_v - set().union(*(numbers(k) for k in keep))
    lost_t = terms(e) - have_w - set().union(*(terms(k) for k in keep))
    if lost_v or lost_t:
        return None  # a value or finding outside every kept sentence would be lost: keep the entry whole
    lines = e.splitlines()
    return "\n".join([lines[0], f"{B.provider_of(e)} | {ADDITIONAL}", "", ADDITIONAL, *keep])


def _body(e: str) -> str:
    """The entry's body, normalised: lowercase words, citations and headings
    furniture removed, so two copies of one text compare equal."""
    t = B.CITE.sub(" ", "\n".join(e.splitlines()[2:])).lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _restates(e: str, by: str) -> bool:
    """True when `e`'s body is `by`'s, or inside it, near-exactly."""
    a, b = _body(e), _body(by)
    return bool(a) and (a in b or difflib.SequenceMatcher(None, a, b).ratio() >= NEAR)


def _plain(e: str) -> bool:
    """No value and no finding term: nothing the value-and-term test can see."""
    body = "\n".join(e.splitlines()[2:])  # the date line's own digits are not a value
    return not numbers(body) and not terms(body)


def keep_decision(e: str, base_ents: list[str], canon: Canon) -> tuple[str, str | None]:
    """('keep', entry) | ('keep: additional findings', row) | (reason, None).
    An entry with no value and no finding term is dropped only when its text
    restates a same-visit delivered entry: the value-and-term test cannot see
    what it says, and a visit dropped here is never read again."""
    same = _same(e, base_ents, canon)
    if not same:
        return "keep", e
    if _plain(e):
        return (SAME_VISIT, None) if any(_restates(e, b) for b in same) else ("keep", e)
    same_day = [b for b in base_ents if B.iso(b) == B.iso(e)]
    have_v = set().union(*(numbers(b) for b in same_day))
    have_w = set().union(*(words(b) for b in same_day))
    if not _new_in(e, have_v, have_w):
        return SAME_VISIT, None
    row = additional(e, same_day)
    return ("keep: additional findings", row) if row else ("keep", e)


_DRAW_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
_ACCESSION = re.compile(
    r"(?i)\b(?:accession|acc|specimen(?:\s*id)?)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,})"
)
_PANEL = re.compile(
    r"(?i)\b(cbc|complete blood count|cmp|comprehensive metabolic panel|bmp|basic metabolic panel|lipid panel|"
    r"urinalysis|hemoglobin a1c|a1c|tsh|hepatic function panel|coagulation panel|pt/inr)\b"
)
SAME_DRAW = "merged: one lab draw entered from two new documents"


def _draw(e: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """(draw time or accession marks, panels) of a lab entry, or None."""
    body = "\n".join(e.splitlines()[2:])
    panels = frozenset(p.lower() for p in _PANEL.findall(body))
    marks = frozenset(_DRAW_TIME.findall(body)) | frozenset(a.upper() for a in _ACCESSION.findall(body))
    return (marks, panels) if panels and marks else None


def merge_draws(kept: list[str]) -> tuple[list[str], int]:
    """New entries with one lab draw entered twice (the same date, the same
    draw time or accession, the same panels: two documents carrying one
    report) become one entry: the longer one, plus each sentence of the other
    stating a value it lacks, plus a line citing the other document, so no
    value is lost and both sources are cited."""
    out = list(kept)
    merged = 0
    i = 0
    while i < len(out):
        j, joined = None, ""
        for k in range(i + 1, len(out)):
            joined = _merge_one(out[i], out[k])
            if joined:
                j = k
                break
        if j is None:
            i += 1
            continue
        out[i] = joined
        del out[j]
        merged += 1
    return out, merged


def _merge_one(a: str, b: str) -> str:
    """The two entries as one when they carry one lab draw and nothing is
    lost by joining them, else ''."""
    da, db = _draw(a), _draw(b)
    if not (da and db and B.iso(a) == B.iso(b) and da[1] == db[1] and da[0] & db[0]):
        return ""
    big, small = (a, b) if len(a) >= len(b) else (b, a)
    extra = [f"{s} {c}" for s, c in _sentences(small) if numbers(s) - numbers(big)]
    cites = sorted({m.group(0) for m in B.CITE.finditer(small)} - {m.group(0) for m in B.CITE.finditer(big)})
    joined = "\n".join([big, *extra, *[f"The same results are reported in {c}." for c in cites[:1]]])
    return "" if numbers(small) - numbers(joined) else joined


def _covered(e: str, by: str) -> bool:
    if _plain(e):
        return _restates(e, by)
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
