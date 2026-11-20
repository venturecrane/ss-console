"""The repeats read's code: what a reader's ``repeats`` may remove, and the
removal. $0. Every rule here can only KEEP text (``repeat_read`` is why).

A paragraph of an added entry is dropped only when ALL hold:

(a) the first read and a second read not shown the first's answer both say
    it repeats, naming the same keeper (the second read is only shown that
    keeper);
(b) every sentence either read quotes is a WHOLE sentence (or a run of
    whole sentences) of one paragraph of that keeper, of at least
    ``repeat_words.MIN_QUOTE`` tokens;
(c) every number of the paragraph (``values.numbers``, the one proof 10
    reads: a time of day, a ratio and a year are numbers too) is among the
    first read's quoted sentences' numbers, and every date it writes
    (``final_set.dates_in``) among theirs;
(d) every WORD of the paragraph but a short list of function words is among
    the quoted sentences' words, each sentence of it within one quoted
    sentence, and a sentence holding a negation equal to one token for token
    (``repeat_words``: a list of the words that matter let every word it did
    not name through, 2026-11-20);
(e) no sentence it is dropped against lies in a paragraph also being
    dropped in this run (keepers are a fixed set, ``repeat_read``; this keeps
    a chain of drops from removing the last copy of a fact);
(f) no exhibit the document cites is left cited nowhere
    (``final_set.exhibits_uncited``). WITHIN ONE ENTRY (the earlier
    paragraph is the keeper), a drop that would uncite an exhibit instead
    appends the dropped paragraph's citations of it to the keeper
    paragraph's last citation (``cite_merge``): a citation-only merge, no
    sentence text changed and no claim key changed, since a citation after a
    citation closes an empty segment no audit grades. A keeper whose text
    does not end at a citation keeps the paragraph.

Whole paragraphs only; never a sentence edited, no text added but that
merged citation, never a delivered entry touched (they are not in this
document). A paragraph that
opens a subsection heading the next paragraph shares is kept (the next
paragraph would lose its heading, and its claim key with it). A subsection
heading left with nothing under it goes with what was under it. An added
entry left with no paragraph is removed whole only when every word of its
provider line is in each keeper's provider line; one left with content but
no citation keeps its paragraphs.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from . import body as B
from .final_set import dates_in, exhibits_uncited, with_entries
from .repeat_read import DELIVERED, WITHIN, Candidate, Keeper, Para, Verdict
from .repeat_words import short, tokens, whole_sentence, word_legs
from .values import numbers

#: Why a paragraph the first read called a repeat was kept, one name per veto leg.
LEGS = (
    "keeper_unknown",
    "quote_not_found",
    "number",
    "date",
    "quote_short",
    "words",
    "sentence",
    "unit",
    "second_read",
    "conflict_elsewhere",
    "keeper_dropped",
    "heading",
    "rest_uncited",
    "header",
    "exhibit_uncited",
)


@dataclass
class Drop:
    cand: Candidate
    para: Para
    keeper: Keeper
    quotes: list[str]
    blocks: set[int] = field(default_factory=set)  # the keeper's paragraphs the quotes lie in

    @property
    def at(self) -> tuple[int, int]:
        return self.cand.index, self.para.index


def drop(cand: Candidate, para: Para, keeper: Keeper, quotes: list[str]) -> Drop:
    """The drop a first read proposes, with the keeper paragraphs its quotes lie in."""
    blocks = {keeper.block} if keeper.kind == WITHIN else blocks_of(keeper.text, quotes) or set()
    return Drop(cand, para, keeper, quotes, blocks)


def blocks_of(keeper: str, quotes: list[str]) -> set[int] | None:
    """The keeper's paragraphs each quote is a whole sentence of, or None when a quote is in none."""
    paras = keeper.split("\n\n")
    out: set[int] = set()
    for q in quotes:
        hit = next((i for i, p in enumerate(paras) if whole_sentence(q, p)), None)
        if hit is None:
            return None
        out.add(hit)
    return out


def code_legs(para: Para, keeper: Keeper | None, v: Verdict) -> list[str]:
    """The legs (b), (c) and (d) the first read's repeat fails; [] passes."""
    if keeper is None:
        return ["keeper_unknown"]
    if not v.quotes or short(v.quotes):
        return ["quote_short"]
    if blocks_of(keeper.text, v.quotes) is None:
        return ["quote_not_found"]
    said = " ".join(v.quotes)
    out = []
    if numbers(para.body) - numbers(said):
        out.append("number")
    if dates_in(para.body) - dates_in(said):
        out.append("date")
    return out + word_legs(para.body, v.quotes)


def agrees(keeper: Keeper, states_all: bool, quotes: list[str]) -> bool:
    """Leg (a) and (b) of the second read: it says so and quotes the keeper."""
    return states_all and bool(quotes) and not short(quotes) and blocks_of(keeper.text, quotes) is not None


# ---- the removal ------------------------------------------------------------------------
def _is_head(line: str, headings: set[str]) -> bool:
    return line.strip() in headings


def _heads_only(block: str, headings: set[str]) -> bool:
    lines = [ln for ln in block.split("\n") if ln.strip()]
    return bool(lines) and all(_is_head(ln, headings) for ln in lines)


def _keeps_heading(d: Drop, headings: set[str]) -> bool:
    """True when the paragraph opens a heading the next paragraph needs."""
    blocks = d.cand.entry.split("\n\n")
    nxt = d.para.index + 1
    return d.para.heads > 0 and nxt < len(blocks) and not _is_head(blocks[nxt].split("\n")[0], headings)


def entry_without(entry: str, gone: set[int], headings: set[str]) -> str:
    """`entry` less its paragraphs at `gone`, and less a heading left alone by them."""
    blocks = entry.split("\n\n")
    keep = [i for i in range(len(blocks)) if i not in gone]
    out = []
    for n, i in enumerate(keep):
        nxt = keep[n + 1] if n + 1 < len(keep) else None
        orphan = i + 1 in gone and _heads_only(blocks[i], headings)
        if orphan and (nxt is None or _is_head(blocks[nxt].split("\n")[0], headings)):
            continue
        out.append(blocks[i])
    return "\n\n".join(out)


def _content(entry: str, headings: set[str]) -> bool:
    return any(not _heads_only(b, headings) and b.strip() for b in entry.split("\n\n")[1:])


Adds = dict[int, dict[int, list[str]]]  # {entry index: {block index: citations appended}}


@dataclass
class Settled:
    md: str
    dropped: list[Drop]
    kept: Counter[str]
    entries_removed: int = 0
    cites_merged: int = 0  # paragraphs given a dropped copy's citation (``cite_merge``)

    def shas(self) -> list[str]:
        return sorted(hashlib.sha256(d.para.text.encode()).hexdigest() for d in self.dropped)


def _entry_veto(e: str, mine: list[Drop], headings: set[str]) -> str:
    """'' when the entry may lose `mine`, else the leg that keeps them."""
    if B.cited(e):
        return ""
    if _content(e, headings):
        return "rest_uncited"
    words = set(tokens(B.provider_of(mine[0].cand.entry)))
    return "header" if any(words - set(tokens(B.provider_of(d.keeper.text))) for d in mine) else ""


def _with_cites(entry: str, adds: dict[int, list[str]]) -> str:
    """`entry` with each citation in `adds` appended to its paragraph's last
    citation (``cite_merge``): no sentence text changes."""
    blocks = entry.split("\n\n")
    for b, cites in adds.items():
        blocks[b] = blocks[b].rstrip() + "".join(f" {c}" for c in cites)
    return "\n\n".join(blocks)


def _build(md: str, ents: list[str], drops: list[Drop], headings: set[str], adds: Adds) -> Settled:
    """The document with `drops` applied and `adds` merged; an entry's drops
    a rule keeps are taken back (counted in ``kept``)."""
    out, applied, removed, kept = list(ents), [], 0, Counter[str]()
    for i in sorted({d.cand.index for d in drops}):
        mine = [d for d in drops if d.cand.index == i]
        e = entry_without(_with_cites(ents[i], adds.get(i, {})), {d.para.index for d in mine}, headings)
        why = _entry_veto(e, mine, headings)
        if why:
            kept[why] += len(mine)
            continue
        applied += mine
        removed += int(not _content(e, headings))
        out[i] = e if _content(e, headings) else ""
    final = [e for e in out if e]
    return Settled(with_entries(md, final) if final != ents else md, applied, kept, removed)


def ends_cited(block: str) -> bool:
    """True when the paragraph's text ends at a citation (one appended there
    makes no claim of its own: ``audit/claims`` grades no empty segment)."""
    found = list(B.CITE.finditer(block))
    return bool(found) and block.rstrip().endswith(found[-1].group(0))


def cite_merge(d: Drop, x: int, ents: list[str], adds: Adds) -> bool:
    """IN AN ENTRY, the dropped paragraph's citations of exhibit `x` appended
    to its keeper paragraph's last citation, so `x` stays cited with no
    sentence edited. False (the paragraph is kept) when the keeper's text
    does not end at a citation."""
    if d.keeper.kind != WITHIN:
        return False
    i, b = d.keeper.index, d.keeper.block
    if not ends_cited(ents[i].split("\n\n")[b]):
        return False
    have = adds.setdefault(i, {}).setdefault(b, [])
    have += [c for c in dict.fromkeys(m.group(0) for m in B.CITE.finditer(d.para.text) if int(m.group(1)) == x)]
    return True


def _chained(drops: list[Drop], headings: set[str], kept: Counter[str]) -> list[Drop]:
    """Rule (e) and the heading rule; one drop per paragraph (the first)."""
    going, ok, seen = {d.at for d in drops}, [], set()
    for d in drops:
        if d.at in seen:
            continue
        seen.add(d.at)
        if d.keeper.kind != DELIVERED and any((d.keeper.index, b) in going for b in d.blocks):
            kept["keeper_dropped"] += 1
        elif _keeps_heading(d, headings):
            kept["heading"] += 1
        else:
            ok.append(d)
    return ok


def settle(md: str, drops: list[Drop], headings: set[str]) -> Settled:
    """Rules (e), (f), the heading and the entry rules over the drops both
    reads agree on and the code passed; the document as it then stands. An
    exhibit a drop would uncite stays cited by a citation merge in an entry
    (``cite_merge``), else the drops citing it are kept."""
    ents = B.entries_of(md)[1]
    kept: Counter[str] = Counter()
    ok, adds, tried = _chained(drops, headings, kept), {}, set()
    while True:
        s = _build(md, ents, ok, headings, adds)
        lost = exhibits_uncited(md, s.md)
        if not lost:
            return Settled(
                s.md,
                s.dropped,
                kept + s.kept,
                s.entries_removed,
                sum(1 for b in adds.values() for c in b.values() if c),
            )
        merged = False
        for x in sorted(lost - tried):
            tried.add(x)
            d = next((d for d in ok if x in B.cited(d.para.text) and cite_merge(d, x, ents, adds)), None)
            merged = merged or d is not None
        if merged:
            continue
        back = [d for d in ok if B.cited(d.para.text) & lost] or ok  # none to blame: keep every drop
        kept["exhibit_uncited"] += len(back)
        ok = [d for d in ok if d not in back]
