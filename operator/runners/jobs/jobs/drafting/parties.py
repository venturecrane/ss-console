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

A request served on ONE party (or a matter with one other-side party) plans no
parties at all, so it drafts exactly as before. SMD may name the parties by
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
_ALL = re.compile(
    r"(?i)\b(?:both|all|each|every)\s+(?:of\s+the\s+)?(?:named\s+)?(?:defendants?|responding\s+parties|parties)\b"
    r"|\b(?:to|on|upon)\s+(?:the\s+)?defendants\b"
)
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


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        n = clean(n)
        if _words(n) and not any(same(n, o) for o in out):
            out.append(n)
    return out


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
    ours = [v["value"] for v in fields.get(side) or [] if isinstance(v, dict)] if side else []
    theirs = [v["value"] for v in fields.get(other) or [] if isinstance(v, dict)] if other else []
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


def plan(request: str, theirs: list[str], ours: list[str]) -> list[str]:
    """The parties the request serves its sets on, in the order it names them;
    [] when it serves one party (the job drafts as it always did)."""
    if len(theirs) < 2:
        return []
    for c in _claims(request):
        if any(_named_at(c, o, ours, []) is not None for o in ours):
            continue  # the client, named as the one who serves
        got = [t for t in theirs if _named_at(c, t, theirs, ours) is not None]
        if len(got) != 1:
            raise PartyUnknown(
                f'the request serves a set on "{c[:80]}", which is '
                + ("none" if not got else "more than one")
                + " of the matter's parties ("
                + "; ".join(theirs)
                + ")"
            )
    at = {t: _named_at(request or "", t, theirs, ours) for t in theirs}
    named = sorted((p for p, pos in at.items() if pos is not None), key=lambda p: at[p] or 0)
    if not named and _ALL.search(request or ""):
        named = list(theirs)
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
        who = override(jd, theirs) or plan(request, theirs, ours)
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
