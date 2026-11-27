"""The paper reader on invented sets and responses laid out as the first firm
lays out its filed papers, and on the risk inputs: each reads to the same
Paper, or fails closed with an Unread naming the region, block and reason."""

from __future__ import annotations

from pathlib import Path

import paper_testkit as kit
import pytest
from docx import Document

from jobs.drafting.paper import blocks, read
from jobs.drafting.paper.model import Paper, Unread


def _paper(path: Path, profile: dict[str, str] | None = None) -> Paper:
    got = read.read(blocks.read_docx(path), profile)
    assert isinstance(got, Paper), got
    return got


def _unread(path: Path) -> Unread:
    got = read.read(blocks.read_docx(path))
    assert isinstance(got, Unread), "expected the reader to fail closed"
    return got


def test_filed_set_reads_every_region(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "set.docx"))
    assert p.type == "special_interrogatories"
    assert [it.number for it in p.items] == list(range(1, 41))
    assert p.items[0].label == "SPECIAL INTERROGATORY"
    assert len(p.items[0].request) == 2  # the label line and the request
    assert p.detectors == {"title": "bold", "items": "label"}
    ids = [bid for bid, _ in p.assignment()]
    assert sorted(ids) == sorted(b.id for b in p.blocks)  # every block, exactly once
    assert len(p.caption.ids) == 4 and len(p.title.ids) == 1 and len(p.ident.ids) == 3


def test_definitions_unnumbered_under_a_bold_title(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "set.docx"))
    by_term = {d.term: d for d in p.definitions}
    assert list(by_term) == ["YOU", "DOCUMENT", "INCIDENT", "PERSON"]
    you, doc, incident, person = by_term.values()
    assert you.quote == "“”" and you.punct_inside and you.number is None  # “YOU,”
    assert doc.plural == "DOCUMENTS" and doc.quote == '"' and len(doc.ids) == 2  # its continuation paragraph
    assert incident.punct_inside  # “INCIDENT.”
    assert person.plural == "PERSONS"  # "PERSON(S)"
    # the title and the introduction stay in the region, in no definition
    assert len(p.definitions_region.ids) == 7


def test_declaration_counts_and_reason_lists(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "set.docx"))
    d = p.declaration
    assert d is not None
    # stated in prose twice and beside the statute cite; the 2030.030 limit cite is not a count
    assert [c.value for c in d.count_mentions] == [40, 40, 40]
    lists = {r.reason: r for r in d.reason_lists}
    assert lists["A"].numbers == list(range(1, 21)) and lists["A"].noun == "Nos."
    assert lists["C"].numbers == [1] and lists["C"].noun == "No."
    assert lists["E"].numbers == list(range(21, 41))
    assert "B" not in lists


@pytest.mark.parametrize("mode,detector", [("label", "numbered_label"), ("decimal", "numbered")])
def test_items_numbered_by_word_alone(tmp_path: Path, mode: str, detector: str) -> None:
    p = _paper(kit.set_docx(tmp_path / "auto.docx", kit.SetSpec(auto_number=mode)))
    assert [it.number for it in p.items] == list(range(1, 41))
    assert p.detectors["items"] == detector
    assert p.declaration is not None and [c.value for c in p.declaration.count_mentions] == [40, 40, 40]


def test_word_numbered_definitions_do_not_become_items(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE", "ONE")
    defs = kit.add_numbering(doc, "%1.")
    doc.add_paragraph().add_run("DEFINITIONS").bold = True
    kit.number(doc.add_paragraph('"YOU" means Defendant.'), defs)
    kit.number(doc.add_paragraph('"VEHICLE" means the invented car.'), defs)
    items = kit.add_numbering(doc, "%1.")
    for k in range(1, 4):
        kit.number(doc.add_paragraph(f"State invented fact {k}."), items)
    doc.add_paragraph("Dated: a day")
    doc.save(str(tmp_path / "x.docx"))
    p = _paper(tmp_path / "x.docx")
    assert [d.term for d in p.definitions] == ["YOU", "VEHICLE"]
    assert [d.number for d in p.definitions] == [1, 2]
    assert [it.number for it in p.items] == [1, 2, 3]


def test_signature_in_a_text_box_is_the_closing(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "tb.docx", kit.SetSpec(signature="textbox")))
    assert len(p.items) == 40
    assert any(".x" in i for i in p.closing.ids)
    assert all(".x" not in i for it in p.items for i in it.ids)


def test_signature_table_row_after_the_last_request(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "t.docx"))
    assert p.closing.ids and all(i.startswith("t") for i in p.closing.ids)
    assert not any(i.startswith("t") for i in p.items[-1].ids)


def test_declaration_missing_is_fine(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "nd.docx", kit.SetSpec(n=12, declaration=False)))
    assert p.declaration is None and len(p.items) == 12


def test_gap_in_numbering_is_unread(tmp_path: Path) -> None:
    u = _unread(kit.set_docx(tmp_path / "gap.docx", kit.SetSpec(skip=(17,))))
    assert u.region == "items" and "item 18 follows item 16: a gap" in u.why and u.block_id


def test_declaration_count_not_the_items_is_unread(tmp_path: Path) -> None:
    u = _unread(kit.set_docx(tmp_path / "c.docx", kit.SetSpec(decl_count=38)))
    assert u.region == "declaration" and "states 38; the set has 40" in u.why


def test_declaration_list_outside_the_set_is_unread(tmp_path: Path) -> None:
    u = _unread(kit.set_docx(tmp_path / "e.docx", kit.SetSpec(decl_extra=44)))
    assert u.region == "declaration" and "(C) list names [44]" in u.why


def test_unassigned_block_is_unread(tmp_path: Path) -> None:
    u = _unread(kit.set_docx(tmp_path / "s.docx", kit.SetSpec(stray_after_signature=True)))
    assert u.region == "closing" and "belongs to no region" in u.why and u.block_id


def test_later_set_may_continue_numbering_but_set_one_may_not(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET TWO", "TWO")
    for k in (41, 42):
        doc.add_paragraph(f"SPECIAL INTERROGATORY NO. {k}: State invented fact {k}.")
    doc.add_paragraph("Dated: a day")
    doc.save(str(tmp_path / "two.docx"))
    assert [it.number for it in _paper(tmp_path / "two.docx").items] == [41, 42]
    doc.paragraphs[-4].text = "SET NO.: ONE"
    doc.save(str(tmp_path / "one.docx"))
    assert "numbered 41, not 1" in _unread(tmp_path / "one.docx").why


def test_responses_split_request_objections_answer(tmp_path: Path) -> None:
    p = _paper(kit.response_docx(tmp_path / "r.docx"))
    assert p.type == "responses_to_special_interrogatories"
    assert [it.number for it in p.items] == [1, 2, 3]
    first = p.items[0]
    assert len(first.request) == 2 and first.response_head is not None
    assert len(first.objections) == 1 and len(first.answer) == 2 and len(first.cites) == 1
    assert len(p.front.ids) == 2 and len(p.verification.ids) == 2 and len(p.closing.ids) == 2


def test_response_heading_naming_another_item_is_unread(tmp_path: Path) -> None:
    u = _unread(kit.response_docx(tmp_path / "r.docx", wrong_head=9))
    assert "response heading No. 9 under item 3" in u.why


def test_profile_pins_a_detector(tmp_path: Path) -> None:
    path = kit.set_docx(tmp_path / "set.docx", kit.SetSpec(n=5, declaration=False))
    assert _paper(path, {"items": "label", "title": "bold"}).detectors == {"title": "bold", "items": "label"}
    pinned = read.read(blocks.read_docx(path), {"items": "numbered"})
    assert isinstance(pinned, Unread) and "no items found by the numbered detector" in pinned.why
