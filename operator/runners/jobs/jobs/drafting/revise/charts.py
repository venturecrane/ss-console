"""A brief's tables: the caption apart from the charts, a chart's whole look
as comparable data, and schema-ordered writes.

A chart's look is more than its fill: the box and inner rules, the column
widths (the firm's 2:1, not equal), the cell margins, each cell's fill and
edges, its paragraphs' alignment (an amount column right-aligned) and
keep-with-next, and its text colour (white on a dark header). ``look`` reads
all of it. The CAPTION table (the first table, above the first heading) is
not a chart (``split``).

``put`` writes an element in its schema's order (CT_TblPr, CT_TcPr, CT_RPr):
Word refuses, or silently repairs, a ``tblBorders`` placed after ``tblLook``.
"""

from __future__ import annotations

import copy
from typing import Any

from .. import briefpage

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TBLPR = (
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
    "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption",
    "tblDescription", "tblPrChange",
)  # fmt: skip
TCPR = (
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar", "textDirection",
    "tcFitText", "vAlign", "hideMark", "headers", "cellIns", "cellDel", "cellMerge", "tcPrChange",
)  # fmt: skip
RPR = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow",
    "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern",
    "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em",
    "lang", "eastAsianLayout", "specVanish", "oMath",
)  # fmt: skip
#: tblPr children carried whole: the box and rules, width, layout, margins.
_TBL_CARRIED = ("tblW", "tblLayout", "tblCellMar")
_TC_CARRIED = ("tcW", "tcMar")


def put(parent: Any, tag: str, src: Any, order: tuple[str, ...]) -> None:
    """``src`` (a copy) as ``parent``'s ``tag`` child, in schema order; None removes it."""
    for old in parent.findall(f"{_W}{tag}"):
        parent.remove(old)
    if src is None:
        return
    later = {f"{_W}{t}" for t in order[order.index(tag) + 1 :]}
    el = copy.deepcopy(src)
    for child in parent:
        if child.tag in later:
            child.addprevious(el)
            return
    parent.append(el)


def _sig(el: Any) -> Any:
    """An element as comparable data: its tag, attributes and children."""
    if el is None:
        return None
    return (el.tag.rsplit("}", 1)[-1], tuple(sorted(el.attrib.items())), tuple(_sig(c) for c in el))


def _child(parent: Any, tag: str) -> Any:
    return parent.find(f"{_W}{tag}") if parent is not None else None


def _run_colour(r: Any) -> Any:
    return _sig(_child(r._r.rPr, "color"))


def _cell(c: Any) -> tuple[Any, ...]:
    pr = c._tc.tcPr
    paras = c.paragraphs
    return (
        tuple(_sig(_child(pr, t)) for t in ("shd", "tcBorders", *_TC_CARRIED)),
        tuple(str(p.paragraph_format.alignment) for p in paras),
        tuple(bool(p.paragraph_format.keep_with_next) for p in paras),
        tuple(_run_colour(r) for p in paras for r in p.runs if r.text.strip()),
    )


def look(t: Any) -> tuple[Any, ...]:
    """Everything a reader sees of the table's look, as comparable data."""
    pr = t._tbl.tblPr
    grid = _child(t._tbl, "tblGrid")
    return (
        tuple(_sig(_child(pr, tag)) for tag in ("tblBorders", *_TBL_CARRIED)),
        _sig(grid),
        tuple(tuple(_cell(c) for c in row.cells) for row in t.rows),
    )


def split(doc: Any) -> tuple[Any | None, list[Any]]:
    """(the caption table, the charts) of a document."""
    cap = briefpage._caption_table(doc)
    charts = [t for t in doc.tables if cap is None or t._tbl is not cap]
    caption = next((t for t in doc.tables if cap is not None and t._tbl is cap), None)
    return caption, charts
