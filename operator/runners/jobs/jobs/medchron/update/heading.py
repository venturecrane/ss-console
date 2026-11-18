"""Every added entry's heading is printed on the record. $0, no model.

WHY (2026-11-17). A heading is never a claim (``audit/claims``: the date and
provider lines are the entry's header), so the audit never reads one, and a
repair that wrote AFTER the audit once invented a provider name and a date
that reached the firm. Here, for each ADDED entry (a fold carries its
delivered entry's heading and is not checked):

* the date (every numeric form, ``stages/review._date_forms``) and every name
  word of the provider (``providers._words``, credentials aside, as
  ``finisher.check_provider`` reads a name) are printed on ONE of the
  entry's own cited pages (CITED);
* else on the heading of the note one of those pages continues
  (``noteheading.heading_block``: the nearest note heading on the pages
  before it that were not composed) (SAME_NOTE);
* else the heading FAILED. There is no fallback to a canonical name: a
  heading no page states is a recorded failure, never an inferred pass.

A page read by transcription (the job's own paid transcript, nothing is read
again) is a weaker reading than a text layer; such a check is marked
``weaker: transcript``.

RECORD-ONLY. The one action is ``final_set``'s: a heading the finisher
edited that fails here goes back to the heading the audit document carried.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from . import body as B
from .encounter import cited_pages

CITED, SAME_NOTE, FAILED = "on a cited page", "on a page of the same note", "failed"
#: A cited page the name could be on has no text layer to read: never a failure.
NO_TEXT = "on a cited page with no text layer"
WEAKER, NO_LAYER = "transcript", "no text layer"
_BRACKET = re.compile(r"(?m)^\[.*\]\s*$")
#: (exhibit, page) -> the page's text, or None.
Text = Callable[[int, int], str | None]


@dataclass(frozen=True)
class Pages:
    """What the heading check reads, per new exhibit."""

    text: Text
    #: exhibit -> every page's text ({page: text})
    pages_of: Callable[[int], dict[int, str]] = lambda _ex: {}
    #: exhibit -> the pages of it that were composed (read)
    read: Callable[[int], set[int]] = lambda _ex: set()
    #: (exhibit, page) -> the page was read by transcription
    transcribed: Callable[[int, int], bool] = lambda _ex, _p: False


@dataclass
class Check:
    where: str
    page: tuple[int, int] | None = None
    weaker: str = ""
    missing: list[str] = field(default_factory=list)  # "date", "name", "text"

    def as_dict(self) -> dict[str, object]:
        return {
            "where": self.where,
            "page": list(self.page) if self.page else None,
            "weaker": self.weaker,
            "missing": self.missing,
        }


def _date(e: str) -> date | None:
    d = B.iso(e)
    try:
        return date(int(d[:4]), int(d[5:7]), int(d[8:10])) if d else None
    except ValueError:
        return None


_WORD = re.compile(r"[a-z]{2,}")


def _words(text: str) -> list[str]:
    """Lowercase words, punctuation and accents aside ("O'Roe" and "ORoe"
    are one word, "Muñoz" is "munoz", a hyphen parts two)."""
    import unicodedata

    flat = unicodedata.normalize("NFKD", (text or "").lower())
    flat = "".join(ch for ch in flat if not unicodedata.combining(ch))
    return _WORD.findall(flat.replace("'", "").replace("\u2019", ""))


#: Words after a name that are no part of the surname.
_SUFFIX = frozenset({"jr", "sr", "ii", "iii", "iv"})
#: Credentials as written once their dots and hyphens are gone ("Ph.D." is
#: "phd", "PA-C" is "pac"): never a name word.
_CREDS = frozenset(
    "md do dc pt np pa pac lcsw aud rn ma dpm od phd msn bsn lvn lpn cna aprn fnp fnpc crna dnp rd msw lmft otr "
    "otrl dpt mph lac ot facs faaos".split()
)


def _tokens(part: str) -> list[str]:
    """A name part's whitespace tokens less credentials and suffixes, each
    read as written ("M.D.", "Ph.D.", "Jr." are gone, "Roe-Smith" stays)."""
    out = []
    for t in part.split():
        key = "".join(_words(t.replace(".", "").replace("-", "")))
        if key and key not in _CREDS and key not in _SUFFIX:
            out.append(t)
    return out


#: Words a facility or laboratory heading shares with every other one (the
#: generic part of its name); ``stages.group.FACILITY_WORD`` names the rest.
GENERIC = frozenset(
    "laboratory laboratories lab labs medical center centre clinic clinics department dept hospital hospitals "
    "health healthcare services service of the and inc llc pc group associates regional community system "
    "network care institute".split()
)


@dataclass(frozen=True)
class NameRule:
    """What a page must print for a heading's provider: `need` of `tokens`,
    in any order, or the tokens written as one word (a hyphenated surname
    printed "RoeSmith")."""

    tokens: tuple[str, ...]
    need: int

    def met(self, words: set[str]) -> bool:
        if not self.tokens:
            return False
        return "".join(self.tokens) in words or sum(t in words for t in self.tokens) >= self.need


def _is_cred(token: str) -> bool:
    key = "".join(_words(token.replace(".", "").replace("-", "")))
    return bool(key) and key in _CREDS


def _facility_rule(text: str) -> NameRule:
    """At least half of `text`'s distinctive words (generic, facility and
    specialty words aside), or of all its words when it has none."""
    from math import ceil

    from ...stages.group import FACILITY_WORD
    from .encounter import _SPECIALTY

    every = [w for t in _tokens(text) for w in _words(t)]
    own = tuple(dict.fromkeys(w for w in every if w not in GENERIC and not _SPECIALTY.match(w)))
    own = tuple(w for w in own if not FACILITY_WORD.search(w))
    toks = own or tuple(dict.fromkeys(every))
    return NameRule(toks, max(1, ceil(len(toks) / 2)))


def _person(parts: list[tuple[str, bool]], facility: Callable[[str], bool]) -> tuple[NameRule, set[int]] | None:
    """(the surname rule, the parts that make the person), or None. `parts`:
    (name part without its parentheses, whether it carries a credential).

    With a credential, the PERSON is the part that carries it, or the part
    just before a part that is only a credential; its surname is its last
    word, unless it is a given name alone after a name part ("Roe, Jane
    (M.D.)"), when the part before it is the surname. With no credential,
    "Surname, Given" applies only to exactly two parts neither of which is a
    facility; else the person is the last part that is no facility."""

    def words(part: str) -> list[str]:
        return [w for t in _tokens(part) for w in _words(t)]

    def surname(i: int) -> NameRule:
        toks = tuple(_words(_tokens(parts[i][0])[-1]))
        return NameRule(toks, len(toks))

    named = [i for i, (p, _c) in enumerate(parts) if words(p)]
    cred = next((i for i, (_p, c) in enumerate(parts) if c), None)
    if cred is not None:
        at = cred if words(parts[cred][0]) else max((i for i in named if i < cred), default=None)
        if at is None:
            return None
        before = max((i for i in named if i < at), default=None)
        if len(words(parts[at][0])) == 1 and before is not None and not facility(parts[before][0]):
            toks = tuple(words(parts[before][0]))
            return NameRule(toks, len(toks)), {at, before}
        return surname(at), {at}
    if len(named) == 2 and not any(facility(parts[i][0]) for i in named):
        toks = tuple(words(parts[named[0]][0]))
        return NameRule(toks, len(toks)), set(named)
    people = [i for i in named if not facility(parts[i][0]) and len(words(parts[i][0])) <= 4]
    return (surname(people[-1]), {people[-1]}) if people else None


def name_rules(provider: str) -> tuple[NameRule, ...]:
    """Every rule that ties the heading's provider to a page; ONE met is
    enough (2026-11-17: a firm's heading names the facility and the person,
    "Facility Name Specialty, Given Middle Surname, M.D.", and a cited page
    may print either one).

    The PERSON (``_person``) is matched on the surname alone, whatever the
    given name's form, initials, order, credentials, hyphenation or middle
    name; a hyphenated surname by every part or as one word. Every other
    part is matched as a facility (``_facility_rule``). A heading with no
    person is one facility or laboratory, matched on at least half of its
    distinctive words, so a city or a word the page leaves out never fails
    it. On a real update the earlier tests gave 12, 5 and 1 false failures;
    each shape is a fixture."""
    from ...stages.group import FACILITY_WORD
    from .encounter import _SPECIALTY

    def facility(part: str) -> bool:
        ws = [w for t in _tokens(part) for w in _words(t)]
        return bool(FACILITY_WORD.search(part)) or any(w in GENERIC or _SPECIALTY.match(w) for w in ws)

    raw = [p.strip() for p in re.split(r",|;|/|\|| [-\u2013] ", provider or "")]
    parts = []
    for p in raw:
        inner = " ".join(re.findall(r"\(([^)]*)\)", p))
        bare = re.sub(r"\([^)]*\)", " ", p).strip()
        cred = any(_is_cred(t) for t in (bare + " " + inner).split())
        if bare or cred:
            parts.append((bare, cred))
    found = _person(parts, facility)
    if found is None:
        return (_facility_rule(" ".join(p for p, _c in parts)),)
    person, used = found
    others = [_facility_rule(p) for i, (p, _c) in enumerate(parts) if i not in used and _tokens(p)]
    return (person, *(r for r in others if r.tokens))


def name_rule(provider: str) -> NameRule:
    """The heading's first rule: its person when it has one, else its facility."""
    return name_rules(provider)[0]


def name_words(provider: str) -> list[str]:
    """The words of ``name_rule`` (what the report and the tests read)."""
    return list(name_rule(provider).tokens)


def _says(text: str, d: date | None, rules: tuple[NameRule, ...]) -> list[str]:
    """What of the heading `text` does not print: "date", "name"."""
    from ...stages.review import _date_forms

    text = _BRACKET.sub("", text or "")
    words = set(_words(text))
    miss = []
    if d is None or not re.search(_date_forms(d), text):
        miss.append("date")
    if not any(r.met(words) for r in rules):
        miss.append("name")
    return miss


def _layer(t: str | None) -> bool:
    from .select import FAILED_MARKS

    return bool(t and t.strip() and t.strip() not in FAILED_MARKS)


def check(entry: str, pages: Pages) -> Check:
    """Where the entry's date and provider are printed."""
    d, names = _date(entry), name_rules(B.provider_of(entry))
    cited = cited_pages(entry)
    texts = [(ex, p, pages.text(ex, p)) for ex, p in cited]
    have = [(ex, p, t) for ex, p, t in texts if t is not None and _layer(t)]
    blind = [(ex, p) for ex, p, t in texts if not _layer(t)]
    if not have:
        return Check(NO_TEXT, blind[0] if blind else None, NO_LAYER, ["text"])
    best: list[str] = ["date", "name"]
    for ex, p, t in have:
        miss = _says(t, d, names)
        if not miss:
            return Check(CITED, (ex, p), WEAKER if pages.transcribed(ex, p) else "")
        best = miss if len(miss) < len(best) else best
    from .noteheading import heading_block

    for ex, p, _t in have:
        block = heading_block(pages.pages_of(ex), p, lambda q, ex=ex: q in pages.read(ex))
        if block and not _says(block, d, names):
            back = re.search(r"p\.(\d+):", block)
            at = (ex, int(back.group(1))) if back else (ex, p)
            return Check(SAME_NOTE, at, WEAKER if pages.transcribed(*at) else "")
    if blind and best == ["name"]:  # the date is printed; the name may be on the page no text layer carries
        return Check(NO_TEXT, blind[0], NO_LAYER, best)
    weak = WEAKER if any(pages.transcribed(ex, p) for ex, p, _t in have) else ""
    return Check(FAILED, None, weak, best)


__all__ = [
    "CITED",
    "FAILED",
    "GENERIC",
    "NO_LAYER",
    "NO_TEXT",
    "SAME_NOTE",
    "WEAKER",
    "Check",
    "NameRule",
    "Pages",
    "check",
    "name_rule",
    "name_rules",
    "name_words",
]
