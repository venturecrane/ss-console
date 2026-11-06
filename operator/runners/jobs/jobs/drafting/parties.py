"""Propounded discovery to more than one party: who each set is served on.

**The incident.** On 2026-11-05 an attorney asked for RFA, RFPD and SROG to
two defendants, a City and a School District. The job planned one unit per SET
TYPE, never per party, so it drafted to one defendant only; SMD submitted a
second job by hand, seeded it from the first, and fixed it by hand again.

**The design: the request names the parties, the caption verifies them.** A
court's caption prints its defendants in one string, segmented any number of
ways ("<District>, City of X, State of CA, and DOES 1 - 100": is "City of X" a
party, or where the District is?). Three rehearsals on the real file showed a
grammar for that string always guesses wrong somewhere, so code never decides
a caption's segmentation on its own. It cuts every caption value into pieces
at commas, semicolons and "and", and takes each piece and each run of up to
three adjacent pieces, as printed, as a CANDIDATE name. A party is a candidate
the attorney's request prints (case-insensitive, whole words; the longest
wins). A single piece also matches when the request's spelling carries up to
two extra words among its words, in order ("Northfield Joint Union High
School District" names the caption's "Northfield Joint Union School
District"); one phrase that could be two caption parties stops. A capitalized
phrase carrying half or more of an unnamed party's words is a near miss and
stops, quoting both spellings: a party is never dropped silently. So the request decides the split: "City of X" named in the request is
its own party even inside the District's value.

* Two or more distinct names in the request: one unit per (named party, set).
* A "to X only" line (the attorney's words, or SMD's hand line on a
  re-submitted job: the envelope's only free text is ``request_text``) narrows
  the plan to exactly those names, even one.
* "all / each / both defendants" with no names uses the caption's parties only
  when every value splits cleanly into whole parties (``entities``: no
  qualifier such as "City of X" or "State of CA" after a name); else it stops.
* Two names in the request that are one caption span, a scope name that is no
  candidate, or a request naming none of several defendants: stop
  (``party_unknown``, SMD alerted). Never a guess.
* One name, or none, with one defendant on the caption: no parties are
  planned, and the job drafts exactly as it always did.

SMD may name the parties by hand in the job directory's ``parties.json``
(``{"parties": [...]}``); each must be a candidate.

``bind`` then writes the unit's party into its identification block, its title
and its definition of YOU / YOUR by code, so a set to one party never carries
another's name where the party served is stated.

Rehearsal at $0 (reads job.json, data/caption.json and data/facts.json; spends
nothing, writes nothing): ``python -m jobs.drafting.parties <job_dir>``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import caption as caption_mod, sets
from .outcome import DraftingFailed

OVERRIDE = "parties.json"

#: Words that never identify a party on their own.
_STOP = frozenset("the of a an and inc llc l c corp corporation co company ltd lp llp pc".split())
#: Every defendant, in so many words.
_EVERY = re.compile(
    r"(?i)\b(?:both|all|each|every)\s+(?:of\s+the\s+)?(?:named\s+)?(?:defendants?|responding\s+parties|parties)\b"
    r"|\b(?:to|on|upon)\s+(?:the\s+)?defendants\b"
)
#: A line limiting the parties ("to the School District only", "only to the City").
_ONLY = re.compile(
    r"(?i)\bto\s+(?P<a>(?:[^.;\n]|\.(?=\S)|\.(?=\s+only\b))+?)\s+only\b|\bonly\s+(?:to|on|upon)\s+(?P<b>[^.;\n]+)"
)
#: "<party> only", the clause before it (SMD's hand line: "SMD: <party> only").
_BARE_ONLY = re.compile(r"(?P<c>[^.;:\n]+?)\s+only\b", re.I)
#: "DOES 1 through 50", "Does 1 - 100", "ROES 1 to 10": fictitious defendants, never a party served.
_FICTITIOUS = re.compile(r"(?i)^(?:and\s+)?(?:does?|roes?)\s+\d+\s*(?:-|–|to|through|thru)\s*\d+\b")
#: What a caption prints after a name, describing it ("a public entity", "inclusive").
_DESCRIBES = re.compile(r"(?i)^(?:an?\s|inclusive\b|et\s+al\b|individually\b|jointly\b|erroneously\b|sued\s+as\b)")
#: A corporate suffix printed after a comma ("Smith and Sons, Inc."): part of the name before it.
_SUFFIX = re.compile(r"(?i)^(?:inc|llc|l\.l\.c|corp|corporation|co|ltd|lp|llp|pc|p\.c|n\.a)\.?$")
#: A jurisdiction a caption may print after an agency's name, or as a party of its own: code cannot tell.
_QUALIFIER = re.compile(r"(?i)^(?:the\s+)?(?:state|county|city|commonwealth|territory)\s+of\s+\S")
#: Words that make one word, or one phrase, a whole entity name.
_ENTITY = frozenset(
    "city county district state department board authority agency commission school college university hospital "
    "inc llc corporation corp company association trust regents trustees bank group partners partnership services "
    "center clinic church police sheriff transit".split()
)
_SEP = re.compile(r"[;,]|\band\b", re.I)
_RUN = 3  # a candidate spans at most this many adjacent pieces
_EXTRA = 2  # words a request's spelling of a name may carry that the caption's lacks
#: Words a near miss never counts ("the City of", "and").
_DETERMINERS = frozenset("the of a an and to for at in on by".split())


class PartyUnknown(ValueError):
    """The request's parties cannot be read from it and the caption with certainty."""


class Undecided(ValueError):
    """A caption value code cannot split into whole parties with confidence."""


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if w not in _STOP]


def _within(inner: list[str], outer: list[str]) -> tuple[int, int] | None:
    """Where ``inner``'s words all appear in order inside ``outer`` with at most
    ``_EXTRA`` extra words among them ("Northfield Joint Union School District"
    inside "Northfield Joint Union High School District"): (first, last) index
    in ``outer``, or None."""
    if not inner:
        return None
    for i, w in enumerate(outer):
        if w != inner[0]:
            continue
        k, extra, j = 1, 0, i
        while k < len(inner) and extra <= _EXTRA:
            j += 1
            if j >= len(outer):
                break
            if outer[j] == inner[k]:
                k += 1
            else:
                extra += 1
        if k == len(inner) and extra <= _EXTRA:
            return i, j
    return None


def same(a: str, b: str) -> bool:
    """One party in two spellings: one's words inside the other's (``_within``)."""
    x, y = _words(a), _words(b)
    return bool(x and y) and (_within(x, y) is not None or _within(y, x) is not None)


def _entity(piece: str) -> bool:
    """A piece that reads as a whole party name on its own."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'.&-]*", piece)
    caps = [w for w in words if w[:1].isupper() or w[:1].isdigit()]
    if not caps or not piece.strip()[:1].isalnum():
        return False
    return len(caps) >= 2 or any(w.lower().strip(".") in _ENTITY for w in words)


def _noise(text: str) -> bool:
    """Not a name: a fictitious defendant, a description or a suffix alone."""
    return bool(_FICTITIOUS.match(text) or _DESCRIBES.match(text) or _SUFFIX.match(text))


# ---- the caption's candidates ------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    value: int  # which caption value
    start: int  # its span in that value
    end: int
    text: str  # as printed
    pieces: int = 1  # adjacent pieces it spans: a run is matched exactly, a piece by ``_within``


def _pieces(value: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for m in [*_SEP.finditer(value), None]:
        end = m.start() if m else len(value)
        seg = value[pos:end]
        if seg.strip():
            lead = len(seg) - len(seg.lstrip())
            spans.append((pos + lead, pos + len(seg.rstrip())))
        pos = m.end() if m else len(value)
    return spans


def candidates(values: list[str]) -> list[Candidate]:
    """Every name the caption values could print: each piece, and each run of
    up to three adjacent pieces, as printed; never a fictitious entry, a
    description or a suffix alone."""
    out: list[Candidate] = []
    for vi, v in enumerate(values):
        sp = _pieces(v)
        for i in range(len(sp)):
            for j in range(i, min(i + _RUN, len(sp))):
                a, b = sp[i][0], sp[j][1]
                text = v[a:b]
                if any(_noise(v[x:y]) for x, y in (sp[i], sp[j])) or not _entity(text):
                    continue
                out.append(Candidate(vi, a, b, text, j - i + 1))
    return out


def _pattern(text: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in text.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.I)


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9']+", text or "")]


def _hits(text: str, c: Candidate) -> list[tuple[int, int, Candidate]]:
    """Where ``text`` names ``c``: a run of pieces word for word, a single piece
    also with up to ``_EXTRA`` extra words in the request's spelling."""
    if c.pieces > 1:
        return [(m.start(), m.end(), c) for m in _pattern(c.text).finditer(text or "")]
    toks, mine, out = _tokens(text), [t for t, _a, _b in _tokens(c.text)], []
    words = [t for t, _a, _b in toks]
    at = 0
    while at < len(words):
        got = _within(mine, words[at:])
        if got is None:
            break
        i, j = at + got[0], at + got[1]
        out.append((toks[i][1], toks[j][2], c))
        at = j + 1
    return out


def _related(a: Candidate, b: Candidate) -> bool:
    return a.value == b.value and a.start < b.end and b.start < a.end


def named_in(text: str, cands: list[Candidate]) -> list[Candidate]:
    """The candidates ``text`` names, longest first where they overlap, in
    the order the text names them; one per span of the text. One phrase of
    the text naming two unrelated candidates stops (``PartyUnknown``)."""
    hits = [h for c in cands for h in _hits(text, c)]
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    taken: list[tuple[int, int, Candidate]] = []
    for h in hits:
        over = [t for t in taken if h[0] < t[1] and t[0] < h[1]]
        if over and not all(_related(h[2], t[2]) for t in over):
            raise PartyUnknown(
                f'the request\'s "{text[h[0] : h[1]][:80]}" could be "{h[2].text}" or "{over[0][2].text}" on the caption'
            )
        if not over:
            taken.append(h)
    out: list[Candidate] = []
    for _s, _e, c in sorted(taken, key=lambda h: h[0]):
        if not any(o.text.lower() == c.text.lower() for o in out):
            out.append(c)
    return out


def near_misses(text: str, cands: list[Candidate], named: list[Candidate]) -> None:
    """``PartyUnknown`` when a phrase of ``text`` outside every name found
    carries half or more of a caption party's distinctive words without naming
    it: a spelling code could not match, never planned around (2026-11-05)."""
    rest = text or ""
    for c in named:
        for a, b, _c in _hits(rest, c):
            rest = rest[:a] + " " * (b - a) + rest[b:]
    # A phrase's capitalized words: a name is capitalized; "the city's records" is not one.
    phrases = [
        {m.group(0).lower() for m in re.finditer(r"\b[A-Z0-9][A-Za-z0-9']*", p)}
        for p in re.split(r"[.;,\n]|\band\b", rest)
    ]
    for c in cands:
        if c.pieces > 1 or _JURISDICTION.match(c.text) or any(_related(c, n) for n in named):
            continue  # a pure jurisdiction ("State of CA") qualifies a party; it is never chased
        mine = set(_words_all(c.text)) - _DETERMINERS
        for ph in phrases:
            shared = sum(w in ph for w in mine)
            if shared >= 2 and 2 * shared > len(mine):
                raise PartyUnknown(
                    f'the request may mean "{c.text}" but does not name it as the caption does (a phrase carries '
                    + ", ".join(sorted(w for w in mine if w in ph))
                    + ")"
                )


#: A caption piece that is a jurisdiction and nothing more ("State of CA", "County of X").
_JURISDICTION = re.compile(r"(?i)^(?:the\s+)?(?:state|county|city|commonwealth|territory)\s+of\s+[A-Za-z.' ]{1,40}$")
#: A sign-off line: what follows is the signature, not the request.
_SIGN_OFF = re.compile(r"(?i)^\s*(?:thanks|thank\s+you|regards|best(?:\s+regards)?|sincerely)\b[\s,.!]*$")
#: A line of only a person's name (one to three capitalized words, no party word).
_NAME_LINE = re.compile(r"^\s*[A-Z][a-z'.-]+(?:\s+[A-Z][a-z'.-]+){0,2}\s*$")
_STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
_STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|"
    "Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|"
    "Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|"
    "Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    "West Virginia|Wisconsin|Wyoming"
)
#: An address line: a ZIP after a state ("CA 95555", "California 95555"), or a street word and a
#: five-digit number on one line. A bare five-digit number ("Case No. 34567") is not an address.
_ZIP = re.compile(r"\b(?:" + "|".join(_STATES) + "|" + _STATE_NAMES + r")\.?,?\s+\d{5}(?:-\d{4})?\b")
_STREET = re.compile(r"(?i)\b(?:st|ave|blvd|rd|suite|ste)\b\.?")
_FIVE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def _address(ln: str) -> bool:
    return bool(_ZIP.search(ln) or (_STREET.search(ln) and _FIVE.search(ln)))


_PHONE = re.compile(r"(?i)\b(?:tel|phone|fax|facsimile)\b|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
#: Words that START a new block after a signature: SMD's hand line, a requester's added words.
_BLOCK = re.compile(r"(?i)^\s*(?:SMD\b|Added by .+ in the same email thread:)")


def request_body(text: str) -> str:
    """The request without its signature or quoted reply: after a sign-off line
    (or a line of only a name) nothing is read until SMD's hand line or the
    requester's added words begin a new block; a quoted ("> ") line, and a line
    carrying a ZIP code or a phone or fax number, is never read. The firm's
    address ("..., CA 95555") is not a party."""
    out, signed = [], False
    for ln in (text or "").splitlines():
        if _BLOCK.match(ln):
            signed = False
        elif _SIGN_OFF.match(ln) or (
            # a name alone signs off only after the request said something ("Christa" opening it does not)
            any(x.strip() for x in out) and _NAME_LINE.match(ln) and not any(w.lower() in _ENTITY for w in ln.split())
        ):
            signed = True
        if signed or ln.lstrip().startswith(">") or _address(ln) or _PHONE.search(ln):
            continue
        out.append(ln)
    return "\n".join(out)


def _words_all(text: str) -> list[str]:
    return [t for t, _a, _b in _tokens(text)]


def _distinct(names: list[Candidate]) -> list[str]:
    """Each name as printed; two names that are one caption span stop."""
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if a.value == b.value and a.start < b.end and b.start < a.end:
                raise PartyUnknown(f'the request names "{a.text}" and "{b.text}", which are one party on the caption')
    return [c.text for c in names]


# ---- a caption that splits cleanly into whole parties --------------------------------------


def entities(value: str) -> list[str]:
    """The parties one caption value names when it splits CLEANLY, each as
    printed: at commas and semicolons (a suffix stays with its name, a
    description and a fictitious entry are dropped), then at "and" only where
    every piece is a whole name. ``Undecided`` when a piece is a jurisdiction
    after a name ("<District>, City of X, State of CA": one party or three?)
    or is no name at all."""
    pieces: list[str] = []
    boundary = True  # the next piece starts a party
    for raw in re.split(r"([;,])", str(value or "")):
        if raw == ";":
            boundary = True
            continue
        p = raw.strip()
        if not p or p == ",":
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
            raise Undecided(f'"{p[:80]}" after "{pieces[-1][:60]}" in the caption\'s "{str(value)[:200]}"')
        pieces.append(p)
        boundary = False
    out: list[str] = []
    for p in pieces:
        parts = [x.strip() for x in re.split(r"(?i)\s+and\s+", p)]
        if len(parts) > 1 and all(_entity(x) for x in parts):
            out += parts
        elif _entity(p) or len(pieces) == 1:
            out.append(p)
        else:
            raise Undecided(f'"{p[:80]}" in the caption\'s "{str(value)[:200]}"')
    return out


# ---- the matter's other side, and the plan ---------------------------------------------------


def other_values(cap: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """The other side's party values as the court's caption prints them (the
    side that is not the client's); the matter record's names when the caption
    names no one there."""
    fields = cap.get("fields") or {}
    rows = [p for p in cap.get("parties") or [] if isinstance(p, dict) and p.get("name")]
    record: dict[str, Any] = (
        {"parties": rows} if rows else {"plaintiffs": list(facts.get("client_names") or []), "defendants": []}
    )
    side = caption_mod.client_side(fields, record) if (fields.get("plaintiffs") or fields.get("defendants")) else ""
    other = {"plaintiffs": "defendants", "defendants": "plaintiffs"}.get(side, "")
    got = [str(v["value"]) for v in fields.get(other) or [] if isinstance(v, dict)] if other else []
    return got or [str(p["name"]) for p in rows if p.get("side") == "other"]


def _whole(values: list[str]) -> list[str]:
    """Every party the values name when each splits cleanly; ``PartyUnknown`` quoting one that does not."""
    out: list[str] = []
    for v in values:
        try:
            out += [e for e in entities(v) if not any(same(e, o) for o in out)]
        except Undecided as exc:
            raise PartyUnknown(f"the request serves every defendant, but the caption cannot be split: {exc}") from None
    return out


def _scope(request: str, cands: list[Candidate]) -> list[Candidate] | None:
    """The names a "to X only" line limits the request to; None when it has none."""
    got: list[Candidate] = []
    lines = [m.group("a") or m.group("b") or "" for m in _ONLY.finditer(request or "")]
    for chunk in lines:
        found = named_in(chunk, cands)
        if not found and re.search(r"\b[A-Z][A-Za-z]", chunk) and not _EVERY.search(f"to {chunk}"):
            raise PartyUnknown(
                f'the request limits its sets to "{chunk.strip()[:80]}", which is no party on the caption'
            )
        got += [c for c in found if c not in got]
    # "<party> only" with no "to": a scope only when it names a caption party.
    for m in _BARE_ONLY.finditer(request or ""):
        got += [c for c in named_in(m.group("c"), cands) if c not in got]
    return got or None


@dataclass(frozen=True)
class Resolved:
    planned: list[str]  # the parties units are planned for ([]: no parties, today's plan)
    known: list[str]  # every party the request and caption name (what ``bind`` swaps away)


def resolve(request: str, values: list[str]) -> Resolved:
    """Who the request serves (module docstring, the rules in order)."""
    request = request_body(request)
    cands = candidates(values)
    found = named_in(request, cands)
    near_misses(request, cands, found)
    named = _distinct(found)
    scoped = _scope(request, cands)
    if scoped is not None:
        return Resolved(_distinct(scoped), named)
    if len(named) >= 2:
        return Resolved(named, named)
    try:
        whole = [e for v in values for e in entities(v)]
        several = len(whole) >= 2
    except Undecided:
        whole, several = [], True
    if _EVERY.search(request or ""):
        if named and not several:
            return Resolved([], named)
        every = _whole(values)
        if len(every) < 2 and re.search(r"(?i)\b(?:both|all|each|every)\b", request or ""):
            raise PartyUnknown(f"the request serves every defendant, but the caption names {len(every)}")
        return Resolved(every if len(every) >= 2 else [], every)
    if named:
        return Resolved(named if several else [], named)
    if several:
        listed = "; ".join(values)[:300]
        raise PartyUnknown(f"the request names none of the caption's defendants ({listed}) by name")
    return Resolved([], whole)


def override(job_dir: Path, values: list[str]) -> list[str] | None:
    """SMD's hand-named parties (``parties.json``), each a caption candidate, or None."""
    p = Path(job_dir) / OVERRIDE
    if not p.is_file():
        return None
    cands = candidates(values)
    out = []
    for n in json.loads(p.read_text(encoding="utf-8")).get("parties") or []:
        hit = [c.text for c in cands if c.text.lower() == str(n).strip().lower()]
        if not hit:
            raise PartyUnknown(f'{OVERRIDE} names "{str(n)[:80]}", which is no party on the caption')
        out.append(hit[0])
    return out


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _job(data: Path, job_dir: Path, request: str) -> Resolved:
    values = other_values(_read(data / "caption.json"), _read(data / "facts.json"))
    got = override(job_dir, values)
    res = resolve(request, values) if got is None else Resolved(got, got)
    return res


def served_on(data: Path, job_dir: Path, request: str) -> list[str]:
    """The parties a job's request serves its sets on (``override``, else
    ``resolve``), from its data directory; ``PartyUnknown`` stops it."""
    return _job(data, job_dir, request).planned


def unit_title(p: dict[str, Any]) -> str:
    """A planned unit's label, with its party when it has one."""
    return f"{p['label']} to {p['party']}" if p.get("party") else p["label"]


def plan_units(found: list[Any], who: list[str]) -> list[dict[str, Any]]:
    """The frozen unit plan: one per (party, set) when parties were planned,
    else one per set; a single set with no party is the root unit ``""``."""
    if who:
        pairs = [(p, u) for p in who for u in found]
        return [
            {"key": f"set-{i}", "variant": u.key, "label": u.label, "party": p} for i, (p, u) in enumerate(pairs, 1)
        ]
    plan = [{"key": f"set-{i}", "variant": u.key, "label": u.label} for i, u in enumerate(found, 1)]
    if len(plan) < 2:
        plan = [{"key": "", "variant": plan[0]["variant"] if plan else None, "label": ""}]
    return plan


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


def bound(doc: str, party: str | None, label: str, data: Path, job_dir: Path, request: str) -> tuple[str, list[str]]:
    """``bind`` for a unit served on ``party`` (none: unchanged), against every
    party the request and caption name; a definition of YOU still naming
    another party fails the job (``party_mismatch``)."""
    if not party:
        return doc, []
    doc, notes, problems = bind(doc, party, _job(data, job_dir, request).known)
    if problems:
        raise DraftingFailed(f"party_mismatch: {label}: " + "; ".join(problems))
    return doc, notes


def keep_terms(canon_party: str | None, party: str | None) -> frozenset[str]:
    """The terms a set keeps when aligned to another: its own YOU when the
    other is served on someone else."""
    return PARTY_TERMS if canon_party != party else frozenset()


def shared_front(front: str, canon_party: str | None, party: str | None) -> tuple[str, str]:
    """Another set's front matter as this set's compose is told it, and the
    sentence saying whom each is served on when they differ."""
    if canon_party == party:
        return front, ""
    said = f' That set is served on {canon_party}; this one is served on {party}, and "YOU" means {party}.'
    return drop_party_terms(front), said


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
    request = str(_read(jd / "job.json").get("request_text") or "")
    data = jd / "data"
    values = other_values(_read(data / "caption.json"), _read(data / "facts.json"))
    print(f"caption, other side: {values}")
    print(f"named in the request: {[c.text for c in named_in(request, candidates(values))]}")
    try:
        res = _job(data, jd, request)
    except PartyUnknown as exc:
        print(f"party_unknown: {exc}")
        return 1
    found = sets.variants(request)
    print(f"sets asked:  {[u.label for u in found]}")
    plan = plan_units(found, res.planned) if found else []
    print(f"units ({len(plan)}):")
    for p in plan:
        print(f"  {p['key'] or '(one document)'}: {unit_title(p) or 'the requested set'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
