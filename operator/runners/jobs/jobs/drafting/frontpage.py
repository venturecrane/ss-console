"""The first page's authored gaps: attorney block to party, court to caption.

The attorney's instruction (A&P, 2026-11-04): on the first page, leave extra
space between the attorney information and the party represented ("Attorneys
for Plaintiff"), and between the court's name and the caption. Two keys under
``format.layout.<class>``, each a count of blank lines:

* ``front_attorney_gap_lines``: before the "Attorney(s) for ..." line;
* ``front_court_gap_lines``: between the party represented (the lines under
  "Attorneys for") and the court's name (Chris, 5:03 PM);
* ``front_caption_gap_lines``: between the court's name and the caption table.

Blank LINES, not paragraph spacing: on pleading paper every line sits on the
paper's pitch with no space before or after (``pleading.check``), so a gap is
a number of the paper's own lines. ``space`` inserts them after render;
``check`` counts them on the rendered file (a render defect, never a repair).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_FOR = re.compile(r"(?i)^\s*attorneys?\s+for\b")
_COURT = re.compile(r"(?i)\bcourt\b|\bcounty\b")


def gaps(fmt: dict[str, Any], cls: str) -> tuple[int, int, int]:
    """(before "Attorneys for", before the court's name, court to caption), in blank lines."""
    lay = (fmt.get("layout") or {}).get(cls) or {}
    return (
        int(lay.get("front_attorney_gap_lines") or 0),
        int(lay.get("front_court_gap_lines") or 0),
        int(lay.get("front_caption_gap_lines") or 0),
    )


def _court_at(paras: list[Any]) -> int | None:
    """The first line of the court's name: a bold court or county line after "Attorneys for"."""
    start = next((i for i, p in enumerate(paras) if _FOR.match(p.text)), 0)
    for i in range(start, len(paras)):
        runs = [r for r in paras[i].runs if r.text.strip()]
        if runs and all(r.bold for r in runs) and _COURT.search(paras[i].text):
            return i
    return None


def _front(doc: Any) -> tuple[list[Any], Any | None]:
    """The paragraphs before the caption table, and the table's element."""
    from docx.text.paragraph import Paragraph

    paras = []
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}tbl"):
            return paras, el
        if el.tag.endswith("}p"):
            p = Paragraph(el, doc)
            if p.style is not None and "Heading" in p.style.name:
                return paras, None
            paras.append(p)
    return paras, None


def _blank(anchor_el: Any) -> None:
    from docx.oxml import OxmlElement

    anchor_el.addprevious(OxmlElement("w:p"))


def space(path: Path, fmt: dict[str, Any], cls: str) -> None:
    """Insert the authored blank lines in the rendered file, in place."""
    attorney, court, caption = gaps(fmt, cls)
    if not (attorney or court or caption):
        return
    import docx

    doc = docx.Document(str(path))
    paras, table = _front(doc)
    party = next((p for p in paras if _FOR.match(p.text)), None)
    if party is not None:
        for _ in range(attorney):
            _blank(party._p)
    at = _court_at(paras)
    if at is not None:
        for _ in range(court):
            _blank(paras[at]._p)
    if table is not None and any(p.text.strip() and _COURT.search(p.text) for p in paras):
        for _ in range(caption):
            _blank(table)
    doc.save(str(path))


def _blanks_before(paras: list[Any], i: int) -> int:
    n = 0
    while i - 1 - n >= 0 and not paras[i - 1 - n].text.strip():
        n += 1
    return n


def check(doc: Any, fmt: dict[str, Any], cls: str) -> list[str]:
    attorney, court, caption = gaps(fmt, cls)
    if not (attorney or court or caption):
        return []
    paras, table = _front(doc)
    out = []
    c = _court_at(paras)
    if court and c is not None and _blanks_before(paras, c) != court:
        out.append(f"first page: not {court} blank line(s) before the court's name")
    at = next((i for i, p in enumerate(paras) if _FOR.match(p.text)), None)
    if attorney and at is not None and _blanks_before(paras, at) != attorney:
        out.append(f"first page: not {attorney} blank line(s) before {paras[at].text.strip()[:30]!r}")
    if caption and table is not None and _blanks_before(paras, len(paras)) != caption:
        out.append(f"first page: not {caption} blank line(s) between the court's name and the caption")
    return out
