"""A short reference to a caption party: "to the middle school", "the school district".

**The incident (2026-11-23).** An attorney asked for "sets of discovery to the
middle school and school district". The caption printed "<X> Middle School"
and "<Y> Unified School District"; ``parties.named_in`` matches a party only
when the request prints its whole name, so neither was named.

**The rule.** A short reference serves a party only where the request sends
something: right after "to the" or "upon the", or after "and" / "or"
continuing such a reference ("to the middle school and school district"). A
run of the request's words there names a caption party when

* it is that party's last words as printed, ending in an entity word
  (school, district, county, department, hospital, inc, llc ...);
* the word after it ends the phrase: the end of the text, punctuation, "and",
  "or", "to" or "upon". Never a noun ("to the district attorney"), never a
  possessive ("to the school district's counsel"): no shorter run is tried
  past a word that continues the phrase;
* exactly one caption party ends so. Two stop (``PartyUnknown``, SMD
  alerted, never a guess); a one-word run ("to the school") whose word is in
  another party's name stops too.

A run no party ends with names nothing, and the plan's own rules stop it
("names none of the caption's defendants"). Facts are never parties: "on the
middle school playground", "records for the school district" and "what he
told to the district attorney" name no one.
"""

from __future__ import annotations

import re

from .parties import _DETERMINERS, _ENTITY, Candidate, PartyUnknown, _aliases, _tokens, _words_all

#: The words that send a set to a party, before "the".
SENDING = frozenset({"to", "upon"})
#: A word after the run that ends the phrase.
_ENDS = frozenset({"and", "or", "to", "upon"})
_PUNCT = re.compile(r"[.,;:!?()\[\]\n]")
TAIL_MAX = 4  # words a short reference may carry


def tail_of(run: list[str], c: Candidate) -> bool:
    """``run`` is the last words of ``c`` as printed, ending in an entity word."""
    mine = _words_all(c.text)
    return bool(run) and len(run) <= len(mine) and mine[-len(run) :] == run and run[-1] in _ENTITY


def tail_party(run: list[str], singles: list[Candidate]) -> Candidate | None:
    """The one party ``run`` is the tail of; None for none; two stop, and so
    does a one-word run another party's name carries."""
    hit: dict[frozenset[str], Candidate] = {}
    for c in singles:
        if tail_of(run, c):
            hit.setdefault(_aliases(c.text) or frozenset({c.text.lower()}), c)
    if len(hit) > 1:
        names = " or ".join(f'"{c.text}"' for c in hit.values())
        raise PartyUnknown(f'the request\'s "the {" ".join(run)}" could be {names} on the caption')
    got = next(iter(hit.values()), None)
    if got is not None and len(run) == 1:
        other = next((c for c in singles if c is not got and run[0] in _words_all(c.text)), None)
        if other is not None and not (_aliases(other.text) & _aliases(got.text)):
            raise PartyUnknown(f'the request\'s "the {run[0]}" could be "{got.text}" or "{other.text}" on the caption')
    return got


def _ends(text: str, toks: list[tuple[str, int, int]], j: int) -> bool:
    """The run ending before token ``j`` ends its phrase there."""
    if j >= len(toks):
        return True
    return bool(_PUNCT.search(text[toks[j - 1][2] : toks[j][1]])) or toks[j][0] in _ENDS


def hits(text: str, cands: list[Candidate]) -> list[tuple[int, int, Candidate]]:
    """Where ``text`` names a caption party by a short reference: (start, end, party)."""
    toks = _tokens(text)
    words = [t for t, _a, _b in toks]
    singles = [c for c in cands if c.pieces == 1]
    out: list[tuple[int, int, Candidate]] = []
    starts = [i + 1 for i in range(1, len(words) - 1) if words[i] == "the" and words[i - 1] in SENDING]
    seen: set[int] = set()
    while starts:
        at = starts.pop(0)
        if at in seen:
            continue
        seen.add(at)
        for k in range(min(TAIL_MAX, len(words) - at), 0, -1):
            run = words[at : at + k]
            if any(w in _DETERMINERS for w in run) or not _ends(text, toks, at + k):
                continue
            c = tail_party(run, singles)
            if c is None:
                break  # the longest run that ends a phrase names no party: no shorter run is read
            end = at + k - 1
            out.append((toks[at][1], toks[end][2], c))
            nxt = end + 2 if words[end + 1 : end + 2] in (["and"], ["or"]) else -1
            if 0 < nxt < len(words):
                starts.insert(0, nxt + 1 if words[nxt] == "the" else nxt)
            break
    return out
