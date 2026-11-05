"""What the filed brief already is: its body paragraphs by role, and the value
each formatting key has in it.

``value``: a formatting key's value in the filed file, so an ask is compared
against what the brief already has: an ask already met changes nothing, and a
relative ask ("more space") must go beyond the filed value.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from . import charts, layout as lay

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def body_paragraphs(doc: Any) -> list[Any]:
    """The brief's body: what follows the first heading, not a heading or list."""
    paras = list(doc.paragraphs)
    first = next((i for i, p in enumerate(paras) if lay.heading_level(p)), len(paras))
    return [p for p in paras[first:] if p.text.strip() and not lay.heading_level(p) and not lay.is_list(p)]


# ---- the value a key has in the filed file -------------------------------------------
def _mode(vals: list[Any]) -> Any:
    vals = [v for v in vals if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def _spacing(paras: list[Any], exact: bool) -> float | None:
    from docx.enum.text import WD_LINE_SPACING

    out = []
    for p in paras:
        rule, line = lay.eff(p, "line_spacing_rule"), lay.eff(p, "line_spacing")
        if line is None:
            continue
        is_exact = rule in (WD_LINE_SPACING.EXACTLY, WD_LINE_SPACING.AT_LEAST)
        if exact and is_exact:
            out.append(round(line.pt, 2))
        elif not exact and not is_exact and isinstance(line, float):
            out.append(round(line, 2))
    return _mode(out)


def _space(paras: list[Any], attr: str) -> float | None:
    return _mode([round((lay.eff(p, attr) or 0) / 12700, 2) for p in paras]) if paras else None


def value(path: Path, key: str) -> float | None:
    """``key``'s value in the filed file, or None when it has none to read."""
    import docx

    doc = docx.Document(str(path))
    heads = [p for p in doc.paragraphs if p.text.strip() and lay.heading_level(p)]
    body = body_paragraphs(doc)
    _cap, tables = charts.split(doc)
    readers = {
        "line_spacing_pt": lambda: _spacing(body, True),
        "line_spacing": lambda: _spacing(body, False),
        "heading_line_spacing_pt": lambda: _spacing(heads, True),
        "heading_line_spacing": lambda: _spacing(heads, False),
        "heading_space_before_pt": lambda: _space(heads, "space_before"),
        "heading_space_after_pt": lambda: _space(heads, "space_after"),
        "table_font_pt": lambda: _mode(
            [r.font.size.pt for t in tables for c in t._cells for p in c.paragraphs for r in p.runs if r.font.size]
        ),
        "table_border_pt": lambda: _mode([_box_pt(t) for t in tables]),
    }
    if key in readers:
        return readers[key]()
    if key.endswith("_gap_lines"):
        return float(lay.front_options(doc).get(key) or 0)
    return None


def _box_pt(t: Any) -> float | None:
    pr = t._tbl.tblPr
    b = pr.find(f"{_W}tblBorders") if pr is not None else None
    top = b.find(f"{_W}top") if b is not None else None
    sz = top.get(f"{_W}sz") if top is not None else None
    return int(sz) / 8 if sz is not None and sz.isdigit() else None
