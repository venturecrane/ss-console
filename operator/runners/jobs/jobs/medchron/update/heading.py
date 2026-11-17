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
WEAKER = "transcript"
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


def name_words(provider: str) -> list[str]:
    from . import finisher as FN, providers as P

    return [w.lower() for w in P._words(provider) if w.lower().strip(".") not in FN._CRED]


def _says(text: str, d: date | None, names: list[str]) -> list[str]:
    """What of the heading `text` does not print: "date", "name"."""
    from ...stages.review import _date_forms

    text = _BRACKET.sub("", text or "")
    words = set(re.findall(r"[a-z][a-z'-]+", text.lower()))
    miss = []
    if d is None or not re.search(_date_forms(d), text):
        miss.append("date")
    if not names or any(w not in words for w in names):
        miss.append("name")
    return miss


def check(entry: str, pages: Pages) -> Check:
    """Where the entry's date and provider are printed."""
    d, names = _date(entry), name_words(B.provider_of(entry))
    cited = cited_pages(entry)
    texts = [(ex, p, pages.text(ex, p)) for ex, p in cited]
    have = [(ex, p, t) for ex, p, t in texts if t]
    if not have:
        return Check(FAILED, None, "", ["text"])
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
    weak = WEAKER if any(pages.transcribed(ex, p) for ex, p, _t in have) else ""
    return Check(FAILED, None, weak, best)


__all__ = ["CITED", "FAILED", "SAME_NOTE", "WEAKER", "Check", "Pages", "check", "name_words"]
