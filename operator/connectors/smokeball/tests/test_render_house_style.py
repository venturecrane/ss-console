"""A firm's AUTHORED house style, passed to the renderer as ``house``, held
in-class.

The renderer carries no firm's values: these tests pass a fictional firm's
``HouseStyle`` (the shape the drafting job builds from a firm's
drafting-firm.yaml) and assert it is honored: the font and size on every run,
plain Letter paper with line numbering removed, a brief's heading levels,
exact-point justified body and court lines, a discovery set's spacing, and a
deposition outline's page numbers. "In-class" is the point: a firm template's
own style (an underlined Heading 1, an Arial body, a space-after) must not move
a document off the AUTHORED standard, so each test runs on the starter AND on a
base that tries to. Without ``house`` the render is the product default
(test_render_product_defaults_unchanged.py).
"""

from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, Twips

import dataclasses

from smokeball_connector.docx_classes import HouseStyle
from smokeball_connector.docx_format import render_document

# A FICTIONAL firm's authored house style, the shape the drafting job builds
# from a firm's drafting-firm.yaml. The renderer holds no firm's values; every
# assertion below is about honoring an authored override.
_COMMON = dict(font="Times New Roman", size_pt=12, plain_letter_paper=True)
HOUSE = {
    "discovery_set": HouseStyle(**_COMMON, body_line_spacing=2.0, item_line_spacing=2.0, item_space_after_pt=0.0),
    "mediation_brief": HouseStyle(
        **_COMMON,
        heading_indent_in=(0.0, 0.5, 1.0),
        heading_underline=(False, True, True),
        body_line_spacing_pt=24.0,
        body_justify=True,
        body_first_line_indent_in=0.5,
        centered_court_lines=True,
        bold_italic_heading_indent_in=1.0,
        page_numbers_always=True,
        footer_title="Plaintiff's Mediation Brief",
    ),
    "memo": HouseStyle(**_COMMON),
    "depo_outline": HouseStyle(**_COMMON, page_numbers_always=True),
}
HOUSE["discovery_response"] = HOUSE["discovery_set"]


def _render(md, cls, base, house=None, **kw):
    return render_document(md, cls, base, house=house or HOUSE.get(cls), **kw)


HOUSE_CLASSES = ("discovery_set", "discovery_response", "mediation_brief", "memo", "depo_outline")

BRIEF = """| PLAINTIFF ALPHA EXAMPLE, | Case No. {{FILL: case number}} |
| v. |  |
| BETA EXAMPLE |  |

# I. INTRODUCTION

This brief is submitted on behalf of plaintiff.

## A. Past Medical Expenses

Body text of the subsection.

# II. LIABILITY

More body.
"""

DISCOVERY = """# PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE

## DEFINITIONS

"INCIDENT" means the collision described in the complaint.

**SPECIAL INTERROGATORY NO. 1:**

Identify each person who witnessed the INCIDENT.

**SPECIAL INTERROGATORY NO. 2:**

State all facts supporting your contention.
"""


def _hostile_base(*, footer: str = "", page_field: bool = False) -> bytes:
    """A firm base that tries every way to move the standard: A4 paper, line
    numbering (pleading paper), an underlined Arial Heading 1 and SMD Heading 1,
    an Arial 14 body with space after."""
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Twips(11906), Twips(16838)  # A4
    ln = OxmlElement("w:lnNumType")
    ln.set(qn("w:countBy"), "1")
    sec._sectPr.append(ln)
    for name in ("Heading 1", "SMD Heading 1"):
        st = doc.styles[name] if name == "Heading 1" else doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.font.underline = True
        st.font.name = "Arial"
    body = doc.styles.add_style("SMD Body", WD_STYLE_TYPE.PARAGRAPH)
    body.font.name, body.font.size = "Arial", Pt(14)
    body.paragraph_format.space_after = Pt(18)
    if footer:
        sec.footer.paragraphs[0].text = footer
    if page_field:
        r = sec.footer.paragraphs[0].add_run()
        for kind, text in (("begin", None), (None, " PAGE "), ("end", None)):
            el = OxmlElement("w:fldChar" if kind else "w:instrText")
            if kind:
                el.set(qn("w:fldCharType"), kind)
            else:
                el.text = text
            r._r.append(el)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


BASES = {"starter": None, "hostile firm base": _hostile_base()}


def _doc(blob: bytes) -> Document:
    return Document(io.BytesIO(blob))


def _effective_underline(run, para) -> bool:
    if run.underline is not None:
        return bool(run.underline)
    style = para.style
    while style is not None:
        if style.font.underline is not None:
            return bool(style.font.underline)
        style = style.base_style
    return False


def level1_headings_underlined(blob: bytes) -> bool:
    """True when any level-1 (roman numeral) heading renders underlined."""
    for p in _doc(blob).paragraphs:
        if p.text.split(".")[0].strip() in ("I", "II", "III", "IV"):
            if any(_effective_underline(r, p) for r in p.runs if r.text.strip()):
                return True
    return False


# ---- font, paper -----------------------------------------------------------------


@pytest.mark.parametrize("cls", HOUSE_CLASSES)
@pytest.mark.parametrize("base", list(BASES))
def test_every_run_is_times_new_roman_12_in_class(cls: str, base: str) -> None:
    blob, _ = _render(BRIEF + DISCOVERY + "- a bullet\n1. a numbered item\n", cls, BASES[base])
    doc = _doc(blob)
    runs = [r for p in doc.paragraphs for r in p.runs if r.text.strip()]
    runs += [r for t in doc.tables for c in t._cells for p in c.paragraphs for r in p.runs if r.text.strip()]
    assert runs
    for r in runs:
        fonts = r._r.rPr.find(qn("w:rFonts"))
        assert fonts is not None and {fonts.get(qn(a)) for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs")} == {
            "Times New Roman"
        }, r.text
        assert r.font.size == Pt(12), r.text


@pytest.mark.parametrize("cls", HOUSE_CLASSES)
def test_plain_letter_paper_never_pleading_paper(cls: str) -> None:
    blob, report = _render(BRIEF, cls, BASES["hostile firm base"])
    sec = _doc(blob).sections[0]
    assert (sec.page_width, sec.page_height) == (Inches(8.5), Inches(11))
    assert sec._sectPr.find(qn("w:lnNumType")) is None
    notes = report.to_dict()["notes"]
    assert any("US Letter" in n for n in notes) and any("never pleading paper" in n for n in notes)


def test_without_an_override_the_firms_own_typography_and_paper_stand() -> None:
    blob, _ = render_document("Body.", "memo", BASES["hostile firm base"])
    sec = _doc(blob).sections[0]
    assert sec.page_width == Twips(11906)


# ---- mediation brief headings ---------------------------------------------------------


@pytest.mark.parametrize("base", list(BASES))
def test_mediation_level1_headings_are_centered_bold_and_never_underlined(base: str) -> None:
    blob, _ = _render(BRIEF, "mediation_brief", BASES[base])
    doc = _doc(blob)
    h1 = [p for p in doc.paragraphs if p.text in ("I. INTRODUCTION", "II. LIABILITY")]
    assert len(h1) == 2
    for p in h1:
        assert p.alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert all(r.bold for r in p.runs)
    assert not level1_headings_underlined(blob)


def test_the_underline_check_can_fail() -> None:
    """Falsifier: an override that underlines level 1 is caught."""
    bad = dataclasses.replace(HOUSE["mediation_brief"], heading_underline=(True, True, False))
    blob, _ = _render(BRIEF, "mediation_brief", None, house=bad)
    assert level1_headings_underlined(blob)


@pytest.mark.parametrize("base", list(BASES))
def test_mediation_level2_is_indented_bold_and_underlined(base: str) -> None:
    blob, _ = _render(BRIEF, "mediation_brief", BASES[base])
    p = next(p for p in _doc(blob).paragraphs if p.text == "A. Past Medical Expenses")
    assert p.paragraph_format.left_indent == Inches(0.5)
    assert all(r.bold and r.underline for r in p.runs)


@pytest.mark.parametrize("base", list(BASES))
def test_mediation_body_double_spaced_and_caption_first(base: str) -> None:
    blob, _ = _render(BRIEF, "mediation_brief", BASES[base])
    doc = _doc(blob)
    body = next(p for p in doc.paragraphs if p.text.startswith("This brief"))
    assert body.paragraph_format.line_spacing == Pt(24)
    assert body.paragraph_format.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    assert body.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert body.paragraph_format.space_after == Pt(0)
    first = doc.element.body[0]
    assert first.tag == qn("w:tbl")  # the caption table leads the document


# ---- discovery --------------------------------------------------------------------


@pytest.mark.parametrize("cls", ("discovery_set", "discovery_response"))
@pytest.mark.parametrize("base", list(BASES))
def test_discovery_items_double_spaced_between_items_only(cls: str, base: str) -> None:
    blob, _ = _render(DISCOVERY, cls, BASES[base])
    doc = _doc(blob)
    labels = [p for p in doc.paragraphs if p.text.startswith("SPECIAL INTERROGATORY NO.")]
    items = [p for p in doc.paragraphs if p.text.startswith(("Identify", "State all"))]
    assert len(labels) == 2 and len(items) == 2
    for p in labels:
        assert all(r.bold and r.underline and r.font.all_caps for r in p.runs if r.text.strip())
        pf = p.paragraph_format
        assert (pf.line_spacing, pf.space_before, pf.space_after) == (2.0, Pt(0), Pt(0))
    for p in items:
        pf = p.paragraph_format
        assert pf.first_line_indent == Inches(0.5)
        assert (pf.line_spacing, pf.space_before, pf.space_after) == (2.0, Pt(0), Pt(0))


def test_discovery_spacing_check_can_fail() -> None:
    bad = dataclasses.replace(HOUSE["discovery_set"], item_space_after_pt=12.0)
    blob, _ = _render(DISCOVERY, "discovery_set", None, house=bad)
    item = next(p for p in _doc(blob).paragraphs if p.text.startswith("Identify"))
    assert item.paragraph_format.space_after == Pt(12)


def test_discovery_definitions_section_is_supported() -> None:
    blob, report = _render(DISCOVERY, "discovery_set", None)
    doc = _doc(blob)
    assert any(p.text == "DEFINITIONS" for p in doc.paragraphs)
    d = next(p for p in doc.paragraphs if p.text.startswith('"INCIDENT"'))
    assert d.paragraph_format.line_spacing == 2.0


# ---- deposition outline ---------------------------------------------------------------


def _footer_xml(blob: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(blob))
    return "".join(z.read(n).decode() for n in z.namelist() if n.startswith("word/footer"))


@pytest.mark.parametrize(
    "base",
    [None, _hostile_base(), _hostile_base(footer="123 Example Street")],
    ids=["starter", "firm base", "firm base with footer text"],
)
def test_depo_outline_footer_always_carries_a_centered_page_field(base: bytes | None) -> None:
    blob, _ = _render("# Witness background\n\n1. Name and address.\n", "depo_outline", base)
    xml = _footer_xml(blob)
    assert "PAGE" in xml and 'w:fldCharType="begin"' in xml
    footer = _doc(blob).sections[0].footer
    page_para = next(p for p in footer.paragraphs if "PAGE" in p._p.xml)
    assert page_para.alignment == WD_ALIGN_PARAGRAPH.CENTER
    if base is not None and b"123 Example" in base:
        assert any("123 Example Street" in p.text for p in footer.paragraphs)


def test_depo_outline_does_not_add_a_second_page_field() -> None:
    blob, report = _render("Body.", "depo_outline", _hostile_base(page_field=True))
    assert _footer_xml(blob).count("PAGE") == 1
    assert "page number field added to the footer" not in report.to_dict()["notes"]


def test_memo_is_times_new_roman_12() -> None:
    blob, _ = _render("# To the file\n\nA memo body.", "memo", BASES["hostile firm base"])
    p = next(p for p in _doc(blob).paragraphs if p.text == "A memo body.")
    assert all(r.font.name == "Times New Roman" and r.font.size == Pt(12) for r in p.runs)


# ---- the attorney's signed briefs: level 3, body indent, emphasis, tables, page numbers -----

SPECIMEN = """# VI. DAMAGES

## A. Past Medical Expenses

### 1. Northfield Physical Therapy

***Northfield Physical Therapy*** treated plaintiff. **Dates of service:** 02/01/2026 to 03/01/2026. *Prognosis* guarded.

| Provider | Billed | Paid (Howell) |
| --- | --- | --- |
| Northfield Physical Therapy | $900.00 | $410.25 |

1. A numbered point.
"""


@pytest.mark.parametrize("base", list(BASES))
def test_mediation_level3_is_indented_one_inch_bold_and_underlined(base: str) -> None:
    blob, _ = _render(SPECIMEN, "mediation_brief", BASES[base])
    p = next(p for p in _doc(blob).paragraphs if p.text == "1. Northfield Physical Therapy")
    assert p.paragraph_format.left_indent == Inches(1.0)
    assert all(r.bold and r.underline for r in p.runs)


@pytest.mark.parametrize("base", list(BASES))
def test_mediation_body_is_first_line_indented_and_emphasis_renders(base: str) -> None:
    blob, _ = _render(SPECIMEN, "mediation_brief", BASES[base])
    p = next(p for p in _doc(blob).paragraphs if p.text.startswith("Northfield Physical Therapy treated"))
    assert p.paragraph_format.first_line_indent == Inches(0.5) and p.paragraph_format.line_spacing == Pt(24)
    runs = {r.text: (bool(r.bold), bool(r.italic)) for r in p.runs}
    assert runs["Northfield Physical Therapy"] == (True, True)  # bold italic
    assert runs["Dates of service:"] == (True, False)  # a run-in label
    assert runs["Prognosis"] == (False, True)
    assert not any("*" in r.text for r in p.runs)
    numbered = next(p for p in _doc(blob).paragraphs if "A numbered point." in p.text)
    assert numbered.paragraph_format.first_line_indent == Inches(-0.5)  # a numbered item still hangs


def test_mediation_tables_are_times_new_roman_12() -> None:
    blob, _ = _render(SPECIMEN, "mediation_brief", None)
    cells = [r for c in _doc(blob).tables[0]._cells for p in c.paragraphs for r in p.runs if r.text.strip()]
    assert cells and all(r.font.name == "Times New Roman" and r.font.size == Pt(12) for r in cells)


def test_a_mediation_brief_is_numbered_at_the_bottom_even_under_a_firm_footer() -> None:
    blob, _ = _render(SPECIMEN, "mediation_brief", _hostile_base(footer="123 Example Street"))
    assert "PAGE" in _footer_xml(blob)


FRONT = """`{{FILL: attorney name | caption}}`
Attorneys for Plaintiff

**SUPERIOR COURT OF THE STATE OF CALIFORNIA**
**`{{FILL: COUNTY OF X | caption}}`**

| `{{FILL: PLAINTIFF | pleading}}`, | Case No. CV-0001 |
| --- | --- |
| v. | **PLAINTIFF'S MEDIATION BRIEF** |

# I. INTRODUCTION

Body.

## A. Past Medical

***`{{FILL: provider | records}}`***

**Dates of service:** 01/02/2026.
"""


@pytest.mark.parametrize("base", list(BASES))
def test_the_signed_brief_front_matter_provider_lines_and_footer(base: str) -> None:
    blob, _ = _render(FRONT, "mediation_brief", BASES[base])
    doc = _doc(blob)
    paras = [p for p in doc.paragraphs if p.text.strip()]
    attorney, court = paras[0], [p for p in paras if "SUPERIOR COURT" in p.text or "COUNTY OF" in p.text]
    assert attorney.paragraph_format.first_line_indent in (None, 0) and attorney.alignment != WD_ALIGN_PARAGRAPH.CENTER
    assert len(court) == 2 and all(p.alignment == WD_ALIGN_PARAGRAPH.CENTER for p in court)
    assert "`" not in "".join(p.text for p in paras)  # a code span inside emphasis leaves no backticks
    provider = next(p for p in paras if p.text.startswith("{{FILL: provider"))
    assert provider.paragraph_format.left_indent == Inches(1.0) and provider.paragraph_format.first_line_indent == 0
    lettered = next(p for p in paras if p.text == "A. Past Medical")
    assert lettered.paragraph_format.left_indent == Inches(0.5)
    footer = doc.sections[0].footer
    assert [p.text for p in footer.paragraphs if p.text.strip()][-1] == "Plaintiff's Mediation Brief"
    assert "PAGE" in _footer_xml(blob)
