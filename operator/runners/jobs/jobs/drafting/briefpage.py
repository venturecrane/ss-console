"""The brief's first page and signature block, as the attorney set them.

The attorney's permanent rules for a mediation brief (A&P, 2026-11-04, 3:28 PM),
three keys under ``format.layout.<class>``:

* ``front_single_spaced``: the attorney caption and everything above the
  brief's body (the court, the case caption, the document's description, the
  date, time and place of the mediation) single spaced; the body stays as the
  layout says. On pleading paper "single" is half the paper's pitch, two text
  lines to each numbered line, every line still exact.
* ``caption_rule_below``: a solid line under the last line of the case caption
  (the caption table's bottom border).
* ``signature_date_beside``: in the signature block, the "Dated:" line sits on
  the line above the attorney's name (the "By:" line), at the left, with the
  firm, the "By:" line and the lines under it set to the right half; never
  on top of the block.

``apply`` sets them on the rendered file (after render, and again after the
pleading paper is laid, which sets every line to the pitch); ``check`` reads
them back (a render defect, never a repair).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Where the right-hand signature column starts.
SIGNATURE_INDENT_IN = 3.25
_DATED = re.compile(r"(?i)^\s*dated\s*:")
_BY = re.compile(r"(?i)^\s*by\s*:")


def opts(fmt: dict[str, Any], cls: str) -> dict[str, bool]:
    lay = (fmt.get("layout") or {}).get(cls) or {}
    return {k: bool(lay.get(k)) for k in ("front_single_spaced", "caption_rule_below", "signature_date_beside")}


def front_paragraphs(doc: Any) -> list[Any]:
    """Every paragraph above the brief's body: before the first heading, with
    the caption table's own paragraphs."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    out: list[Any] = []
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            p = Paragraph(el, doc)
            if p.style is not None and "Heading" in p.style.name:
                return out
            out.append(p)
        elif el.tag.endswith("}tbl"):
            out += [p for c in Table(el, doc)._cells for p in c.paragraphs]
    return out


def _single(doc: Any, pitch_pt: float | None) -> None:
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    for p in front_paragraphs(doc):
        pf = p.paragraph_format
        pf.space_before, pf.space_after = Pt(0), Pt(0)
        if pitch_pt:
            pf.line_spacing, pf.line_spacing_rule = Pt(pitch_pt / 2), WD_LINE_SPACING.EXACTLY
        else:
            pf.line_spacing = 1.0


def _caption_table(doc: Any) -> Any | None:
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}tbl"):
            return el
        if el.tag.endswith("}p") and "Heading" in (el.style or ""):
            return None
    return None


def _rule_below(tbl: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    pr = tbl.tblPr
    borders = pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        pr.append(borders)
    for old in borders.findall(qn("w:bottom")):
        borders.remove(old)
    el = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "8"), ("w:space", "0"), ("w:color", "auto")):
        el.set(qn(k), v)
    borders.append(el)


def _signature(doc: Any) -> bool:
    """Put the last "Dated:" line on the "By:" line under it; True when done."""
    from docx.shared import Inches

    paras = list(doc.paragraphs)
    dated = next((i for i in range(len(paras) - 1, -1, -1) if _DATED.match(paras[i].text)), None)
    if dated is None:
        return False
    by = next((i for i in range(dated + 1, min(dated + 5, len(paras))) if _BY.match(paras[i].text)), None)
    if by is None:
        return False
    from docx.text.run import Run

    d, b = paras[dated], paras[by]
    first = b.runs[0]._r if b.runs else None
    moved = [r._r for r in d.runs]
    for el in moved:
        if first is not None:
            first.addprevious(el)
        else:
            b._p.append(el)
    if moved:
        Run(moved[-1], b).add_tab()  # the "By:" line starts at the tab stop
    b.paragraph_format.left_indent, b.paragraph_format.first_line_indent = Inches(0), Inches(0)
    b.paragraph_format.tab_stops.add_tab_stop(Inches(SIGNATURE_INDENT_IN))
    for p in paras[dated + 1 : by]:
        if p.text.strip():
            p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = (
                Inches(SIGNATURE_INDENT_IN),
                Inches(0),
            )
    for p in paras[by + 1 : by + 4]:
        if p.text.strip() and len(p.text) <= 90 and not (p.style is not None and "Heading" in p.style.name):
            p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = (
                Inches(SIGNATURE_INDENT_IN),
                Inches(0),
            )
    d._p.getparent().remove(d._p)
    return True


def apply(path: Path, fmt: dict[str, Any], cls: str, pitch_pt: float | None = None, signature: bool = True) -> None:
    """Set the authored first page and signature block on the file at ``path``, in place."""
    o = opts(fmt, cls)
    if not any(o.values()):
        return
    import docx

    doc = docx.Document(str(path))
    if o["front_single_spaced"]:
        _single(doc, pitch_pt)
    tbl = _caption_table(doc)
    if o["caption_rule_below"] and tbl is not None:
        _rule_below(tbl)
    if o["signature_date_beside"] and signature:
        _signature(doc)
    doc.save(str(path))


def front_ok(p: Any, pitch_pt: float | None) -> bool:
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    pf = p.paragraph_format
    if (pf.space_before or Pt(0)) != Pt(0) or (pf.space_after or Pt(0)) != Pt(0):
        return False
    if pitch_pt:
        return pf.line_spacing == Pt(pitch_pt / 2) and pf.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    return pf.line_spacing == 1.0


def check(doc: Any, fmt: dict[str, Any], cls: str, pitch_pt: float | None = None) -> list[str]:
    from docx.oxml.ns import qn

    o, out = opts(fmt, cls), []
    if o["front_single_spaced"]:
        bad = [p for p in front_paragraphs(doc) if p.text.strip() and not front_ok(p, pitch_pt)]
        if bad:
            out.append(f"first page: {len(bad)} line(s) above the body not single spaced: {bad[0].text[:30]!r}")
    tbl = _caption_table(doc)
    if o["caption_rule_below"] and tbl is not None:
        b = tbl.tblPr.find(qn("w:tblBorders"))
        bottom = b.find(qn("w:bottom")) if b is not None else None
        if bottom is None or bottom.get(qn("w:val")) != "single":
            out.append("first page: no solid line under the case caption")
    if o["signature_date_beside"]:
        texts = [p.text for p in doc.paragraphs]
        if any(_DATED.match(t) for t in texts) and not any(_DATED.match(t) and "By:" in t for t in texts):
            out.append("signature: the date is not on the line above the attorney's name")
    return out
