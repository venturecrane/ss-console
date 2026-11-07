"""Where a memo base's chart look comes from when the firm's chosen memo has none
(the coordinator's decision on #3876, 2026-11-07).

A memo's chart (``body.chart<n>``) is the only look the draft's tables are laid by
(``memo_base._member``). A firm's best memos often carry no table at all. So the
chart look of a chartless memo comes, in code and by rank, from:

1. ``firm_memo``: the firm's OWN highest-ranked memo that has a readable chart
   (the hunt names them, ``library_setup.job.chart_donors``). Its charts are read
   by the same plan (``memo_base._roles``), copied into the chosen memo with the
   styles they name, and blanked into the same slots, so the made memo's tables
   look like the firm's other memos' tables;
2. ``body_look``: only when the firm has no memo with a readable chart at all, a
   plain table whose every cell is the chosen memo's own body paragraph look, with
   no table style and no borders of its own.

Which one was used is recorded: :func:`source` is what the ledger entry and the
record beside each draft carry (``chart_source``), so the read-back says so.
"""

from __future__ import annotations

import copy
import hashlib
import io
import zipfile
from typing import Any

from lxml import etree

from ..firmbase.base import W

FIRM_MEMO = "firm_memo"
BODY_LOOK = "body_look"
STYLES = "word/styles.xml"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
#: Columns of the body-look proto: any other width grows or narrows from it (``_member``).
BODY_LOOK_COLUMNS = 2


def source(kind: str, blob: bytes | None = None, file_id: str = "") -> dict[str, Any]:
    """The ``chart_source`` record: the donor memo by file id and sha256, or ``body_look``."""
    if kind == FIRM_MEMO and blob is not None:
        return {"from": FIRM_MEMO, "file_id": file_id, "sha256": hashlib.sha256(blob).hexdigest()}
    return {"from": BODY_LOOK, "file_id": "", "sha256": ""}


def _scrub(tbl: Any) -> Any:
    """A donor chart with nothing that points outside its own memo's package: no
    picture, link or object (their relationships are the donor's), no list numbering."""
    for el in list(tbl.iter()):
        if el.tag in (f"{W}drawing", f"{W}pict", f"{W}object") or any(a.startswith(_R) for a in el.attrib):
            parent = el.getparent()
            if parent is not None:
                if el.tag == f"{W}hyperlink":
                    for child in list(el):
                        el.addprevious(child)
                parent.remove(el)
    for num in list(tbl.iter(f"{W}numPr")):
        num.getparent().remove(num)
    return tbl


def donor_charts(donor: bytes) -> dict[int, Any]:
    """The donor memo's charts, one per column count, read by the memo plan; {} when
    it has none the plan can read (or is no memo the plan reads at all)."""
    import docx
    from docx.text.paragraph import Paragraph

    from . import memo_base

    doc = docx.Document(io.BytesIO(donor))
    kids = list(doc.element.body)
    first = next(
        (i for i, e in enumerate(kids) if e.tag == f"{W}p" and memo_base._heading(Paragraph(e, doc), "")), None
    )
    if first is None:
        return {}
    _paras, charts = memo_base._roles(doc, kids[first:-1])
    return {n: _scrub(copy.deepcopy(tbl)) for n, (tbl, _blank) in charts.items()}


def _style_ids(tbls: list[Any]) -> set[str]:
    tags = (f"{W}tblStyle", f"{W}pStyle", f"{W}rStyle")
    return {el.get(f"{W}val") or "" for t in tbls for el in t.iter(*tags)} - {""}


def _styles_root(blob: bytes) -> Any:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return etree.fromstring(z.read(STYLES)) if STYLES in z.namelist() else None


def merged_styles(target: bytes, donor: bytes, tbls: list[Any]) -> bytes | None:
    """The target's styles.xml with each style the donor charts name and the target
    lacks (and the styles they are based on) copied from the donor; None when no
    style is missing."""
    mine, theirs = _styles_root(target), _styles_root(donor)
    if mine is None or theirs is None:
        return None
    have = {s.get(f"{W}styleId") for s in mine.iter(f"{W}style")}
    by_id = {s.get(f"{W}styleId"): s for s in theirs.iter(f"{W}style")}
    want, added = sorted(_style_ids(tbls) - have), False
    while want:
        sid = want.pop()
        if sid in have or sid not in by_id:
            continue
        mine.append(copy.deepcopy(by_id[sid]))
        have.add(sid)
        added = True
        based = by_id[sid].find(f"{W}basedOn")
        if based is not None and based.get(f"{W}val") not in have:
            want.append(based.get(f"{W}val") or "")
    return etree.tostring(mine, xml_declaration=True, encoding="UTF-8", standalone=True) if added else None


def with_styles(pkg: bytes, styles: bytes | None) -> bytes:
    """``pkg`` with its styles part replaced, every other part by its bytes."""
    if styles is None:
        return pkg
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(pkg)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            out.writestr(info, styles if info.filename == STYLES else src.read(info.filename))
    return buf.getvalue()


def _text_width(body: Any) -> int:
    sect = body.find(f"{W}sectPr")
    size = sect.find(f"{W}pgSz") if sect is not None else None
    mar = sect.find(f"{W}pgMar") if sect is not None else None
    if size is None or mar is None:
        return 9360  # US Letter with one-inch margins, in twentieths of a point
    return int(size.get(f"{W}w")) - int(mar.get(f"{W}left") or 0) - int(mar.get(f"{W}right") or 0)


def body_look_chart(body: Any, body_p: Any) -> Any:
    """A plain table of :data:`BODY_LOOK_COLUMNS` columns and three rows (header, a
    row, a total), every cell's paragraph a copy of the memo's own body paragraph:
    no table style, no borders, no shading of its own."""
    n = BODY_LOOK_COLUMNS
    width = _text_width(body) // n
    tbl = etree.Element(f"{W}tbl")
    tpr = etree.SubElement(tbl, f"{W}tblPr")
    etree.SubElement(tpr, f"{W}tblW", {f"{W}w": str(width * n), f"{W}type": "dxa"})
    grid = etree.SubElement(tbl, f"{W}tblGrid")
    for _ in range(n):
        etree.SubElement(grid, f"{W}gridCol", {f"{W}w": str(width)})
    for _ in range(3):
        tr = etree.SubElement(tbl, f"{W}tr")
        for _ in range(n):
            tc = etree.SubElement(tr, f"{W}tc")
            etree.SubElement(etree.SubElement(tc, f"{W}tcPr"), f"{W}tcW", {f"{W}w": str(width), f"{W}type": "dxa"})
            tc.append(copy.deepcopy(body_p))
    return tbl


__all__ = [
    "BODY_LOOK",
    "BODY_LOOK_COLUMNS",
    "FIRM_MEMO",
    "body_look_chart",
    "donor_charts",
    "merged_styles",
    "source",
    "with_styles",
]
