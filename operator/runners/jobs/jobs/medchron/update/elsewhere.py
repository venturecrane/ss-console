"""A class 2 "omitted content" finding whose content is already composed in
another entry is closed by code, as a note for the attorney. $0, no model.

WHY (2026-11-17). An update held on 8 must-fix findings after its second
review round, and a person who read all 8 against the pages found the
reviewer wrong on every one. Each was class 2 (omitted content) and the
content it asked for was already composed: in a delivered entry citing
another exhibit copy of the same visit, in another added entry citing a
different filed copy of it, or in a different added entry than the one the
reviewer anchored the finding to. The finisher can only edit or decline, so
the job held for a hand finish, a code change and a release. This module is
that person's check, in code.

A finding closes only when ONE entry is found that covers the cited page:

* PAGE IDENTITY: the page identity the update decided before vision
  (``update/page_identity.json``, read by ``pageid_io.load``) says the cited
  page is a copy of a page another entry cites (a delivered page, or a page
  of another new document, either way round); or
* SAME CONTENT: an added entry, or a delivered entry on the same visit date
  and provider as the finding's entry (``body.score``, the lane's provider
  scoring), whose text ``content_agrees`` with the cited page's text;

and that entry holds every clinical value (``values.clinical_values``: labs,
vitals, doses; page, fax and record-number digits carry none) and every
diagnosis or finding term (``values.terms``) the cited page carries, and
every number and word the finding's own point (``page_says``, else the
problem) names that the page carries. One missing value or term keeps the
finding OPEN, so a real omission still holds for SMD. A cited page with no
text to read closes only by an EXACT picture match, and then the point's
every word and number must be in the covering entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit.page_text import content_agrees
from . import body as B, pageid as PI, pageid_io as PIO, review_repair as RR
from .review_parts import delivered_cites
from .select import ALREADY, OVERLAP
from .values import clinical_values, content_numbers, terms, words

_KLASS = re.compile(r"^\s*(\d+)")
#: (exhibit, page) of a page, in an exhibit number either delivered or added.
Page = tuple[int, int]


@dataclass(frozen=True)
class Twin:
    """A page the cited page is a copy of, and whether the pictures are pixel-equal."""

    page: Page
    exact: bool


@dataclass(frozen=True)
class Cover:
    """Where the finding's content already is."""

    arm: str  # "page identity" | "same content"
    anchor: str  # N<k> or D<k>
    where: str  # "the entry for 08/27/2026, Sample Hospital"


def is_omission(f: RR.Finding) -> bool:
    m = _KLASS.match(f.klass or "")
    return (m.group(1) == "2") if m else "omitted" in (f.klass or "").lower()


def where(entry_text: str) -> str:
    """The entry in the attorney's words: its date and provider."""
    lines = entry_text.splitlines()
    date = lines[0].strip() if lines else ""
    prov = B.provider_of(entry_text)
    return f"the entry for {date}, {prov}" if prov else f"the entry for {date}"


def note(f: RR.Finding, c: Cover) -> str:
    return f"Exhibit {f.exhibit} p. {f.page} is already covered in {c.where}."


def twins(ud: Path, exhibits: dict[str, int]) -> dict[Page, list[Twin]]:
    """{page: the pages the update's page identity says it is a copy of, and
    the pages that are copies of it}. `exhibits`: new document id -> its exhibit."""
    out: dict[Page, list[Twin]] = {}
    for doc, rows in PIO.load(ud).pages.items():
        ex = exhibits.get(doc)
        if ex is None:
            continue
        for p, hit in rows.items():
            if hit.reason not in (ALREADY, OVERLAP):
                continue
            other = PIO.delivered_exhibit(hit.other) if hit.other.startswith(PI.DELIVERED) else exhibits.get(hit.other)
            if other is None:
                continue
            out.setdefault((ex, p), []).append(Twin((other, hit.page), hit.exact))
            out.setdefault((other, hit.page), []).append(Twin((ex, p), hit.exact))
    return out


def _text(evidence: RR.Evidence, page: Page) -> str | None:
    t = evidence(*page)
    return None if t is None or not t.strip() or t.strip().startswith(PI.SKIPPED[:16]) else t


def missing(page_text: str | None, entry_text: str, point: str) -> list[str]:
    """What the cited page (or, read against it, the finding's point) carries
    that the entry lacks. Empty: the entry holds it all."""
    have_v, have_w, have_n = clinical_values(entry_text), words(entry_text), content_numbers(entry_text)
    if page_text is None:  # nothing to read the page by: the point itself must be held, every word of it
        return sorted(
            {f"word {w}" for w in words(point) - have_w} | {f"value {n}" for n in content_numbers(point) - have_n}
        )
    out = {f"value {v}" for v in clinical_values(page_text) - have_v}
    out |= {f"term {t}" for t in terms(page_text) - have_w}
    out |= {f"word {w}" for w in (words(point) & words(page_text)) - have_w}
    out |= {f"value {n}" for n in (content_numbers(point) & content_numbers(page_text)) - have_n}
    return sorted(out)


@dataclass
class Scene:
    """What the rule reads: the added entries of the document as it is, the
    delivered entries, page text, and the page identity."""

    added: list[RR.Entry]
    delivered: list[str]
    evidence: RR.Evidence
    twins: dict[Page, list[Twin]]

    def cited_by(self, page: Page) -> list[tuple[str, str]]:
        """(anchor, text) of every entry citing `page`, added first."""
        out = [(e.anchor, e.text) for e in self.added if page in e.cites()]
        out += [(f"D{i}", t) for i, t in enumerate(self.delivered, 1) if page in delivered_cites(t)]
        return out


def _same_visit(entry: str | None, delivered: str) -> bool:
    if entry is None or B.iso(entry) != B.iso(delivered) or not B.iso(entry):
        return False
    a, b = B.provider_of(entry).lower(), B.provider_of(delivered).lower()
    return (bool(a) and a == b) or B.score(entry, delivered) >= 0.5


def _agrees(entry: str, page_text: str) -> bool:
    """``content_agrees`` on the entry's body, its date and provider lines and
    its citations aside (they are the chronology's words, not the page's)."""
    return content_agrees(B.CITE.sub(" ", "\n".join(entry.splitlines()[2:])), page_text)


def candidates(f: RR.Finding, s: Scene) -> list[tuple[str, str, str, str | None]]:
    """(arm, anchor, entry text, the page text it is checked against), in order."""
    page: Page = (int(f.exhibit or 0), int(f.page or 0))
    text = _text(s.evidence, page)
    out: list[tuple[str, str, str, str | None]] = []
    for tw in s.twins.get(page, []):
        t = text or _text(s.evidence, tw.page)
        if t is None and not tw.exact:
            continue  # a near copy with no text to read: nothing settles what it says
        out += [("page identity", a, et, t) for a, et in s.cited_by(tw.page)]
    if text is not None:
        mine = next((e.text for e in s.added if e.anchor == f.anchor.split(".")[0]), None)
        out += [("same content", e.anchor, e.text, text) for e in s.added if _agrees(e.text, text)]
        out += [
            ("same content", f"D{i}", d, text)
            for i, d in enumerate(s.delivered, 1)
            if _same_visit(mine, d) and _agrees(d, text)
        ]
    return out


def cover(f: RR.Finding, s: Scene) -> tuple[Cover | None, str]:
    """(where the content is, '') or (None, why the finding stays open)."""
    if not is_omission(f) or not f.exhibit or not f.page:
        return None, "not an omission naming its exhibit and page"
    point = f.page_says or f.problem
    seen = candidates(f, s)
    if not seen:
        return None, "no other entry cites a copy of the page or reads like it"
    why: list[str] = []
    for arm, anchor, text, page_text in seen:
        lack = missing(page_text, text, point)
        if not lack:
            return Cover(arm, anchor, where(text)), ""
        why.append(f"{anchor} lacks {', '.join(lack[:6])}")
    return None, "; ".join(dict.fromkeys(why))


def scene(added: list[RR.Entry], delivered: list[str], evidence: RR.Evidence, ud: Path, rows: list[Any]) -> Scene:
    """`rows`: ``new_exhibits.final.json``, each new document's id and exhibit."""
    exhibits = {
        str(r["id"]): int(r["exhibit"])
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("exhibit"), int) and isinstance(r.get("id"), str)
    }
    return Scene(added, delivered, evidence, twins(ud, exhibits))


__all__ = ["Cover", "Scene", "cover", "is_omission", "missing", "note", "scene", "twins", "where"]
