"""The brief's first page and signature block, as the attorney set them.

The attorney's permanent rules for a mediation brief (A&P, 2026-11-04, 3:28 PM
and 5:03 PM), keys under ``format.layout.<class>``:

* ``front_single_spaced``: the attorney caption and everything above the
  brief's body (the court, the case caption, the document's description, the
  date, time and place of the mediation) single spaced (line 240 auto, no
  space before or after), on plain or pleading paper; the body stays as the
  layout says.
* ``caption_rule_below``: a solid line under the last line of the case
  caption. ``true`` rules the caption table's whole width; ``"left"`` rules
  only the last row's left cell, ending at the caption's vertical line (Chris,
  5:03 PM: "the solid line should only extend to the vertical line").
* ``signature_date_beside``: in the signature block, the "Dated:" line sits on
  the line above the attorney's name (the "By:" line), at the left, with the
  firm, the "By:" line and the lines under it set to the right half; never on
  top of the block.
* ``signature_single_spaced``: the "Dated: ... By:" line and the attorney's
  name lines under it single spaced (the firm's line and the blank signature
  lines above stay on the body's spacing), as he amended the brief himself.
* ``signature_gap_lines``: the blank lines between the firm's name and the
  "Dated: ... By:" line (room to sign).

``apply`` sets them on the rendered file (after render, and again after the
pleading paper is laid, which sets every line to the pitch: ``move=False``
then, the block is already laid); ``check`` reads them back (a render defect,
never a repair); ``off_pitch`` names the lines pleading paper's pitch rule
leaves to this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Where the right-hand signature column starts.
SIGNATURE_INDENT_IN = 3.25
#: How many lines under the "By:" line are the attorney's name lines.
NAME_LINES = 3
_DATED = re.compile(r"(?i)^\s*dated\s*:")
_BY = re.compile(r"(?i)^\s*by\s*:")
_KEYS = (
    "front_single_spaced",
    "caption_rule_below",
    "signature_date_beside",
    "signature_single_spaced",
    "signature_gap_lines",
)


def opts(fmt: dict[str, Any], cls: str) -> dict[str, Any]:
    lay = (fmt.get("layout") or {}).get(cls) or {}
    return {k: lay.get(k) or False for k in _KEYS}


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


def _single(paras: list[Any]) -> None:
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    for p in paras:
        pf = p.paragraph_format
        pf.space_before, pf.space_after = Pt(0), Pt(0)
        pf.line_spacing, pf.line_spacing_rule = 1.0, WD_LINE_SPACING.SINGLE


def _caption_table(doc: Any) -> Any | None:
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}tbl"):
            return el
        if el.tag.endswith("}p") and "Heading" in (el.style or ""):
            return None
    return None


def _border(parent: Any, tag: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for old in parent.findall(qn(tag)):
        parent.remove(old)
    el = OxmlElement(tag)
    for k, v in (("w:val", "single"), ("w:sz", "8"), ("w:space", "0"), ("w:color", "auto")):
        el.set(qn(k), v)
    parent.append(el)


def _last_left_cell(tbl: Any) -> Any:
    from docx.oxml.ns import qn

    return tbl.findall(qn("w:tr"))[-1].findall(qn("w:tc"))[0]


def _rule_below(tbl: Any, span: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    pr = tbl.tblPr
    borders = pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        pr.append(borders)
    if span != "left":
        _border(borders, "w:bottom")
        return
    for old in borders.findall(qn("w:bottom")):
        borders.remove(old)
    tc_pr = _last_left_cell(tbl).get_or_add_tcPr()
    tcb = tc_pr.find(qn("w:tcBorders"))
    if tcb is None:
        tcb = OxmlElement("w:tcBorders")
        tc_pr.append(tcb)
    _border(tcb, "w:bottom")


def _block(doc: Any) -> tuple[list[Any], int, int] | None:
    """(paragraphs, the "Dated:" line's index or -1 when it is already on the
    "By:" line, the "By:" line's index) for the last signature block."""
    paras = list(doc.paragraphs)
    by = next((i for i in range(len(paras) - 1, -1, -1) if _BY.match(paras[i].text) or "\tBy:" in paras[i].text), None)
    if by is None:
        return None
    if _DATED.match(paras[by].text):
        return paras, -1, by
    dated = next((i for i in range(by - 1, max(-1, by - 6), -1) if _DATED.match(paras[i].text)), None)
    return (paras, dated, by) if dated is not None else None


def _name_lines(paras: list[Any], by: int) -> list[Any]:
    out = []
    for p in paras[by + 1 :]:
        if not p.text.strip() or len(p.text) > 90 or (p.style is not None and "Heading" in p.style.name):
            break
        out.append(p)
        if len(out) == NAME_LINES:
            break
    return out


def _indent(p: Any) -> None:
    from docx.shared import Inches

    p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = Inches(SIGNATURE_INDENT_IN), Inches(0)


def _move_date(paras: list[Any], dated: int, by: int) -> None:
    from docx.shared import Inches
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
            _indent(p)
    for p in _name_lines(paras, by):
        _indent(p)
    d._p.getparent().remove(d._p)


def _gap(paras: list[Any], by: int, n: int) -> None:
    """Exactly ``n`` blank lines between the firm's line and the "By:" line."""
    from docx.oxml import OxmlElement

    i = by - 1
    while i >= 0 and not paras[i].text.strip():
        paras[i]._p.getparent().remove(paras[i]._p)
        i -= 1
    for _ in range(n):
        paras[by]._p.addprevious(OxmlElement("w:p"))


def signature_lines(doc: Any) -> list[Any]:
    """The "Dated: ... By:" line and the attorney's name lines under it."""
    blk = _block(doc)
    if blk is None or blk[1] != -1:
        return []
    paras, _d, by = blk
    return [paras[by], *_name_lines(paras, by)]


def off_pitch(doc: Any, fmt: dict[str, Any], cls: str) -> list[Any]:
    """The lines this module sets off the pleading paper's pitch."""
    o = opts(fmt, cls)
    return (front_paragraphs(doc) if o["front_single_spaced"] else []) + (
        signature_lines(doc) if o["signature_single_spaced"] else []
    )


def apply(path: Path, fmt: dict[str, Any], cls: str, pitch_pt: float | None = None, move: bool = True) -> None:
    """Set the authored first page and signature block on the file at ``path``, in place."""
    o = opts(fmt, cls)
    if not any(o.values()):
        return
    import docx

    doc = docx.Document(str(path))
    if o["front_single_spaced"]:
        _single(front_paragraphs(doc))
    tbl = _caption_table(doc)
    if o["caption_rule_below"] and tbl is not None:
        _rule_below(tbl, o["caption_rule_below"])
    blk = _block(doc) if move else None
    if blk is not None and o["signature_date_beside"] and blk[1] >= 0:
        _move_date(*blk)
        blk = _block(doc)
    if blk is not None and o["signature_gap_lines"] and blk[1] == -1:
        _gap(blk[0], blk[2], int(o["signature_gap_lines"]))
    if o["signature_single_spaced"]:
        _single(signature_lines(doc))
    doc.save(str(path))


def single_ok(p: Any) -> bool:
    from docx.shared import Pt

    pf = p.paragraph_format
    if (pf.space_before or Pt(0)) != Pt(0) or (pf.space_after or Pt(0)) != Pt(0):
        return False
    return pf.line_spacing in (None, 1.0)


def _check_rule(tbl: Any, span: Any) -> list[str]:
    from docx.oxml.ns import qn

    b = tbl.tblPr.find(qn("w:tblBorders"))
    whole = b.find(qn("w:bottom")) if b is not None else None
    if span != "left":
        return (
            []
            if whole is not None and whole.get(qn("w:val")) == "single"
            else ["first page: no solid line under the case caption"]
        )
    cells = tbl.findall(qn("w:tr"))[-1].findall(qn("w:tc"))

    def bottom(tc: Any) -> Any:
        pr = tc.find(qn("w:tcPr"))
        tcb = pr.find(qn("w:tcBorders")) if pr is not None else None
        return tcb.find(qn("w:bottom")) if tcb is not None else None

    left = bottom(cells[0])
    if whole is not None or any(bottom(c) is not None for c in cells[1:]):
        return ["first page: the line under the caption runs past its vertical line"]
    if left is None or left.get(qn("w:val")) != "single":
        return ["first page: no solid line under the caption's left cell"]
    return []


def check(doc: Any, fmt: dict[str, Any], cls: str, pitch_pt: float | None = None) -> list[str]:
    o, out = opts(fmt, cls), []
    if o["front_single_spaced"]:
        bad = [p for p in front_paragraphs(doc) if p.text.strip() and not single_ok(p)]
        if bad:
            out.append(f"first page: {len(bad)} line(s) above the body not single spaced: {bad[0].text[:30]!r}")
    tbl = _caption_table(doc)
    if o["caption_rule_below"] and tbl is not None:
        out += _check_rule(tbl, o["caption_rule_below"])
    texts = [p.text for p in doc.paragraphs]
    if o["signature_date_beside"]:
        if any(_DATED.match(t) for t in texts) and not any(_DATED.match(t) and "By:" in t for t in texts):
            out.append("signature: the date is not on the line above the attorney's name")
    blk = _block(doc)
    if o["signature_gap_lines"] and blk is not None and blk[1] == -1:
        paras, _d, by = blk
        n = 0
        while by - 1 - n >= 0 and not paras[by - 1 - n].text.strip():
            n += 1
        if n != int(o["signature_gap_lines"]):
            out.append(f"signature: {n} blank line(s) above the Dated/By line, not {o['signature_gap_lines']}")
    if o["signature_single_spaced"]:
        lines = signature_lines(doc)
        if not lines or not all(single_ok(p) for p in lines):
            out.append("signature: the Dated/By line and the name lines are not single spaced")
    return out
