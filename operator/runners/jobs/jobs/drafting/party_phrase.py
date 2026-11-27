"""A party the request names in a shorter spelling than the caption's, and the
backstop that never plans fewer parties than the request names.

**The incident (2026-11-26, the $0 census replay).** A request asked for "sets
of discovery to the <Name> Middle School and <Name> School District". The
caption printed the district with one or two words the request left out
("<Name> City School District", "<Town> City <Name> Unified School
District"). Only the middle school resolved: the plan drafted three sets to
it and none to the district, with no stop and no note. The attorney would
have received half of what he asked for.

**The sending list.** Only where the request sends something: the words after
"to", "on" or "upon", up to the end of the sentence (or a colon, semicolon or
bracket), cut into items at commas, "&", "and" and "or". "and to <B>"
continues the list; any other sending word ends it.

**The short spelling (``hits``).** An item's longest run of words ending in an
entity word (school, district, city ...) names a caption party when

* every one of its words is in that party's caption name, in order (the
  caption may carry more words), and
* its distinctive words (not an entity word, nor a kind such as middle, high,
  elementary, unified) are ADJACENT in the caption name, and there is one.

Exactly one party must qualify; two stop (``PartyUnknown``). So "<Name>
School District" names "<Name> City School District", never "<Name> Middle
School" (no "district"), and "Northfield Union School District" never names
"Northfield Joint Union School District" ("northfield union" is not adjacent
there): that spelling stays a near miss, and stops.

**The backstop (``unnamed``).** When an item of a sending list names a party
and another item of the same list reads as a party (capitalized, or ending in
an entity word; never a set, a role such as counsel or plaintiff, "all
defendants", or a description) yet names none, the job stops
``party_unknown``. Never a plan with fewer parties than the request names.
"""

from __future__ import annotations

import re

from .parties import _ENTITY, _STOP, Candidate, PartyUnknown, _aliases, _hits, _noise, _tokens, _words

SENDING = frozenset({"to", "on", "upon"})
_JOIN = frozenset({"and", "or"})
#: Text between two words that ends a sending list.
_LIST_END = re.compile(r"[.;:!?()\[\]\n]")
#: Text between two words that starts the next item.
_ITEM = re.compile(r"[,&]")
#: Words that say what kind of entity, never which one.
KIND = frozenset("middle high elementary junior senior unified public community".split())
#: Leading words an item drops before it is read as a name.
_LEAD = frozenset({"the", "defendant", *SENDING})
#: An item carrying one of these is no party served.
NOT_PARTY = frozenset(
    "plaintiff plaintiffs plaintiff's counsel attorney attorneys me us our my him her them you "
    "rfa rfas rfp rfps rfpd srog srogs frog frogs interrogatories requests admission admissions production "
    "discovery set sets copy copies all each both every defendants".split()
)

Tok = tuple[str, int, int]


def _lists(text: str) -> list[list[list[Tok]]]:
    """Each sending list of ``text``: its items, each its tokens."""
    toks, out = _tokens(text), []
    for k, (w, _a, _b) in enumerate(toks):
        if w not in SENDING:
            continue
        items: list[list[Tok]] = [[]]
        for m in range(k + 1, len(toks)):
            gap = text[toks[m - 1][2] : toks[m][1]]
            if _LIST_END.search(gap):
                break
            word = toks[m][0]
            if word in SENDING and not (m > k + 1 and toks[m - 1][0] in _JOIN):
                break
            if _ITEM.search(gap) or word in _JOIN:
                items.append([])
            if word not in _JOIN:
                items[-1].append(toks[m])
        out.append([_bare(i) for i in items if _bare(i)])
    return out


def _bare(item: list[Tok]) -> list[Tok]:
    at = 0
    while at < len(item) and item[at][0] in _LEAD:
        at += 1
    return item[at:]


def _in_order(inner: list[str], outer: list[str]) -> bool:
    it = iter(outer)
    return all(w in it for w in inner)


def _adjacent(run: list[str], outer: list[str]) -> bool:
    return any(outer[i : i + len(run)] == run for i in range(len(outer) - len(run) + 1))


def _short(item: list[Tok], singles: list[Candidate]) -> tuple[int, int, Candidate] | None:
    """The one party ``item`` names in a shorter spelling, as (start, end, party)."""
    ends = [i for i, t in enumerate(item) if t[0] in _ENTITY and i > 0]
    if not ends:
        return None
    end = ends[-1]
    mine = [t[0] for t in item[: end + 1] if t[0] not in _STOP]
    own = [w for w in mine if w not in _ENTITY and w not in KIND]
    if not own:
        return None
    hit: dict[frozenset[str], Candidate] = {}
    for c in singles:
        theirs = _words(c.text)
        if _in_order(mine, theirs) and _adjacent(own, theirs):
            hit.setdefault(_aliases(c.text) or frozenset({c.text.lower()}), c)
    if len(hit) > 1:
        names = " or ".join(f'"{c.text}"' for c in hit.values())
        raise PartyUnknown(f'the request\'s "{" ".join(mine)}" could be {names} on the caption')
    got = next(iter(hit.values()), None)
    return None if got is None else (item[0][1], item[end][2], got)


def hits(text: str, cands: list[Candidate]) -> list[tuple[int, int, Candidate]]:
    """Where ``text``'s sending lists name a caption party in a shorter spelling."""
    singles = [c for c in cands if c.pieces == 1]
    found = [_short(item, singles) for items in _lists(text) for item in items]
    return [h for h in found if h is not None]


def _party_like(text: str, item: list[Tok]) -> bool:
    words = [t[0] for t in item]
    if any(w in NOT_PARTY for w in words) or _noise(text[item[0][1] : item[-1][2]]):
        return False
    return text[item[0][1]].isupper() or words[-1] in _ENTITY


def unnamed(text: str, cands: list[Candidate], found: list[Candidate]) -> None:
    """``PartyUnknown`` when a sending list names a party in one item and, in
    another, something that reads as a party but is none of the caption's."""
    from . import party_tail

    if not found:
        return
    spans = [(a, b) for c in found for a, b, _c in _hits(text, c)]
    spans += [(a, b) for a, b, c in party_tail.hits(text, cands) + hits(text, cands) if c in found]
    for items in _lists(text):
        covered = [any(a < it[-1][2] and it[0][1] < b for a, b in spans) for it in items]
        if len(items) < 2 or not any(covered):
            continue
        for it, ok in zip(items, covered):
            if not ok and _party_like(text, it):
                said = text[it[0][1] : it[-1][2]][:80]
                raise PartyUnknown(
                    f'the request names "{said}" beside a caption party, and no caption party is named so; '
                    "the plan would serve fewer parties than the request names"
                )
