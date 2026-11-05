"""The revision made IN the filed Word file: his bytes, only the asked-for
elements changed.

A revision never re-renders the brief. The filed .docx is opened and edited
in place, so everything the attorney accepted and no ask names (his styles
and their names, his chart's colours, widths, margins and alignment, his
caption, his paper, his numbering) is the same XML afterwards, byte for byte
in meaning:

* CONTENT: the filed body reads as markdown blocks in order (``prior``: one
  block per non-empty paragraph or table). The edited markdown is diffed
  against those blocks; an unchanged block keeps its element untouched, a
  changed one has its runs replaced inside the SAME paragraph (its pPr and
  its first run's rPr kept), a new one is a clone of the paragraph it
  replaces or of the nearest sibling of its role (heading, body, list,
  table), and a removed one is removed.
* LAYOUT: each formatting key (``checklist.LAYOUT_KEYS``) edits only the
  elements it names (body or heading paragraphs, the charts, the caption,
  the first page, the signature lines), every element in its schema's order.

``Edit`` records every top-level element it touched, so ``prove`` can hold
the result to it: every other element, and every other part of the package
(styles, numbering, section properties, headers, footers), must be the filed
XML, rsid noise aside; anything else is ``revise_layout_lost``.
"""

from __future__ import annotations

import copy
import difflib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import briefpage, frontpage
from . import charts, filed as filed_mod, layout as lay, prior

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NOISE = re.compile(r'\s(?:w:rsid\w*|w14:paraId|w14:textId)="[^"]*"')
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET = re.compile(r"^\s*-\s+")
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
_EMPH = re.compile(r"(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)")


@dataclass
class Edit:
    """The filed document, open, and what this revision touched in it."""

    doc: Any
    #: The top-level body elements changed or added. Held as elements, never as
    #: id()s: an lxml proxy lives only while referenced, so an id is reused.
    touched: set[Any] = field(default_factory=set)
    removed: int = 0
    said: list[str] = field(default_factory=list)

    def top(self, el: Any) -> Any:
        body = self.doc.element.body
        while el.getparent() is not None and el.getparent() is not body:
            el = el.getparent()
        return el

    def mark(self, el: Any) -> None:
        self.touched.add(self.top(el))


def open_filed(path: Path) -> Edit:
    import docx

    return Edit(docx.Document(str(path)))


# ---- content --------------------------------------------------------------------------
def blocks(md: str) -> list[str]:
    """Markdown as the blocks ``prior.docx_to_md`` writes: one per paragraph or
    table; a block of list lines is one block per item."""
    out: list[str] = []
    for b in re.split(r"\n\s*\n", md):
        b = b.strip()
        if not b:
            continue
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if lines[0].startswith("|"):
            out.append("\n".join(_row(ln) for ln in lines if not _SEPARATOR.match(ln)))
        elif len(lines) > 1 and all(_BULLET.match(ln) for ln in lines):
            out += lines
        elif len(lines) > 1 and _HEADING.match(lines[0]):
            out += [lines[0], " ".join(lines[1:])]
        else:
            out.append(" ".join(lines))
    return out


def _row(line: str) -> str:
    return "| " + " | ".join(" ".join(c.split()) for c in line.strip().strip("|").split("|")) + " |"


def _role(md_block: str) -> str:
    if md_block.startswith("|"):
        return "t"
    m = _HEADING.match(md_block)
    if m:
        return f"h{len(m.group(1))}"
    return "list" if _BULLET.match(md_block) else "p"


def filed_blocks(ed: Edit) -> list[tuple[Any, str]]:
    """(element, markdown block) for every element ``docx_to_md`` reads."""
    out = []
    for kind, b in prior._blocks(ed.doc):
        md = prior.block_md(kind, b)
        if md is not None:
            out.append((b._tbl if kind == "t" else b._p, blocks(md)[0]))
    return out


def _segments(text: str) -> list[tuple[str, bool, bool]]:
    out = []
    for piece in _EMPH.split(text):
        if not piece:
            continue
        if piece.startswith("***") and piece.endswith("***") and len(piece) > 6:
            out.append((piece[3:-3], True, True))
        elif piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            out.append((piece[2:-2], True, False))
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            out.append((piece[1:-1], False, True))
        else:
            out.append((piece, False, False))
    return out


def _set_runs(p_el: Any, text: str, heading: bool) -> None:
    """The paragraph's runs replaced by ``text``: its pPr kept, each new run a
    copy of its first run's rPr (emphasis set from the markdown on prose; a
    heading's or a cell's text is plain markdown, so its run keeps its own)."""
    from docx.oxml import OxmlElement

    runs = p_el.findall(f"{_W}r")
    template = runs[0].find(f"{_W}rPr") if runs else None
    for child in list(p_el):
        if child.tag in (f"{_W}r", f"{_W}hyperlink", f"{_W}ins", f"{_W}del", f"{_W}proofErr"):
            p_el.remove(child)
    for t, bold, italic in [(text, False, False)] if heading else _segments(text):
        r = OxmlElement("w:r")
        rpr = copy.deepcopy(template) if template is not None else OxmlElement("w:rPr")
        if not heading:
            for tag in ("b", "bCs", "i", "iCs"):
                for old in rpr.findall(f"{_W}{tag}"):
                    rpr.remove(old)
            for tag, on in (("b", bold), ("i", italic)):
                if on:
                    charts.put(rpr, tag, OxmlElement(f"w:{tag}"), charts.RPR)
        if len(rpr):
            r.append(rpr)
        te = OxmlElement("w:t")
        te.text = t
        te.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(te)
        p_el.append(r)


def _text_of(md_block: str) -> tuple[str, bool]:
    m = _HEADING.match(md_block)
    if m:
        return " ".join(m.group(2).split()), True
    return _BULLET.sub("", md_block).strip(), False


def _set_table(tbl_el: Any, md_block: str) -> None:
    """The table's cell texts set from the markdown, rows added as copies of
    the last body row (the header row's look stays the header's)."""
    from docx.table import Table

    rows = [ln for ln in md_block.splitlines() if not _SEPARATOR.match(ln)]
    cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in rows]
    trs = tbl_el.findall(f"{_W}tr")
    while len(trs) < len(cells):
        trs[-1].addnext(copy.deepcopy(trs[-1]))
        trs = tbl_el.findall(f"{_W}tr")
    for extra in trs[len(cells) :]:
        tbl_el.remove(extra)
    t = Table(tbl_el, None)
    for row, texts in zip(t.rows, cells):
        for c, text in zip(row.cells, texts):
            paras = c._tc.findall(f"{_W}p")
            for extra in paras[1:]:
                c._tc.remove(extra)
            _set_runs(paras[0], text, heading=True)


def _apply_block(ed: Edit, el: Any, md_block: str, rules: Any) -> None:
    if el.tag == f"{_W}tbl":
        _set_table(el, md_block)
        return
    text, heading = _text_of(md_block)
    _set_runs(el, text, heading)
    if heading:
        _level(ed, el, len(_HEADING.match(md_block).group(1)), rules)  # type: ignore[union-attr]


def _level(ed: Edit, el: Any, level: int, rules: Any) -> None:
    """A heading paragraph at ``level``: the document's own style for that
    level when it has one (its name with the level's digit), else an outline
    level on the paragraph; a new level takes the firm's placement for it."""
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    p = Paragraph(el, ed.doc)
    if lay.heading_level(p) == level:
        return
    name = p.style.name if p.style is not None else ""
    want = re.sub(r"\d\s*$", str(level), name) if name[-1:].isdigit() else ""
    if want and want in [st.name for st in ed.doc.styles]:
        p.style = ed.doc.styles[want]
    else:
        ppr = el.get_or_add_pPr()
        for old in ppr.findall(f"{_W}outlineLvl"):
            ppr.remove(old)
        lvl = OxmlElement("w:outlineLvl")
        lvl.set(f"{_W}val", str(level - 1))
        ppr.append(lvl)
    if rules is not None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches

        i = min(level, len(rules.heading_align)) - 1
        p.alignment = {"center": WD_ALIGN_PARAGRAPH.CENTER, "left": WD_ALIGN_PARAGRAPH.LEFT}[rules.heading_align[i]]
        p.paragraph_format.left_indent = Inches(rules.heading_indent_in[i])
        for r in p.runs:
            r.bold, r.underline = bool(rules.heading_bold[i]), bool(rules.heading_underline[i])


def _template(pairs: list[tuple[Any, str]], role: str, near: int) -> Any:
    """The nearest filed element of ``role`` to position ``near``."""
    order = sorted(range(len(pairs)), key=lambda i: abs(i - near))
    for i in order:
        if _role(pairs[i][1]) == role:
            return pairs[i][0]
    for i in order:  # a heading level the brief lacks: its nearest heading
        if role.startswith("h") and _role(pairs[i][1]).startswith("h"):
            return pairs[i][0]
    for i in order:
        if _role(pairs[i][1]) == "p" or (role == "t" and pairs[i][0].tag == f"{_W}tbl"):
            return pairs[i][0]
    raise ValueError(f"no filed element to take a new {role} block's formatting from")


def _same_role(a: str, b: str) -> bool:
    return a == b or (a[0] == "h" and b[0] == "h")


def edit_content(ed: Edit, revised_md: str, rules: Any = None) -> None:
    """The revised markdown made in the filed body, element by element.
    ``rules`` (the firm's class rules) place a heading at a level the brief
    had none of."""
    pairs = filed_blocks(ed)
    old, new = [md for _el, md in pairs], blocks(revised_md)
    anchor: Any = None  # the element the next new block goes after
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            anchor = pairs[i2 - 1][0]
            continue
        reused: set[int] = set()
        for k, j in enumerate(range(j1, j2)):
            i = i1 + k
            if i < i2 and _same_role(_role(old[i]), _role(new[j])):
                el = pairs[i][0]  # the same element, its words replaced
                reused.add(i)
            else:
                el = copy.deepcopy(_template(pairs, _role(new[j]), i))
                _insert(ed, el, anchor, pairs)
            _apply_block(ed, el, new[j], rules)
            ed.mark(el)
            anchor = el
        for i in range(i1, i2):  # filed blocks the revision no longer has
            if i not in reused:
                gone = pairs[i][0]
                gone.getparent().remove(gone)
                ed.removed += 1


def _insert(ed: Edit, el: Any, anchor: Any, pairs: list[tuple[Any, str]]) -> None:
    if anchor is not None:
        anchor.addnext(el)
    elif pairs:
        pairs[0][0].addprevious(el)
    else:
        ed.doc.element.body.insert(0, el)


# ---- layout ---------------------------------------------------------------------------
def _paras_for(ed: Edit, key: str) -> list[Any]:
    doc = ed.doc
    if key in ("line_spacing", "line_spacing_pt"):
        held = {id(p._p) for p in briefpage.signature_lines(doc)}
        return [p for p in filed_mod.body_paragraphs(doc) if id(p._p) not in held]
    return [p for p in doc.paragraphs if p.text.strip() and lay.heading_level(p)]


def _spacing(ed: Edit, key: str, v: Any) -> None:
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    for p in _paras_for(ed, key):
        pf = p.paragraph_format
        if key.endswith("space_before_pt"):
            pf.space_before = Pt(float(v))
        elif key.endswith("space_after_pt"):
            pf.space_after = Pt(float(v))
        elif key.endswith("_pt"):
            pf.line_spacing_rule, pf.line_spacing = WD_LINE_SPACING.EXACTLY, Pt(float(v))
        else:
            pf.line_spacing = float(v)
        ed.mark(p._p)


def _shade(cell: Any, fill: str) -> None:
    from docx.oxml import parse_xml

    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    charts.put(
        cell._tc.get_or_add_tcPr(),
        "shd",
        parse_xml(f'<w:shd {ns} w:val="clear" w:color="auto" w:fill="{fill}"/>'),
        charts.TCPR,
    )


def _tables(ed: Edit, key: str, v: Any) -> None:
    from docx.oxml import parse_xml
    from docx.shared import Pt
    from smokeball_connector.docx_classes import is_total_row

    _cap, tables = charts.split(ed.doc)
    for t in tables:
        rows = list(t.rows)
        if key == "table_font_pt":
            for c in t._cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(float(v))
        elif key == "table_header_fill" and rows:
            for c in rows[0].cells:
                _shade(c, str(v).upper())
        elif key == "table_total_fill":
            for row in rows[1:]:
                if is_total_row([row.cells[0].text]):
                    for c in row.cells:
                        _shade(c, str(v).upper())
        elif key == "table_border_pt":
            sz = round(float(v) * 8)
            ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            edges = "".join(f'<w:{e} w:val="single" w:sz="{sz}" w:space="0" w:color="auto"/>' for e in _EDGES)
            charts.put(
                t._tbl.tblPr, "tblBorders", parse_xml(f"<w:tblBorders {ns}>{edges}</w:tblBorders>"), charts.TBLPR
            )
        ed.mark(t._tbl)


_EDGES = ("top", "left", "bottom", "right", "insideH", "insideV")
_TCB = ("top", "start", "left", "bottom", "end", "right", "insideH", "insideV", "tl2br", "tr2bl")
_TBLB = ("top", "start", "left", "bottom", "end", "right", "insideH", "insideV")


def _bottom(parent: Any, order: tuple[str, ...], on: bool) -> None:
    from docx.oxml import parse_xml

    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    src = parse_xml(f'<w:bottom {ns} w:val="single" w:sz="8" w:space="0" w:color="auto"/>') if on else None
    charts.put(parent, "bottom", src, order)


def _caption_rule(ed: Edit, v: Any) -> None:
    """The rule under the caption: the whole width (``True``), the left cell
    only (``"left"``, ending at the vertical line), or none."""
    from docx.oxml import OxmlElement

    tbl = briefpage._caption_table(ed.doc)
    if tbl is None:
        return
    pr = tbl.tblPr
    borders = pr.find(f"{_W}tblBorders")
    if borders is None:
        charts.put(pr, "tblBorders", OxmlElement("w:tblBorders"), charts.TBLPR)
        borders = pr.find(f"{_W}tblBorders")
    _bottom(borders, _TBLB, v is True)
    last = tbl.findall(f"{_W}tr")[-1].findall(f"{_W}tc")
    for n, tc in enumerate(last):
        tc_pr = tc.find(f"{_W}tcPr")
        if tc_pr is None:
            tc_pr = OxmlElement("w:tcPr")
            tc.insert(0, tc_pr)
        tcb = tc_pr.find(f"{_W}tcBorders")
        if tcb is None and not (v == "left" and n == 0):
            continue
        if tcb is None:
            charts.put(tc_pr, "tcBorders", OxmlElement("w:tcBorders"), charts.TCPR)
            tcb = tc_pr.find(f"{_W}tcBorders")
        _bottom(tcb, _TCB, v == "left" and n == 0)
    ed.mark(tbl)


def _front(ed: Edit, key: str, v: Any) -> None:
    from docx.oxml import OxmlElement

    doc = ed.doc
    if key == "caption_rule_below":
        _caption_rule(ed, v)
        return
    if key == "front_single_spaced" and v:
        paras = briefpage.front_paragraphs(doc)
        briefpage._single(paras)
        for p in paras:
            ed.mark(p._p)
        return
    if key.startswith("signature"):
        blk = briefpage._block(doc)
        if blk is not None and key == "signature_date_beside" and v and blk[1] >= 0:
            for p in blk[0][blk[1] : blk[2] + 4]:
                ed.mark(p._p)
            briefpage._move_date(*blk)
        blk = briefpage._block(doc)
        if blk is not None and key == "signature_gap_lines" and blk[1] == -1:
            briefpage._gap(blk[0], blk[2], int(v))
            ed.mark(blk[0][blk[2]]._p)
        if key == "signature_single_spaced" and v:
            lines = briefpage.signature_lines(doc)
            briefpage._single(lines)
            for p in lines:
                ed.mark(p._p)
        return
    paras, table = frontpage._front(doc)
    at = {
        "front_attorney_gap_lines": next((i for i, p in enumerate(paras) if frontpage._FOR.match(p.text)), None),
        "front_court_gap_lines": frontpage._court_at(paras),
        "front_caption_gap_lines": len(paras) if table is not None else None,
    }.get(key)
    if at is None:
        return
    anchor: Any = paras[at]._p if at < len(paras) else table
    if anchor is None:
        return
    have = frontpage._blanks_before(paras, at)
    for _ in range(have - int(v)):
        gone = anchor.getprevious()
        gone.getparent().remove(gone)
        ed.removed += 1
    for _ in range(int(v) - have):
        blank = OxmlElement("w:p")
        prev = anchor.getprevious()
        ppr = prev.find(f"{_W}pPr") if prev is not None and prev.tag == f"{_W}p" else None
        if ppr is not None:
            blank.append(copy.deepcopy(ppr))
        anchor.addprevious(blank)
        ed.mark(blank)


def edit_layout(ed: Edit, layout: dict[str, Any]) -> None:
    """Each asked layout key made on the filed elements it names."""
    for key, v in layout.items():
        if key in lay.FRONT_KEYS:
            _front(ed, key, v)
        elif key.startswith("table_"):
            _tables(ed, key, v)
        else:
            _spacing(ed, key, v)


# ---- the proof --------------------------------------------------------------------------
def _canon(el: Any) -> str:
    from lxml import etree

    return _NOISE.sub("", etree.tostring(el, encoding="unicode"))


def snapshot(ed: Edit) -> dict[Any, str]:
    """Every top-level body element as filed, before any edit."""
    return {el: _canon(el) for el in ed.doc.element.body}


def _parts(blob: bytes) -> dict[str, str]:
    """Every XML part but the body and the package's properties, canonical."""
    from lxml import etree

    z = zipfile.ZipFile(io.BytesIO(blob))
    out = {}
    for n in z.namelist():
        if n == "word/document.xml" or n.startswith("docProps/") or not n.endswith((".xml", ".rels")):
            continue
        tree = etree.fromstring(z.read(n))
        for el in tree.iter():
            for k in [k for k in el.attrib if "rsid" in k or k.endswith("}paraId") or k.endswith("}textId")]:
                del el.attrib[k]
        out[n] = etree.tostring(tree, method="c14n").decode("utf-8", "replace")
    return out


def prove(ed: Edit, before: dict[Any, str], filed_blob: bytes, out_blob: bytes, revised_md: str) -> list[str]:
    """Where the revised file differs from the filed one beyond what the edit
    touched; empty is a proof. The words read back are the revision's own."""
    out: list[str] = []
    kept = [el for el in ed.doc.element.body if el not in ed.touched]
    for el in kept:
        if el not in before:
            out.append(f"an element no ask made appeared: {_canon(el)[:80]!r}")
        elif before[el] != _canon(el):
            out.append(f"an element no ask names changed: {_canon(el)[:80]!r}")
    a, b = _parts(filed_blob), _parts(out_blob)
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            out.append(f"{name} changed, and no ask names it")
    import docx

    got = blocks(prior.docx_to_md_doc(docx.Document(io.BytesIO(out_blob))))
    want = blocks(revised_md)
    if got != want:
        diff = next((f"{x!r} for {y!r}" for x, y in zip(got, want) if x != y), f"{len(got)} blocks for {len(want)}")
        out.append(f"the revised file does not read as the revision: {diff[:160]}")
    return out


def save(ed: Edit, out: Path) -> bytes:
    buf = io.BytesIO()
    ed.doc.save(buf)
    blob = buf.getvalue()
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.tmp")
    part.write_bytes(blob)
    part.replace(out)
    return blob
