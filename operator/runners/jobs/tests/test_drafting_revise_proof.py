"""The revision's proof, held to the leaves an ask names (drafting/revise).

The $0 rehearsal on a real request (2026-11-04) found: a brief saved in Word
could not be revised at all, because the package's content-types listing was
compared in order; an ask laid the caption's rule at a default weight over the
filed one; an ask already met still rewrote its elements; a change inside any
touched element passed the proof; and the notes carried from the filed draft
lost their numbering. Each test here fails on the code before.

Every name, number and sentence is invented (Example / Exampletown), and no
test makes a model call: the doorways are scripted.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree

from jobs.demand import deliver
from jobs.drafting import briefpage
from jobs.drafting.revise import inplace, prior
from test_drafting_revise import BRIEF, KEY, REQUEST
from test_drafting_revise_layout import FULL, V7_KEYS, _format_key, _render, _run, firm_styled, fmt  # noqa: F401 - firm_styled and fmt are pytest fixtures, used by name

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
LEFT = "end the solid line under the caption at the vertical line"
GAP = "more space above the court's name"
SIGN = "single space the signature"
THREE = f"Please {LEFT}. Put {GAP}. And {SIGN}."


def _body(blob: bytes) -> list[bytes]:
    return [etree.tostring(el, method="c14n") for el in docx.Document(io.BytesIO(blob)).element.body]


def _reordered(blob: bytes) -> bytes:
    """The package as Word writes it: the same content types and relationships
    in another order than the one python-docx saves them in."""
    src, out = zipfile.ZipFile(io.BytesIO(blob)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "[Content_Types].xml" or n.endswith(".rels"):
                root = etree.fromstring(data)
                for child in list(root):
                    root.remove(child)
                    root.insert(0, child)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            z.writestr(n, data)
    return out.getvalue()


def _item(iid: str, quote: str, **params: Any) -> dict[str, Any]:
    return {"id": iid, "quote": quote, "kind": "formatting", "target": "document", "params": params}


def _caption(d: Any) -> Any:
    return d.tables[0]


def _bottom(cell: Any) -> Any:
    b = cell._tc.tcPr.find(qn("w:tcBorders")) if cell._tc.tcPr is not None else None
    return b.find(qn("w:bottom")) if b is not None else None


def _rule_filed(path: Path, where: str, sz: str = "12") -> Path:
    """The caption's rule as a brief he saved carries it, at his weight: on
    every cell of the last row, or on the table."""
    d = docx.Document(str(path))
    t = _caption(d)
    bottom = f'<w:bottom {W_NS} w:val="single" w:sz="{sz}" w:space="0" w:color="auto"/>'
    pr = t._tbl.tblPr
    borders = pr.find(qn("w:tblBorders"))
    if borders is not None:
        for old in borders.findall(qn("w:bottom")):
            borders.remove(old)
    if where == "table":
        borders.insert(0, parse_xml(bottom))
    else:
        for c in t.rows[-1].cells:
            tc_pr = c._tc.get_or_add_tcPr()
            for old in tc_pr.findall(qn("w:tcBorders")):
                tc_pr.remove(old)
            tc_pr.append(parse_xml(f"<w:tcBorders {W_NS}>{bottom}</w:tcBorders>"))
    d.save(str(path))
    return path


# ---- N7 ------------------------------------------------------------------------------
def test_n7_a_brief_saved_in_word_can_be_revised(tmp_path, fmt) -> None:
    """FALSIFIER (N7): the listing was compared in order, so every brief the
    attorney had saved in Word stopped as revise_base_unproven."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt)).read_bytes()
    key = {"items": [i for i in KEY["items"] if i["id"] == "c7"]}
    v, seat, _jd = _run(tmp_path, _reordered(filed), key)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent


def test_n7_a_content_type_really_lost_is_still_a_difference(tmp_path, fmt) -> None:
    """The set compare must not become no compare."""
    from jobs.drafting.revise import xmlproof

    blob = _render(BRIEF, tmp_path / "filed.docx", fmt).read_bytes()
    src, out = zipfile.ZipFile(io.BytesIO(blob)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "[Content_Types].xml":
                root = etree.fromstring(data)
                root.remove(root[-1])
                data = etree.tostring(root)
            z.writestr(n, data)
    assert xmlproof.parts_differ(blob, _reordered(blob)) == []
    assert xmlproof.parts_differ(blob, out.getvalue()) == ["[Content_Types].xml"]


# ---- N8 ------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["cells", "table"])
def test_n8_the_filed_rule_keeps_its_weight(tmp_path, fmt, where) -> None:
    """FALSIFIER (N8): the rule was rewritten at a default weight over the 1.5
    point rule the attorney had, though he asked only where it ends."""
    filed = _render(FULL, tmp_path / "filed.docx", fmt, caption_rule_below=True, signature_date_beside=True)
    _rule_filed(filed, where)
    key = {"items": [_item("c1", LEFT, layout={"caption_rule_below": "left"})]}
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, f"Please {LEFT}.")
    assert v.outcome == "delivered", (v.stage, v.reason)
    t = _caption(docx.Document(io.BytesIO(seat.files["draft-0001"][-1])))
    left, right = t.rows[-1].cells[0], t.rows[-1].cells[1]
    assert _bottom(left).get(qn("w:sz")) == "12"
    assert _bottom(right) is None


def _as_he_saved_it(path: Path) -> None:
    """A rule and signature lines that meet their asks, written as Word writes
    them rather than as the renderer does: a heavier rule on the left cell, and
    single spacing with no zero before and after on the signature lines."""
    d = docx.Document(str(path))
    _bottom(_caption(d).rows[-1].cells[0]).set(qn("w:sz"), "12")
    for p in briefpage.signature_lines(d):
        sp = p._p.pPr.find(qn("w:spacing"))
        for attr in ("w:before", "w:after"):
            sp.attrib.pop(qn(attr), None)
    d.save(str(path))


def test_n8_an_ask_already_met_touches_nothing(tmp_path, fmt) -> None:
    """FALSIFIER (N8): two of three asks were already how the filed brief reads,
    and their elements were written anyway."""
    filed = _render(FULL, tmp_path / "filed.docx", fmt, **V7_KEYS)
    _as_he_saved_it(filed)
    key = {
        "items": [
            _item("c1", GAP, layout={"front_court_gap_lines": 3}, relative="more"),
            _item("c2", LEFT, layout={"caption_rule_below": "left"}),
            _item("c3", SIGN, layout={"signature_single_spaced": True}),
        ]
    }
    before = filed.read_bytes()
    v, seat, _jd = _run(tmp_path, before, key, THREE)
    assert v.outcome == "delivered", (v.stage, v.reason)
    a, b = _body(before), _body(seat.files["draft-0001"][-1])
    assert len(b) == len(a) + 1
    new = [i for i in range(len(b)) if b[:i] + b[i + 1 :] == a]
    assert new and b"<w:t" not in b[new[0]], "only the one blank line the ask needs is new"


# ---- N9 ------------------------------------------------------------------------------
def _tampered(monkeypatch: pytest.MonkeyPatch, tamper: Any) -> None:
    real = inplace.edit_layout

    def edit_layout(ed: Any, layout: dict[str, Any]) -> None:
        real(ed, layout)
        if layout:
            tamper(ed)

    monkeypatch.setattr(inplace, "edit_layout", edit_layout)


def _caption_run_size(ed: Any) -> None:
    run = next(r for row in ed.doc.tables[0].rows for c in row.cells for p in c.paragraphs for r in p.runs if r.text)
    run.font.size = Pt(9)


def _caption_cell_shade(ed: Any) -> None:
    c = ed.doc.tables[0].rows[0].cells[0]
    c._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {W_NS} w:val="clear" w:color="auto" w:fill="FFFF00"/>'))


def _rule_on_another_row(ed: Any) -> None:
    c = ed.doc.tables[0].rows[0].cells[0]
    pr = c._tc.get_or_add_tcPr()
    pr.append(parse_xml(f'<w:tcBorders {W_NS}><w:bottom w:val="single" w:sz="8"/></w:tcBorders>'))


def _body_justify_changed(ed: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    body = next(p for p in ed.doc.paragraphs if p.text.strip() and p.style.name.endswith("Body"))
    body.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _style_changed(ed: Any) -> None:
    ed.doc.styles["Normal"].font.size = Pt(9)


def _title_changed(ed: Any) -> None:
    ed.doc.core_properties.title = "changed"


def _a_paragraph_added(ed: Any) -> None:
    ed.doc.element.body[-2].addprevious(parse_xml(f"<w:p {W_NS}><w:r><w:t>new words</w:t></w:r></w:p>"))


@pytest.mark.parametrize(
    "tamper",
    [
        _caption_run_size,
        _caption_cell_shade,
        _rule_on_another_row,
        _body_justify_changed,
        _style_changed,
        _title_changed,
        _a_paragraph_added,
    ],
)
def test_n9_a_change_no_ask_names_stops_even_inside_a_touched_element(tmp_path, fmt, monkeypatch, tamper) -> None:
    """FALSIFIER (N9): a touched element was trusted whole, so a run's size
    inside the caption passed because the ask had touched the caption."""
    filed = _render(FULL, tmp_path / "filed.docx", fmt, caption_rule_below=True, signature_date_beside=True)
    _tampered(monkeypatch, tamper)
    key = {"items": [_item("c1", LEFT, layout={"caption_rule_below": "left"})]}
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, f"Please {LEFT}.")
    assert v.outcome == "failed" and str(v.reason).startswith("revise_layout_lost:"), (v.outcome, v.reason)
    assert seat.sent == []


def test_n9_the_same_ask_untampered_delivers(tmp_path, fmt) -> None:
    """The control beside the tampers: the proof does not refuse the ask itself."""
    filed = _render(FULL, tmp_path / "filed.docx", fmt, caption_rule_below=True, signature_date_beside=True)
    key = {"items": [_item("c1", LEFT, layout={"caption_rule_below": "left"})]}
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, f"Please {LEFT}.")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent


def test_n9_a_content_edit_may_change_a_paragraphs_runs_and_not_its_format(tmp_path, fmt) -> None:
    from jobs.drafting.revise import xmlproof

    path = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt))
    ed = inplace.open_filed(path)
    before = inplace.snapshot(ed)
    md = prior.docx_to_md(path).replace("recognize the strength of her case", "value her case fairly")
    inplace.edit_content(ed, md)
    clean = inplace.save(ed, tmp_path / "clean.docx")
    assert inplace.prove(ed, before, path.read_bytes(), clean, md) == []
    touched = next(iter(ed.touched))
    ppr = touched.get_or_add_pPr()
    ppr.append(parse_xml(f'<w:jc {W_NS} w:val="center"/>'))
    bad = inplace.save(ed, tmp_path / "bad.docx")
    found = inplace.prove(ed, before, path.read_bytes(), bad, md)
    assert any("pPr" in f for f in found), found
    assert xmlproof.leaves(parse_xml(f"<w:p {W_NS}/>"), parse_xml(f"<w:p {W_NS}/>")) == []


# ---- N10 -----------------------------------------------------------------------------
NOTES = (
    "# Attorney notes\n\n## Changes\n\n1. The first change.\n\n2. The second change.\n\n- A bullet.\n\n3. The third.\n"
)


def test_n10_the_carried_notes_keep_their_numbering(tmp_path, fmt) -> None:
    """FALSIFIER (N10): the filed notes' numbered lines came back as plain
    paragraphs under "Notes from the filed draft"."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt)).read_bytes()
    key = {"items": [i for i in KEY["items"] if i["id"] == "c7"]}
    v, seat, _jd = _run(tmp_path, filed, key, REQUEST, notes_md=NOTES)
    assert v.outcome == "delivered", (v.stage, v.reason)
    new = docx.Document(io.BytesIO(seat.files["notes-0001"][-1]))
    numbered = [p.text for p in new.paragraphs if p.style.name == "List Number"]
    assert numbered == ["The first change.", "The second change.", "The third."]
    assert [p.text for p in new.paragraphs if p.style.name == "List Bullet"][-1] == "A bullet."


def test_n10_the_brief_reading_does_not_change(tmp_path) -> None:
    """Notes are numbered on their own reading: a brief's blocks stay as they
    were, so its round trip is not disturbed."""
    path = deliver.render_plain(NOTES, tmp_path, "n.docx", "Example")
    assert "1. The first change." not in prior.docx_to_md(path)
    assert "1. The first change." in prior.notes_md(path)
    assert "3. The third." in prior.notes_md(path)
