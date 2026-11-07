"""A case memo's first draft in the firm's own Word file (``firmbase``), its second user.

A firm's memo has no caption and no signature block to hold, and its headings are
often its own design, so its slot plan is not the brief's (``brief_base``):

* the front, everything above the first heading: its first line the title, its other
  bold lines the notice, the rest the memo's lines (to, from, date, re), a label the
  firm sets bold staying bold, a table of them poured column by column;
* the body as a region to the end of the file: one paragraph of each role, one chart
  per column count. A heading the firm lays as two lines, the numeral then the title,
  stays two lines;
* THE CHART IS OPTIONAL (2026-11-07). A chart is the look the draft's tables are laid
  by (``body.chart<n>``, one per column count); a memo with no table at all is a base
  with no chart, and a draft table on it stops the job (``firm_base_unfit``), because
  the plan has no table look but the firm's and invents none. A memo whose tables the
  plan cannot read as a chart is refused, as before;
* a picture goes, whatever it showed, and the words in the headers, footers and notes
  go, because those name the matter the memo was written on (``adopt.clean_package``).

The firm's memo has its own larger headings, so the format check reads this file's
font from the file: one family throughout, the body at the authored size
(``font_fails``); the attorney's rule is 12 point Times New Roman, and a base that
breaks it is refused there.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from typing import Any

from lxml import etree
from smokeball_connector import docgrammar as g

from .. import firmbase
from ..firmbase import FirmBase, Rows, xmlproof
from ..firmbase.base import W, is_blank
from ..firmbase.markers import marked
from .brief_base import _all_bold, _broken, _caption, _chart_slots, _flags, _plain, _read_back, _to_slot, _words, unfit
from .brief_base import checked_format as _brief_checked_format
from .revise import layout as lay

CLS = "memo"
_NUMERAL_ONLY = re.compile(r"^\s*[IVXLC]+\.\s*$")
_ROMAN = re.compile(r"^[IVXLC]+\.\s")
_LETTER = re.compile(r"^([A-Z])\.\s")
_NUMBER = re.compile(r"^\d+\.\s")
_NUMERAL_SPLIT = re.compile(r"^([IVXLC]+\.)\s+(.*)$", re.S)
_TYPED_BULLET = re.compile(r"^\s*[•·▪◦‣-]\s*")
BULLET = "•"
LABEL_MAX = 20
_EMPHASIS = {f"{W}{t}" for t in ("b", "bCs", "i", "iCs")}


# ---- adoption ---------------------------------------------------------------------------
def _heading(p: Any, last_letter: str) -> int:
    """A heading by role, or by direct formatting: a bold line opening with its numeral.
    "C." is a roman numeral or the letter after "B.": the letters so far decide."""
    level = lay.heading_level(p)
    if level:
        return level
    text = p.text.strip()
    if not text or len(text) > 120 or not _flags(p, "bold") or _flags(p, "italic"):
        return 0
    letter = _LETTER.match(text)
    if letter and last_letter and ord(letter.group(1)) == ord(last_letter) + 1:
        return 2
    if _NUMERAL_ONLY.match(text) or _ROMAN.match(text):
        return 1
    return 2 if letter else 3 if _NUMBER.match(text) else 0


def _roles(doc: Any, zone: list[Any]) -> tuple[list[tuple[str, Any]], dict[int, tuple[Any, Any]]]:
    """(each paragraph with its role, one (chart, blank under it) per column count).
    A heading's numeral on its own line makes the next bold line its title."""
    from docx.text.paragraph import Paragraph

    paras: list[tuple[str, Any]] = []
    charts: dict[int, tuple[Any, Any]] = {}
    last_letter, title_next = "", False
    for i, el in enumerate(zone):
        if el.tag == f"{W}tbl":
            grid = el.find(f"{W}tblGrid")
            n = len(grid.findall(f"{W}gridCol")) if grid is not None else 0
            if n and n not in charts and len(el.findall(f"{W}tr")) >= 2:
                charts[n] = (el, zone[i + 1] if i + 1 < len(zone) and is_blank(zone[i + 1]) else None)
            continue
        if el.tag != f"{W}p" or is_blank(el) or el.find(f"{W}pPr/{W}sectPr") is not None:
            continue
        p = Paragraph(el, doc)
        level = _heading(p, last_letter)
        if title_next and not level and _flags(p, "bold"):
            paras.append(("h1t", el))
        elif level:
            paras.append((f"h{level}", el))
            letter = _LETTER.match(p.text.strip())
            last_letter = letter.group(1) if level == 2 and letter else "" if level == 1 else last_letter
        elif el.find(f"{W}pPr/{W}numPr") is not None or (p.style is not None and p.style.name.startswith("List")):
            paras.append(("item", el))
        elif not _TYPED_BULLET.match(p.text):
            paras.append(("p", el))
        title_next = level == 1 and bool(_NUMERAL_ONLY.match(p.text))
    return paras, charts


def _look(el: Any) -> bytes:
    """The paragraph's layout and its first run's look, emphasis aside."""
    ppr = el.find(f"{W}pPr")
    run = next((r for r in el.iter(f"{W}r") if r.find(f"{W}t") is not None), None)
    rpr = run.find(f"{W}rPr") if run is not None else None
    parts = [xmlproof.canon(ppr) if ppr is not None else b""]
    if rpr is not None:
        parts += [xmlproof.canon(c) for c in rpr if c.tag not in _EMPHASIS]
    return b"\0".join(parts)


def _library(paras: list[tuple[str, Any]]) -> tuple[list[Any], list[str]]:
    """The paragraph each role is laid by most often, marked; a note per role the file
    lays more than one way."""
    keep, notes = [], []
    roles: dict[str, list[Any]] = {}
    for role, el in paras:
        roles.setdefault(role, []).append(el)
    for role, members in roles.items():
        counts = Counter(_look(e) for e in members)
        modal, count = counts.most_common(1)[0]
        if len(counts) > 1:
            notes.append(f"{role}: {len(counts)} ways, kept the commonest ({count} of {len(members)})")
        el = next(e for e in members if _look(e) == modal)
        _to_slot(el, f"body.{role}")
        keep.append(el)
    return keep, notes


def _front(doc: Any, front: list[Any]) -> None:
    from docx.text.paragraph import Paragraph

    tables = [e for e in front if e.tag == f"{W}tbl"]
    if len(tables) > 1:
        raise unfit("the firm's memo has more than one table above its first heading")
    titled = False
    for el in front:
        if el.tag == f"{W}tbl":
            _caption(el)
            continue
        if el.tag != f"{W}p":
            continue
        if is_blank(el):
            for run in [r for r in el.findall(f"{W}r") if r.find(f"{W}drawing") is not None]:
                el.remove(run)
            continue
        p = Paragraph(el, doc)
        _to_slot(el, "front.notice" if titled and _flags(p, "bold") else "front.meta" if titled else "front.title")
        titled = True
    if not titled:
        raise unfit("the firm's memo has no title above its first heading")


def adopt(blob: bytes, source: firmbase.Source) -> tuple[bytes, list[str]]:
    """(the firm's memo as a base, notes). A file with Operator lineage is refused
    unless ``source`` records the attorney's acceptance of exactly these bytes."""
    import docx
    from docx.text.paragraph import Paragraph

    if not firmbase.may_adopt(blob, source):
        raise unfit("the file is the Operator's own and no accepted save is recorded for it")
    doc = docx.Document(io.BytesIO(blob))
    body = doc.element.body
    kids = list(body)
    first = next((i for i, e in enumerate(kids) if e.tag == f"{W}p" and _heading(Paragraph(e, doc), "")), None)
    if first is None or kids[-1].tag != f"{W}sectPr":
        raise unfit("the firm's memo has no heading to read its roles from")
    zone = kids[first:-1]
    paras, charts = _roles(doc, zone)
    keep, notes = _library(paras)
    if not charts and any(el.tag == f"{W}tbl" for el in zone):
        # A table the plan cannot read as a chart: refused, as every chartless memo
        # was before 2026-11-07. A memo with no table at all is a base with no chart.
        raise unfit("the firm's memo has a table and no chart to take a chart's look from")
    if "body.p" not in {f"body.{r}" for r, _ in paras}:
        raise unfit("the firm's memo has no plain paragraph")
    _front(doc, kids[:first])
    for n, (tbl, _blank) in charts.items():
        _chart_slots(tbl, n)
    placed = [x for t, sp in charts.values() for x in (t, sp) if x is not None]
    for el in zone:
        if el not in {*keep, *placed}:
            body.remove(el)
    # The charts first, so the region's order never depends on where the firm's stood.
    for el in placed:
        keep[0].addprevious(el)
    _require_blank(body)
    data = etree.tostring(doc.element, xml_declaration=True, encoding="UTF-8", standalone=True)
    return firmbase.clean_package(blob, data, stories=True), notes


def _require_blank(body: Any) -> None:
    for t in body.iter(f"{W}t"):
        if (t.text or "").strip() and not re.fullmatch(r"\[\[[a-z0-9.]+\]\]", t.text or ""):
            raise unfit("a word of the firm's memo survived the blanking")
    if next(body.iter(f"{W}drawing", f"{W}pict", f"{W}instrText"), None) is not None:
        raise unfit("a picture or a field of the firm's memo survived the blanking")


# ---- the draft --------------------------------------------------------------------------
def _meta_line(row: tuple[tuple[g.Run, ...], ...]) -> tuple[g.Run, ...]:
    """A row of the draft's memo table as one line: "TO: A Person"."""
    out: list[g.Run] = []
    for cell in row:
        for line in _broken(cell):
            out += [*([g.Run(" ")] if out else []), *line]
    return tuple(out)


def _bold_label(line: tuple[g.Run, ...]) -> tuple[g.Run, ...]:
    """The firm's "TO:" bold: its first run's label, up to the colon, takes the emphasis."""
    head = line[0]
    at = head.text.find(":") + 1
    if head.marker or not at or at > LABEL_MAX:
        return line
    return (g.Run(head.text[:at], True, head.italic), g.Run(head.text[at:], head.bold, head.italic), *line[1:])


def _labels_bold(base: FirmBase) -> bool:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(base.bytes)).read("word/document.xml"))
    return any(p.find(f"{W}r/{W}rPr/{W}b") is not None for p in root.iter(f"{W}p") if marked(p) == ["front.meta"])


def split_front(blocks: list[g.Block]) -> tuple[dict[str, list[Any]], g.Table | None, list[g.Block]]:
    """(the front's title, notice and meta lines; its table; the body) of a parsed draft."""
    first = next((i for i, b in enumerate(blocks) if isinstance(b, g.Heading)), None)
    if first is None:
        raise unfit("the draft has no heading")
    lines: dict[str, list[Any]] = {"title": [], "notice": [], "meta": []}
    table = None
    for b in blocks[:first]:
        if isinstance(b, g.Table):
            if table is not None:
                raise unfit("the draft's front holds two tables")
            table = b
        elif isinstance(b, g.Paragraph):
            key = "meta" if not _all_bold(b.runs) else "notice" if lines["title"] else "title"
            lines[key].append(b.runs)
        elif not isinstance(b, g.HRule):
            raise unfit("the draft's front holds something other than lines")
    if not lines["title"]:
        raise unfit("the draft has no title above its first heading")
    return lines, table, blocks[first:]


def _member(base: FirmBase, block: g.Table) -> tuple[str, Rows]:
    """The chart proto of this many columns, else the widest narrower one to grow from,
    else the narrowest wider one to take columns from."""
    n = max(len(r) for r in block.rows)
    have = {int(s[10:]): s for s in base.slots if s.startswith("body.chart")}
    if not have:
        # The plan has no table look but the firm's chart; none is invented.
        raise unfit("the draft has a table and the firm's memo has no chart to lay it by")
    below = [k for k in have if k <= n]
    k = n if n in have else max(below) if below else min(have)
    return have[k], Rows(block.rows, block.header, _total, narrow=k > n)


def _total(row: Any) -> bool:
    from smokeball_connector.docx_classes import is_total_row

    return is_total_row([_plain(row[0])])


def _heading_items(base: FirmBase, b: g.Heading) -> list[tuple[str, Any]]:
    have = [lv for lv in (b.level, b.level - 1, b.level - 2) if f"body.h{lv}" in base.slots]
    if not have:
        raise unfit(f"a level {b.level} heading and no heading the base can lay it by")
    split = _NUMERAL_SPLIT.match(_plain(b.runs))
    if have[0] == 1 and "body.h1t" in base.slots and split:
        return [("body.h1", (g.Run(split.group(1)),)), ("body.h1t", (g.Run(split.group(2)),))]
    return [(f"body.h{have[0]}", b.runs)]


def _items(base: FirmBase, body: list[g.Block]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for b in body:
        if isinstance(b, g.Table):
            items.append(_member(base, b))
        elif isinstance(b, g.Heading):
            items += _heading_items(base, b)
        elif isinstance(b, g.Paragraph):
            items.append(("body.p", b.runs))
        elif isinstance(b, g.Bullet):
            has_item = "body.item" in base.slots
            items.append(("body.item", b.runs) if has_item else ("body.p", (g.Run(f"{BULLET} "), *b.runs)))
        elif isinstance(b, g.Numbered):
            items.append(("body.p", (g.Run(f"{b.label} "), *b.runs)))
    return items


def _draft_words(blocks: list[g.Block]) -> Counter[str]:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, g.Table):
            parts += [" ".join(_plain(x) for x in _broken(c)) for row in b.rows for c in row]
        elif isinstance(b, g.Numbered):
            parts += [b.label, _plain(b.runs)]
        elif not isinstance(b, g.HRule):
            parts.append(_plain(b.runs))
    return _words(" ".join(parts))


def _put_front(f: Any, base: FirmBase, lines: dict[str, list[Any]], table: g.Table | None) -> None:
    cols = sorted(int(s.split(".")[1]) for s in base.slots if s.startswith("cap."))
    meta, notice = list(lines["meta"]), list(lines["notice"])
    if table is not None and cols and len(table.rows[0]) <= len(cols):
        for c in cols:
            f.put(f"cap.{c}", [x for r in table.rows if c < len(r) for x in _broken(r[c])], spare="blank")
    elif table is not None:
        meta += [line for line in map(_meta_line, table.rows) if line]
    if "front.notice" not in base.slots:
        meta, notice = [*meta, *notice], []
    f.put("front.title", lines["title"], carry=True)
    if "front.notice" in base.slots:
        if not notice:
            raise unfit("the draft has no notice line and the base has one")
        f.put("front.notice", notice, carry=True)
    if "front.meta" in base.slots:
        if not meta:
            raise unfit("the draft has no memo lines and the base has them")
        f.put("front.meta", [_bold_label(x) for x in meta] if _labels_bold(base) else meta)
    elif meta:
        raise unfit("the draft has memo lines and the base has no place for them")


def fill(base: FirmBase, md: str) -> tuple[bytes, firmbase.Proof]:
    """The draft in a copy of the base, and the proof."""
    blocks = g.parse_document(md)
    lines, table, body = split_front(blocks)
    f = firmbase.open(base)
    _put_front(f, base, lines, table)
    f.put("body", _items(base, body))
    blob, proof = f.save()
    read = _read_back(blob)
    want = _draft_words(blocks)
    read.pop(BULLET, None)
    want.pop(BULLET, None)
    if read != want:
        proof.leaves_outside_allow.append("the file does not read as the draft")
    return blob, proof


# ---- the format check -------------------------------------------------------------------
def _defaults(doc: Any) -> tuple[str | None, int | None]:
    rpr = doc.styles.element.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
    fonts = rpr.find(f"{W}rFonts") if rpr is not None else None
    size = rpr.find(f"{W}sz") if rpr is not None else None
    return (
        fonts.get(f"{W}ascii") if fonts is not None else None,
        int(size.get(f"{W}val")) if size is not None else None,
    )


def _resolved(run: Any, para: Any, defaults: tuple[str | None, int | None]) -> tuple[str | None, int | None]:
    """The run's font and half-point size as Word resolves them: run, style chain, defaults."""
    name, size = run.font.name, run.font.size
    style = para.style
    while style is not None and (name is None or size is None):
        name = name or style.font.name
        size = size or style.font.size
        style = style.base_style
    return name or defaults[0], (int(size.pt * 2) if size is not None else defaults[1])


def font_fails(doc: Any, font: str, size_pt: float) -> list[str]:
    """One family throughout, and most of the words at the authored size: the firm's
    file owns its larger headings and its chart's size, never another font."""
    defaults = _defaults(doc)
    chars: Counter[int | None] = Counter()
    bad = 0
    paras = [*doc.paragraphs, *(p for t in doc.tables for c in t._cells for p in c.paragraphs)]
    for i, p in enumerate(paras):
        for r in p.runs:
            if not r.text.strip():
                continue
            name, size = _resolved(r, p, defaults)
            bad += name != font
            if i < len(doc.paragraphs):
                chars[size] += len(r.text)
    out = [f"font: {bad} run(s) are not {font}"] if bad else []
    if chars and chars.most_common(1)[0][0] != int(size_pt * 2):
        out.append(f"font: the body is not {size_pt} point")
    return out


def checked_format(fmt: dict[str, Any], cls: str, base: FirmBase) -> dict[str, Any]:
    """``fmt`` as the format check reads a memo on a base: ``font_from_base`` in place of
    every run being explicitly the authored size."""
    out = _brief_checked_format(fmt, cls, base)
    out["font_from_base"] = lambda doc: font_fails(doc, str(fmt["font"]), float(fmt["size_pt"]))
    return out
