"""The paper reader's blocks, read straight from the Word XML (invented files)."""

from __future__ import annotations

from pathlib import Path

import paper_testkit as kit
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from jobs.drafting.paper import blocks


def _read(doc, tmp_path: Path):
    path = tmp_path / "x.docx"
    doc.save(str(path))
    return blocks.read_docx(path)


def test_bold_caps_align_style_and_heading(tmp_path: Path) -> None:
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("DEFINITIONS").bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading("Instructions", level=2)
    mixed = doc.add_paragraph()
    mixed.add_run("Half ").bold = True
    mixed.add_run("bold")
    doc.add_paragraph("")  # empty: no block
    got = _read(doc, tmp_path)
    assert [b.text for b in got] == ["DEFINITIONS", "Instructions", "Half bold"]
    title, heading, body = got
    assert title.bold and title.caps and title.align == "center"
    assert heading.heading == 2 and heading.style == "Heading 2" and heading.bold  # bold from the style
    assert not body.bold and not body.caps
    assert [b.id for b in got] == ["p0", "p1", "p2"]


def test_word_auto_numbering_renders_the_number_not_in_the_text(tmp_path: Path) -> None:
    doc = Document()
    nid = kit.add_numbering(doc, "SPECIAL INTERROGATORY NO. %1:")
    dec = kit.add_numbering(doc, "(%1)", fmt="lowerLetter")
    for text in ("State the date.", "", "Identify each witness."):
        kit.number(doc.add_paragraph(text), nid)
    kit.number(doc.add_paragraph("a part"), dec)
    kit.number(doc.add_paragraph("b part"), dec)
    got = _read(doc, tmp_path)
    assert [b.numbered for b in got] == [
        "SPECIAL INTERROGATORY NO. 1:",
        "SPECIAL INTERROGATORY NO. 2:",  # empty text, still a block: Word shows its number
        "SPECIAL INTERROGATORY NO. 3:",
        "(a)",
        "(b)",
    ]
    assert got[0].num_id == nid and got[0].num_level == 0
    assert got[1].text == ""


def test_table_rows_and_textboxes_are_their_own_blocks(tmp_path: Path) -> None:
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Dated: a day"
    t.cell(0, 1).text = "By: A. Example"
    p = doc.add_paragraph("Anchor line")
    kit.textbox(p, ["EXAMPLE LAW GROUP", "Attorneys for Plaintiff"])
    got = _read(doc, tmp_path)
    assert [(b.id, b.text) for b in got] == [
        ("t0.r0", "Dated: a day | By: A. Example"),
        ("p1", "Anchor line"),  # the box's words are not the anchor's
        ("p1.x0", "EXAMPLE LAW GROUP"),
        ("p1.x1", "Attorneys for Plaintiff"),
    ]
    assert got[0].in_table and got[2].in_textbox and not got[1].in_textbox
