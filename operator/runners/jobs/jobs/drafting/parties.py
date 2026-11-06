"""Propounded discovery to more than one party: who each set is served on.

**The incident.** On 2026-11-05 an attorney asked for RFA, RFPD and SROG to
two defendants, a City and a School District. The job planned one unit per SET
TYPE, never per party, so it drafted to one defendant only; SMD submitted a
second job by hand, seeded it from the first, and fixed it by hand again.

**The rule.** Code decides the parties, from the request's own words and the
matter's parties (the court's caption first, the matter record when the caption
names none). A party is named when the request prints its name, a run of two or
more of its words no other party shares, or one capitalized word only it
carries ("the City"). A request that says "both defendants" (or each, all,
"defendants") is served on every other-side party. A name the request serves a
set on that is none of the matter's parties, or that could be two of them,
stops the job for SMD (``party_unknown``): it never guesses.

**The caption's own shape.** A court's caption often prints every defendant
in one string ("CITY OF X, a public entity; X SCHOOL DISTRICT, a public
entity; and DOES 1 through 50, inclusive"); the 11-05 rehearsal planned one
party from it. ``entities`` splits each value into the names it prints,
exactly as printed, drops the fictitious defendants and the descriptions, and
splits at "and" only between whole names ("Smith and Sons, Inc." stays one).
A jurisdiction qualifier ("County of X", "State of CA - Department of Y")
belongs to the party before it ("<agency>, County of X, State of CA - <dept>"
is one party); a semicolon, a description or "and" ends a party. A qualifier
after a name that is itself a jurisdiction ("CITY OF X, COUNTY OF Y": two
parties, or one?) is undecided. A value it cannot split is kept whole and
named in the stop.

**A scope line.** "to X only" (the attorney's words, or SMD's hand line on a
re-submitted job: the envelope's only free text is ``request_text``) limits
the plan to exactly those parties, even one; each is still checked against
the matter's parties.

A request that serves every defendant, or names two or more parties, is never
planned as one party: when the matter resolves to fewer, or two of its names
are one party, it stops (``party_unknown``) and says why. A request served on
ONE party (or a matter with one other-side party) plans no parties at all, so
it drafts exactly as before. SMD may name the parties by
hand in the job directory's ``parties.json`` (``{"parties": [...]}``); each name
is still checked against the matter's parties.

``bind`` then writes the unit's party into its identification block, its title
and its definition of YOU / YOUR by code, so a set to one party never carries
another's name where the party served is stated.

Rehearsal at $0 (reads job.json, data/caption.json and data/facts.json; spends
nothing, writes nothing): ``python -m jobs.drafting.parties <job_dir>``.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import caption as caption_mod, sets

OVERRIDE = "parties.json"

#: Words that never identify a party on their own.
_STOP = frozenset("the of a an and inc llc l c corp corporation co company ltd lp llp pc".split())
_NON_ID = frozenset(
    "defendant defendants plaintiff plaintiffs cross complainant complainants respondent respondents party parties "
    "set sets one two three four five".split()
)
#: Every defendant, in so many words: never planned as one party.
_BOTH = re.compile(
    r"(?i)\b(?:both|all|each|every)\s+(?:of\s+the\s+)?(?:named\s+)?(?:defendants?|responding\s+parties|parties)\b"
)
#: "to defendants": every defendant when the matter has several.
_PLURAL = re.compile(r"(?i)\b(?:to|on|upon)\s+(?:the\s+)?defendants\b")
_SET_WORDS = "|".join(p.pattern.removeprefix("(?i)") for _k, _l, p in sets.VARIANTS)
#: A set word, then the parties it is served on ("RFAs ... to the City and the District").
_SERVED = re.compile(
    r"(?i)(?:"
    + _SET_WORDS
    + r")[^.;\n]*?\b(?:to|on|upon|directed\s+to|served\s+on|propounded\s+on)\s+(?P<who>[^.;\n]+)"
)
_LEAD = re.compile(r"(?i)^(?:\s*(?:to|on|upon|the|both|each|all|of|defendants?|plaintiffs?)\b)+\s*")


class PartyUnknown(ValueError):
    """The request serves a set on a name that is not one of the matter's parties."""


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if w not in _STOP]


def clean(name: str) -> str:
    """A party's name as printed, without its fictitious defendants ("and DOES 1-25")."""
    return caption_mod._DOES.sub("", str(name or "")).strip(" ,;")


def same(a: str, b: str) -> bool:
    x, y = " ".join(_words(a)), " ".join(_words(b))
    return bool(x and y) and (x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.9)


#: "DOES 1 through 50", "Does 1-50", "ROES 1 to 10": fictitious defendants, never a party served.
_FICTITIOUS = re.compile(r"(?i)^(?:and\s+)?(?:does?|roes?)\s+\d+\s*(?:-|–|to|through|thru)\s*\d+\b")
#: What a caption prints after a name, describing it ("a public entity", "inclusive").
_DESCRIBES = re.compile(r"(?i)^(?:an?\s|inclusive\b|et\s+al\b|individually\b|jointly\b|erroneously\b|sued\s+as\b)")
#: A corporate suffix printed after a comma ("Smith and Sons, Inc."): part of the name before it.
_SUFFIX = re.compile(r"(?i)^(?:inc|llc|l\.l\.c|corp|corporation|co|ltd|lp|llp|pc|p\.c|n\.a)\.?$")
#: Words that make one word, or one phrase, a whole entity name.
_ENTITY = frozenset(
    "city county district state department board authority agency commission school college university hospital "
    "inc llc corporation corp company association trust regents trustees bank group partners partnership services "
    "center clinic church police sheriff transit".split()
)


#: A jurisdiction or location a caption prints after an agency's name ("County of X",
#: "State of CA - Department of Y"): part of the party before it, never a party of its own.
_QUALIFIER = re.compile(r"(?i)^(?:the\s+)?(?:state|county|city|commonwealth|territory)\s+of\s+\S")


class Undecided(ValueError):
    """A caption value code cannot split into entities with confidence."""


def _entity(piece: str) -> bool:
    """A piece that reads as a whole party name on its own."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'.&-]*", piece)
    caps = [w for w in words if w[:1].isupper() or w[:1].isdigit()]
    if not caps or not piece.strip()[:1].isalnum():
        return False
    return len(caps) >= 2 or any(w.lower().strip(".") in _ENTITY for w in words)


def entities(value: str) -> list[str]:
    """The parties one caption value names, each exactly as printed: split at
    commas and semicolons, then at "and" only where every piece is a whole
    name ("Smith and Sons, Inc." stays one). Fictitious defendants and
    descriptions are dropped. ``Undecided`` when a piece is neither a name, a
    description, a suffix nor a fictitious entry."""
    pieces: list[str] = []
    qualified: set[int] = set()  # pieces that carry a jurisdiction qualifier: never split at "and"
    boundary = True  # the next piece starts a party: the value's start, a semicolon, a description, "and"
    for raw in re.split(r"([;,])", str(value or "")):
        if raw == ";":
            boundary = True
            continue
        p = raw.strip()
        if not p or p == ",":
            continue
        if _DESCRIBES.match(p):
            boundary = True
            continue
        if _SUFFIX.match(p) and pieces:
            pieces[-1] = f"{pieces[-1]}, {p}"
            continue
        if re.match(r"(?i)^and\s+", p):
            p, boundary = re.sub(r"(?i)^and\s+", "", p).strip(), True
        if _FICTITIOUS.match(p) or _DESCRIBES.match(p):
            boundary = True
            continue
        if _QUALIFIER.match(p) and not boundary and pieces:
            if _QUALIFIER.match(pieces[-1]):
                # "CITY OF X, COUNTY OF Y": two parties, or one qualified? Code cannot tell.
                raise Undecided(f'"{p[:80]}" after "{pieces[-1][:60]}" in the caption\'s "{str(value)[:160]}"')
            pieces[-1] = f"{pieces[-1]}, {p}"  # "<agency>, County of X, State of CA - <dept>": one party
            qualified.add(len(pieces) - 1)
            continue
        pieces.append(p)
        boundary = False
    out: list[str] = []
    for i, p in enumerate(pieces):
        if i in qualified:
            out.append(p)
            continue
        parts = [x.strip() for x in re.split(r"(?i)\s+and\s+", p)]
        parts = [x for x in parts if x and not _FICTITIOUS.match(x)]
        if len(parts) > 1 and all(_entity(x) for x in parts):
            out += parts
        elif _entity(p) or len(pieces) == 1:
            out.append(p)
        else:
            raise Undecided(f'"{p[:80]}" in the caption\'s "{str(value)[:120]}"')
    return out


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        n = clean(n)
        if _words(n) and not any(same(n, o) for o in out):
            out.append(n)
    return out


def _split(values: list[str]) -> tuple[list[str], list[str]]:
    """(the entities the caption values name, the values code could not split:
    each kept whole, so a request serving several parties stops on it)."""
    out: list[str] = []
    undecided: list[str] = []
    for v in values:
        try:
            out += entities(v)
        except Undecided as exc:
            out.append(clean(v))
            undecided.append(str(exc))
    return out, undecided


def undecided(cap: dict[str, Any]) -> list[str]:
    """Every caption party value code could not split into entities."""
    fields = cap.get("fields") or {}
    vals = [v["value"] for s in ("plaintiffs", "defendants") for v in fields.get(s) or [] if isinstance(v, dict)]
    return _split(vals)[1]


def matter_parties(cap: dict[str, Any], facts: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(the client's side, the other side), as the court's caption prints them;
    the matter record's names when the caption names no one on a side."""
    fields = cap.get("fields") or {}
    rows = [p for p in cap.get("parties") or [] if isinstance(p, dict) and p.get("name")]
    if rows:
        record: dict[str, Any] = {"parties": rows}
    else:
        record = {"plaintiffs": list(facts.get("client_names") or []), "defendants": []}
    side = caption_mod.client_side(fields, record) if (fields.get("plaintiffs") or fields.get("defendants")) else ""
    other = {"plaintiffs": "defendants", "defendants": "plaintiffs"}.get(side, "")
    ours = _split([v["value"] for v in fields.get(side) or [] if isinstance(v, dict)])[0] if side else []
    theirs = _split([v["value"] for v in fields.get(other) or [] if isinstance(v, dict)])[0] if other else []
    ours += [p["name"] for p in rows if p.get("side") == "client"] + list(facts.get("client_names") or [])
    if not theirs:
        theirs = [p["name"] for p in rows if p.get("side") == "other"]
    ours = _dedupe(ours)
    return ours, [t for t in _dedupe(theirs) if not any(same(t, o) for o in ours)]


def _runs(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _named_at(text: str, party: str, theirs: list[str], ours: list[str]) -> int | None:
    """Where ``text`` first names ``party``, or None."""
    toks = [(m.group(0).lower(), m.start(), m.group(0)) for m in re.finditer(r"[A-Za-z0-9]+", text)]
    toks = [t for t in toks if t[0] not in _STOP]
    words = [t[0] for t in toks]
    mine = _words(party)
    others = [_words(o) for o in theirs if o != party]
    ours_w = {w for o in ours for w in _words(o)}
    hits: list[int] = []
    for n in range(len(mine), 1, -1):
        for run in _runs(mine, n):
            if n < len(mine) and any(run in _runs(o, n) for o in others):
                continue  # shared with another party: says nothing alone
            hits += [toks[i][1] for i, r in enumerate(_runs(words, n)) if r == run]
        if hits:
            return min(hits)
    other_w = {w for o in others for w in o}
    for w in mine:
        if w in other_w or w in ours_w or w in _NON_ID or len(w) < 3:
            continue
        hits += [pos for low, pos, orig in toks if low == w and orig[:1].isupper()]
    return min(hits) if hits else None


def _claims(request: str) -> list[str]:
    """Each name the request serves a set on, as written."""
    out: list[str] = []
    for m in _SERVED.finditer(request or ""):
        for chunk in re.split(r",|;|&|\band\b", m.group("who")):
            c = _LEAD.sub("", chunk).strip()
            if c[:1].isupper() and not re.match(rf"(?i)(?:{_SET_WORDS})", c):
                out.append(c)
    return out


#: A line limiting the parties ("to the School District only", "only to the City"): the
#: request's own words or SMD's hand line on a re-submitted job; both live in request_text.
_ONLY = re.compile(
    r"(?i)\bto\s+(?P<a>(?:[^.;\n]|\.(?=\S)|\.(?=\s+only\b))+?)\s+only\b|\bonly\s+(?:to|on|upon)\s+(?P<b>[^.;\n]+)"
)


def _scope(request: str, theirs: list[str], ours: list[str]) -> list[str]:
    """The parties a "to X only" line limits the request to, in its order;
    [] when it carries none. A name in it that is not exactly one of the
    matter's parties stops (``PartyUnknown``)."""
    out: list[str] = []
    for m in _ONLY.finditer(request):
        for chunk in re.split(r",|;|&|\band\b", m.group("a") or m.group("b") or ""):
            c = _LEAD.sub("", chunk).strip()
            if not c[:1].isupper():
                continue
            got = [t for t in theirs if _named_at(c, t, theirs, ours) is not None]
            if len(got) != 1:
                raise PartyUnknown(
                    f'the request limits its sets to "{c[:80]}", which is '
                    + ("none" if not got else "more than one")
                    + f" of the matter's parties ({'; '.join(theirs)})"
                )
            if got[0] not in out:
                out.append(got[0])
    return out


def plan(request: str, theirs: list[str], ours: list[str], unsplit: list[str] | None = None) -> list[str]:
    """The parties the request serves its sets on, in the order it names them;
    [] when it serves one party (the job drafts as it always did).

    A request that serves every defendant, or names two or more parties, is
    never planned as one party: when the matter resolves to fewer, or two of
    its names resolve to the same party, it stops (``PartyUnknown``)."""
    request = request or ""
    claims = [c for c in _claims(request) if not any(_named_at(c, o, ours, []) is not None for o in ours)]
    every = bool(_BOTH.search(request))
    why = f"; the caption could not be split: {'; '.join(unsplit)}" if unsplit else ""
    listed = "; ".join(theirs) or "none"
    if len(theirs) < 2:
        if every or len(claims) >= 2:
            asked = "every defendant" if every else f"{len(claims)} parties ({'; '.join(c[:60] for c in claims)})"
            raise PartyUnknown(
                f"the request serves {asked}, but the matter's other-side parties resolve to {len(theirs)} "
                f"({listed}){why}"
            )
        return []
    resolved: dict[str, str] = {}
    for c in claims:
        got = [t for t in theirs if _named_at(c, t, theirs, ours) is not None]
        if len(got) != 1:
            raise PartyUnknown(
                f'the request serves a set on "{c[:80]}", which is '
                + ("none" if not got else "more than one")
                + f" of the matter's parties ({listed}){why}"
            )
        prior = next((k for k, v in resolved.items() if v == got[0]), None)
        if prior is not None:
            raise PartyUnknown(f'the request names "{prior[:60]}" and "{c[:60]}", which are the same party ({got[0]})')
        resolved[c] = got[0]
    scoped = _scope(request, theirs, ours)
    if scoped:
        return scoped  # "to X only": exactly those parties, even one
    at = {t: _named_at(request, t, theirs, ours) for t in theirs}
    named = sorted((p for p, pos in at.items() if pos is not None), key=lambda p: at[p] or 0)
    if every or (not named and _PLURAL.search(request)):
        named += [t for t in theirs if t not in named]  # every party: those named first, in their order
    return named if len(named) >= 2 else []


def override(job_dir: Path, theirs: list[str]) -> list[str] | None:
    """SMD's hand-named parties (``parties.json``), each one of the matter's, or None."""
    p = Path(job_dir) / OVERRIDE
    if not p.is_file():
        return None
    names = json.loads(p.read_text(encoding="utf-8")).get("parties") or []
    out = []
    for n in names:
        hit = [t for t in theirs if same(n, t)]
        if len(hit) != 1:
            raise PartyUnknown(f'{OVERRIDE} names "{str(n)[:80]}", which is not one of the matter\'s parties')
        out.append(hit[0])
    return out


# ---- the unit's party, written into the set by code -----------------------------------

_RESP = re.compile(
    r"(?im)^(\|?[ \t]*\**[ \t]*RESPONDING PARTY[ \t]*:?[ \t]*\**[ \t]*\|?[ \t]*)([^|\n]*?)([ \t]*\|?[ \t]*)$"
)
_TITLE = re.compile(r"(?i)\b(?:interrogator(?:y|ies)|admissions?|production)\b.*\bto\b|\bto\b.*\bset\b")
PARTY_TERMS = frozenset({"YOU", "YOUR", "RESPONDING PARTY", "DEFENDANT"})


def _swap(text: str, other: str, party: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return party.upper() if m.group(0).isupper() else party

    return re.sub(re.escape(other), sub, text, flags=re.I)


def _names(text: str, party: str) -> bool:
    return f" {' '.join(_words(party))} " in f" {' '.join(_words(text))} "


def bind(doc: str, party: str, theirs: list[str]) -> tuple[str, list[str], list[str]]:
    """``doc`` with ``party`` as its responding party, in its title and in its
    definition of YOU / YOUR. Returns (doc, notes, problems): a problem is a
    definition of YOU that still names no party of this set."""
    notes: list[str] = []
    others = [o for o in theirs if not same(o, party)]
    m = _RESP.search(doc)
    if m and not _names(m.group(2), party):
        value = m.group(2)
        swapped = value
        for o in others:
            swapped = _swap(swapped, o, party)
        new = swapped if _names(swapped, party) else party.upper() if value.isupper() or not value else party
        doc = doc[: m.start(2)] + new + doc[m.end(2) :]
        notes.append(f"the responding party is {party}, as the request serves this set")
    head_end = sets._defs_start(doc)
    head_end = head_end if head_end >= 0 else len(doc)
    out = []
    for ln in doc[:head_end].splitlines(keepends=True):
        if _TITLE.search(ln) and not _names(ln, party):
            new = ln
            for o in others:
                new = _swap(new, o, party)
            if new != ln:
                notes.append(f"the title names {party}")
            ln = new
        out.append(ln)
    doc = "".join(out) + doc[head_end:]
    problems: list[str] = []
    defs = sets.definition_texts(doc)
    for term in sorted((t for t in defs if t in PARTY_TERMS), key=lambda t: -defs[t][0]):
        a, b, text = defs[term]
        new = text
        for o in others:
            new = _swap(new, o, party)
        if new != text:
            doc = doc[:a] + new + doc[b:]
            notes.append(f'"{term}" defined as {party}, the party this set is served on')
        if term in ("YOU", "YOUR") and not _names(new, party) and any(_names(new, o) for o in others):
            problems.append(f'"{term}" is defined as another party, not {party}')
    return doc, notes, problems


def drop_party_terms(front: str) -> str:
    """Another set's front matter without its party's own definitions."""
    keep = []
    for para in re.split(r"\n\s*\n", front):
        m = re.match(r"\s*\d+\.\s+[\"“]([A-Z][A-Z ]*[A-Z])[\"”]", para)
        if m and " ".join(m.group(1).split()) in PARTY_TERMS:
            continue
        keep.append(para)
    return "\n\n".join(keep)


def main(argv: list[str] | None = None) -> int:
    """$0 rehearsal: the parties a job's request would plan, read from its own files."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m jobs.drafting.parties <job_dir>", file=sys.stderr)
        return 2
    jd = Path(args[0])
    job = json.loads((jd / "job.json").read_text(encoding="utf-8"))
    data = jd / "data"
    cap = json.loads((data / "caption.json").read_text(encoding="utf-8")) if (data / "caption.json").is_file() else {}
    facts = json.loads((data / "facts.json").read_text(encoding="utf-8")) if (data / "facts.json").is_file() else {}
    request = str(job.get("request_text") or "")
    ours, theirs = matter_parties(cap, facts)
    print(f"client side: {ours}")
    print(f"other side:  {theirs}")
    try:
        who = override(jd, theirs) or plan(request, theirs, ours, undecided(cap))
    except PartyUnknown as exc:
        print(f"party_unknown: {exc}")
        return 1
    found = sets.variants(request)
    print(f"sets asked:  {[u.label for u in found]}")
    units = [f"{u.label} to {p}" for p in who for u in found] if who and found else [u.label for u in found]
    print(f"units ({len(units)}):")
    for i, u in enumerate(units, 1):
        print(f"  set-{i}: {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
