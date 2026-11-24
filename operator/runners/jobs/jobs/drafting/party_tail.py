"""A short reference to a caption party: "to the middle school", "the school district".

**The incident (2026-11-23).** An attorney asked for "sets of discovery to the
middle school and school district". The caption printed "<X> Middle School"
and "<Y> Unified School District"; ``parties.named_in`` matches a party only
when the request prints its whole name, so neither was named.

**The rule.** After a sending word ("to", "on", "upon", "for", "and") and
"the", a run of the request's words names a caption party when it is that
party's last words as printed, ending in an entity word (school, district,
county, department, hospital, inc, llc ...), and exactly one caption party
ends so. The longest such run wins. A run two parties end with stops
(``PartyUnknown``, SMD alerted, never a guess); a run no party ends with names
nothing, and the plan's own rules stop it ("names none of the caption's
defendants"). A second reference shares the first's "the" across "and" ("the
middle school and school district"). A school in the facts ("the incident at
the middle school") names no one: "at" is no sending word.
"""

from __future__ import annotations

from .parties import _DETERMINERS, _ENTITY, Candidate, PartyUnknown, _aliases, _tokens, _words_all

#: A sending word before "the <tail>".
SENDING = frozenset("to on upon for and".split())
TAIL_MAX = 4  # words a short reference may carry


def tail_of(run: list[str], c: Candidate) -> bool:
    """``run`` is the last words of ``c`` as printed, ending in an entity word."""
    mine = _words_all(c.text)
    return bool(run) and len(run) <= len(mine) and mine[-len(run) :] == run and run[-1] in _ENTITY


def tail_party(run: list[str], singles: list[Candidate]) -> Candidate | None:
    """The one party ``run`` is the tail of; None for none; two stop."""
    hit: dict[frozenset[str], Candidate] = {}
    for c in singles:
        if tail_of(run, c):
            hit.setdefault(_aliases(c.text) or frozenset({c.text.lower()}), c)
    if len(hit) > 1:
        names = " or ".join(f'"{c.text}"' for c in hit.values())
        raise PartyUnknown(f'the request\'s "the {" ".join(run)}" could be {names} on the caption')
    return next(iter(hit.values()), None)


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
            c = None if any(w in _DETERMINERS for w in run) else tail_party(run, singles)
            if c is None:
                continue
            end = at + k - 1
            out.append((toks[at][1], toks[end][2], c))
            if end + 2 < len(words) and words[end + 1] in ("and", "or") and words[end + 2] != "the":
                starts.insert(0, end + 2)
            break
    return out
