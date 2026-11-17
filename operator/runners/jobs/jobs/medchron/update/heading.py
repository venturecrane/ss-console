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
    #: a finisher-written heading that failed and went back to the audited one
    reverted: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "where": self.where,
            "page": list(self.page) if self.page else None,
            "weaker": self.weaker,
            "missing": self.missing,
            "reverted": self.reverted,
        }


def _date(e: str) -> date | None:
    d = B.iso(e)
    try:
        return date(int(d[:4]), int(d[5:7]), int(d[8:10])) if d else None
    except ValueError:
        return None


_WORD = re.compile(r"[a-z]{2,}")


def _words(text: str) -> list[str]:
    """Lowercase words, punctuation aside ("O'Roe" and "ORoe" are one word,
    a hyphen parts two)."""
    return _WORD.findall((text or "").lower().replace("'", "").replace("\u2019", ""))


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


def name_rule(provider: str) -> NameRule:
    """The rule a page meets for the heading's provider.

    A CLINICIAN (2026-11-17: 10 of 17 false failures on a real update were the
    name printed in another form, "J. Roe, MD" for "Roe, Jane (M.D.)"): the
    surname alone, whatever the given name's form, initials, order,
    credentials or middle name; a hyphenated surname by every part, or the
    parts as one word. The surname is the part before the comma of
    "Surname, Given", else the last word of the name.

    A FACILITY or LABORATORY (a heading whose provider has no person, 6 of
    the 17): its distinctive words, the generic ones (``GENERIC``, facility
    and specialty words) aside, at least half of them, so a city or a word
    the page leaves out never fails it; with no distinctive word, half of
    all its words."""
    from math import ceil

    from ...stages.group import CRED_SEG, FACILITY_WORD
    from . import finisher as FN
    from .encounter import _SPECIALTY

    def plain(part: str) -> list[str]:
        return [w for w in _words(part) if w not in FN._CRED and not CRED_SEG.match(w)]

    def facility(part: str) -> bool:
        return bool(FACILITY_WORD.search(part)) or any(w in GENERIC or _SPECIALTY.match(w) for w in plain(part))

    bare = re.sub(r"\([^)]*\)", " ", provider or "")
    parts = [p.strip() for p in re.split(r",|;|/|\|| [-\u2013] ", bare) if plain(p)]
    people = [p for p in parts if not facility(p) and len(plain(p)) <= 4]
    if people:
        if len(people) >= 2 and people[0] == parts[0]:
            surname = people[0]  # "Surname, Given"
        else:
            surname = people[-1].split()[-1]  # "Given Middle Surname", or the person after a facility
        toks = tuple(plain(surname))
        return NameRule(toks, len(toks))
    every = plain(bare)
    own = tuple(dict.fromkeys(w for w in every if w not in GENERIC and not _SPECIALTY.match(w)))
    own = tuple(w for w in own if not FACILITY_WORD.search(w))
    toks = own or tuple(dict.fromkeys(every))
    return NameRule(toks, max(1, ceil(len(toks) / 2)))


def name_words(provider: str) -> list[str]:
    """The words of ``name_rule`` (what the report and the tests read)."""
    return list(name_rule(provider).tokens)


def _says(text: str, d: date | None, rule: NameRule) -> list[str]:
    """What of the heading `text` does not print: "date", "name"."""
    from ...stages.review import _date_forms

    text = _BRACKET.sub("", text or "")
    words = set(_words(text))
    miss = []
    if d is None or not re.search(_date_forms(d), text):
        miss.append("date")
    if not rule.met(words):
        miss.append("name")
    return miss


def _layer(t: str | None) -> bool:
    from .select import FAILED_MARKS

    return bool(t and t.strip() and t.strip() not in FAILED_MARKS)


def check(entry: str, pages: Pages) -> Check:
    """Where the entry's date and provider are printed."""
    d, names = _date(entry), name_rule(B.provider_of(entry))
    cited = cited_pages(entry)
    texts = [(ex, p, pages.text(ex, p)) for ex, p in cited]
    have = [(ex, p, t) for ex, p, t in texts if _layer(t)]
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
    "name_words",
]
