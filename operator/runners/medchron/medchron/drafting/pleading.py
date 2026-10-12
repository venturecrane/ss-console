"""Pleading paper: the rendered Word file laid onto the firm's pleading paper.

Used only when the attorney's request asks for it ("on pleading paper", "into
the caption"): the firm's house style drafts discovery on plain paper by
default (house style D1), and an explicit request overrides that default
(``drafting-firm.yaml`` ``format.pleading`` records the precedence and the
paper's geometry). Without a ``format.pleading`` block a pleading request
cannot be honored and the pre-delivery review holds the job for SMD.

The paper is built the way Word's own pleading template builds it, which is
how the firm's signed papers are laid out (read from the structure of the
firm's specimen, never its text):

* a text box anchored in the page header at the top margin, left of the
  text, holding the line numbers 1 to N, one paragraph each at the exact
  line pitch;
* two vertical rules left of the text and one right of it, the full page
  height;
* every paragraph of the body, tables included, at exactly the same pitch
  with no space before or after, so each line of text sits on a number
  (California Rules of Court, rule 2.108: lines numbered consecutively on the
  left margin, one and a half or double spaced);
* a footer with the page number, a rule, and the document's title in 10-point
  type (rule 2.110).

``check`` reads a file back against the same spec; ``format_check`` runs it
instead of the plain-paper rule when the job's paper is pleading.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EMU_PER_INCH = 914400
EMU_PER_PT = 12700
BOX_NAME = "SMD Pleading Line Numbers"
RULE_NAMES = ("SMD Pleading Left Rule 1", "SMD Pleading Left Rule 2", "SMD Pleading Right Rule")
PAGE_HEIGHT_IN = 11.0
TITLE_PT = 10
#: The words that make a request a request for pleading paper. Chris's own
#: phrasing on 2026-10-12 ("into the caption so that they are on pleading
#: paper") and the plain forms.
ASKS = re.compile(
    r"(?i)\bpleading\s+(?:paper|format|template|caption|form)\b|\bon\s+(?:our\s+|the\s+|a\s+)?pleading\b|"
    r"\binto\s+(?:the\s+|our\s+|a\s+)?(?:pleading|caption)\b|\bline[-\s]numbered\b"
)
NOT_PLEADING = re.compile(r"(?i)\bnot\s+(?:on\s+)?(?:line[-\s]numbered\s+)?pleading\b|\bplain\s+paper\b")


def wants_pleading(request: str) -> bool:
    """True when the request asks for pleading paper; a request that says
    "not pleading" or "plain paper" does not."""
    return bool(ASKS.search(request or "")) and not NOT_PLEADING.search(request or "")


@dataclass(frozen=True)
class Spec:
    lines: int = 28
    pitch_pt: float = 24.0
    #: top, right, bottom, left, in inches.
    margins_in: tuple[float, float, float, float] = (0.5, 0.5, 0.69, 1.5)
    font: str = "Times New Roman"
    size_pt: float = 12.0

    @property
    def box_height_emu(self) -> int:
        return int(round(self.lines * self.pitch_pt * EMU_PER_PT))


def spec_for(fmt: dict[str, Any]) -> Spec | None:
    """The firm's authored pleading paper, or None when the firm has none."""
    p = fmt.get("pleading")
    if not isinstance(p, dict):
        return None
    return Spec(
        lines=int(p["lines"]),
        pitch_pt=float(p["line_pitch_pt"]),
        margins_in=tuple(float(x) for x in p["margins_in"]),  # type: ignore[arg-type]
        font=str(fmt["font"]),
        size_pt=float(fmt["size_pt"]),
    )


_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
)


def _anchor(shape_id: int, name: str, x_emu: int, y_emu: int, cx: int, cy: int, rel_v: str, body: str) -> str:
    return (
        f'<mc:AlternateContent {_NS}><mc:Choice Requires="wps"><w:drawing>'
        f'<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="{251659264 + shape_id}" '
        'behindDoc="1" locked="1" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="margin"><wp:posOffset>{x_emu}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="{rel_v}"><wp:posOffset>{y_emu}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        f'<wp:docPr id="{shape_id}" name="{name}"/><wp:cNvGraphicFramePr/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        f"{body}</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice></mc:AlternateContent>"
    )


def _rule(shape_id: int, name: str, x_emu: int) -> str:
    h = int(PAGE_HEIGHT_IN * EMU_PER_INCH)
    body = (
        "<wps:wsp><wps:cNvCnPr/><wps:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="{h}"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
        '<a:ln w="9525"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln></wps:spPr><wps:bodyPr/></wps:wsp>'
    )
    return _anchor(shape_id, name, x_emu, 0, 0, h, "page", body)


def _numbers(spec: Spec) -> str:
    twips = int(round(spec.pitch_pt * 20))
    half = int(round(spec.size_pt * 2))
    paras = "".join(
        f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{twips}" w:lineRule="exact"/><w:jc w:val="right"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="{spec.font}" w:hAnsi="{spec.font}" w:cs="{spec.font}"/>'
        f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/></w:rPr><w:t>{n}</w:t></w:r></w:p>'
        for n in range(1, spec.lines + 1)
    )
    width = int(0.4 * EMU_PER_INCH)
    body = (
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr>'
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{spec.box_height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>'
        f"<wps:txbx><w:txbxContent>{paras}</w:txbxContent></wps:txbx>"
        '<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"><a:noAutofit/></wps:bodyPr>'
        "</wps:wsp>"
    )
    return _anchor(1, BOX_NAME, -int(0.55 * EMU_PER_INCH), 0, width, spec.box_height_emu, "margin", body)


def _exact(pf: Any, pitch_pt: float) -> None:
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(pitch_pt)
    pf.space_before, pf.space_after = Pt(0), Pt(0)


def _all_paragraphs(doc: Any) -> list[Any]:
    out = list(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                out += list(cell.paragraphs)
    return out


def title_of(doc: Any) -> str:
    """The paper's title as its caption prints it: the first bold line naming
    a discovery set, else the first level-1 heading."""
    pat = re.compile(r"(?i)interrogatories|requests?\s+for\s+(admission|production)|responses?\s+to")
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if pat.search(p.text) and any(r.bold for r in p.runs if r.text.strip()):
                        return " ".join(p.text.split())
    return next((" ".join(p.text.split()) for p in doc.paragraphs if pat.search(p.text)), "")


def apply(path: Path, spec: Spec) -> list[str]:
    """Lay the rendered file at ``path`` onto pleading paper, in place."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    from smokeball_connector.docx_base import add_page_field, set_run_font

    doc = docx.Document(str(path))
    if len(doc.sections) != 1:
        raise ValueError("pleading paper: the rendered file has more than one section")
    sec = doc.sections[0]
    top, right, bottom, left = spec.margins_in
    sec.top_margin, sec.right_margin, sec.bottom_margin, sec.left_margin = (
        Inches(top),
        Inches(right),
        Inches(bottom),
        Inches(left),
    )
    sec.header_distance, sec.footer_distance = Inches(min(0.5, top / 2)), Inches(min(0.3, bottom / 2))
    for ln in sec._sectPr.findall(qn("w:lnNumType")):
        sec._sectPr.remove(ln)
    for p in _all_paragraphs(doc):
        _exact(p.paragraph_format, spec.pitch_pt)
    header = sec.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    for r in list(hp.runs):
        r._r.getparent().remove(r._r)
    hp.paragraph_format.line_spacing = Pt(1)
    hp.paragraph_format.space_before, hp.paragraph_format.space_after = Pt(0), Pt(0)
    text_w = int((8.5 - left - right) * EMU_PER_INCH)
    shapes = [
        _numbers(spec),
        _rule(2, RULE_NAMES[0], -int(0.08 * EMU_PER_INCH)),
        _rule(3, RULE_NAMES[1], -int(0.12 * EMU_PER_INCH)),
        _rule(4, RULE_NAMES[2], text_w + int(0.06 * EMU_PER_INCH)),
    ]
    for xml in shapes:
        run = hp.add_run()
        run._r.append(parse_xml(xml))
    footer = sec.footer
    footer.is_linked_to_previous = False
    for p in list(footer.paragraphs):
        p._p.getparent().remove(p._p)
    page = footer.add_paragraph()
    page.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(add_page_field(page), spec.font, spec.size_pt)
    title = title_of(doc)
    tp = footer.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ppr = tp._p.get_or_add_pPr()
    ppr.append(
        parse_xml(
            '<w:pBdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:top w:val="single" w:sz="6" w:space="1" w:color="auto"/></w:pBdr>'
        )
    )
    set_run_font(tp.add_run(title or " "), spec.font, TITLE_PT)
    buf = io.BytesIO()
    doc.save(buf)
    part = path.with_name(f".{path.name}.pleading.tmp")
    part.write_bytes(buf.getvalue())
    part.replace(path)
    notes = [f"laid on the firm's pleading paper ({spec.lines} numbered lines at {spec.pitch_pt:g} point), as requested"]
    if not title:
        notes.append("pleading paper: no title was found for the footer; the footer carries the page number only")
    return notes


def check(doc: Any, blob: bytes, spec: Spec) -> list[str]:
    """What is wrong with ``doc`` as pleading paper under ``spec``; empty is a pass."""
    import zipfile

    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Inches, Pt

    fails: list[str] = []
    sec = doc.sections[0]
    want = tuple(Inches(x) for x in spec.margins_in)
    got = (sec.top_margin, sec.right_margin, sec.bottom_margin, sec.left_margin)
    if any(abs(int(a) - int(b)) > 635 for a, b in zip(got, want)):
        fails.append("pleading: margins are not the firm's pleading paper")
    z = zipfile.ZipFile(io.BytesIO(blob))
    headers = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith("word/header"))
    box = re.search(r"<w:txbxContent>(.*?)</w:txbxContent>", headers, re.S)
    nums = re.findall(r"<w:t>(\d+)</w:t>", box.group(1)) if box else []
    if nums != [str(n) for n in range(1, spec.lines + 1)]:
        fails.append(f"pleading: the header does not number lines 1 to {spec.lines}")
    elif box and len(re.findall(rf'w:line="{int(round(spec.pitch_pt * 20))}" w:lineRule="exact"', box.group(1))) != spec.lines:
        fails.append(f"pleading: the line numbers are not at exactly {spec.pitch_pt:g} point")
    if sum(headers.count(f'name="{n}"') for n in RULE_NAMES) != len(RULE_NAMES):
        fails.append("pleading: the margin rules are missing")
    if not re.search(rf'name="{BOX_NAME}"', headers):
        fails.append("pleading: no line-number box")
    off = [
        p
        for p in _all_paragraphs(doc)
        if p.text.strip()
        and (
            p.paragraph_format.line_spacing != Pt(spec.pitch_pt)
            or p.paragraph_format.line_spacing_rule != WD_LINE_SPACING.EXACTLY
            or (p.paragraph_format.space_before or Pt(0)) != Pt(0)
            or (p.paragraph_format.space_after or Pt(0)) != Pt(0)
        )
    ]
    if off:
        fails.append(f"pleading: {len(off)} paragraph(s) off the {spec.pitch_pt:g}-point line pitch: {off[0].text[:40]}")
    footers = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith("word/footer"))
    if not re.search(r"<w:instrText[^>]*>\s*PAGE\b", footers) and 'w:instr=" PAGE' not in footers:
        fails.append("pleading: no page number in the footer")
    return fails
