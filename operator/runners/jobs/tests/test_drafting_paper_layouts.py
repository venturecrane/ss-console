"""The paper reader on the layouts the first $0 read of real filed papers
surfaced (2026-11-26), each on an invented fixture, and the genuine numbering
defects that must keep failing."""

from __future__ import annotations

from pathlib import Path

import paper_testkit as kit
from docx import Document

from jobs.drafting.paper import blocks, read
from jobs.drafting.paper.model import Paper, Unread


def _read(path: Path) -> Paper | Unread:
    return read.read(blocks.read_docx(path))


def _paper(path: Path) -> Paper:
    got = _read(path)
    assert isinstance(got, Paper), got
    return got


def _unread(path: Path) -> Unread:
    got = _read(path)
    assert isinstance(got, Unread), "expected the reader to fail closed"
    return got


def _save(doc, path: Path) -> Path:
    doc.save(str(path))
    return path


# 1. The prior-sets statement counts earlier sets, never this one.


def test_prior_sets_sentence_is_prior_count_not_this_sets_count(tmp_path: Path) -> None:
    for form, prior in (("one", [12, 12]), ("two", [12, 10])):
        p = _paper(kit.set_docx(tmp_path / f"{form}.docx", kit.SetSpec(prior=form)))
        assert p.declaration is not None
        assert [c.value for c in p.declaration.count_mentions] == [40, 40, 40]
        assert [c.value for c in p.declaration.prior_counts] == prior


# 2. A possessive apostrophe inside a term never closes it.


def test_term_with_a_possessive_apostrophe_is_whole(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE", "ONE")
    kit.bold(doc, "DEFINITIONS")
    doc.add_paragraph('"PLAINTIFF\'S VEHICLE" means the invented car.')
    doc.add_paragraph("“DEFENDANT’S PREMISES,” means the invented store.")
    doc.add_paragraph('"OWNER\'S RECORD" or "OWNER\'S RECORDS" means any invented log.')
    doc.add_paragraph("SPECIAL INTERROGATORY NO. 1: State invented fact 1.")
    doc.add_paragraph("Dated: a day")
    p = _paper(_save(doc, tmp_path / "x.docx"))
    assert [d.term for d in p.definitions] == ["PLAINTIFF'S VEHICLE", "DEFENDANT’S PREMISES", "OWNER'S RECORD"]
    assert p.definitions[1].punct_inside and p.definitions[1].quote == "“”"
    assert p.definitions[2].plural == "OWNER'S RECORDS"


# 3. A plain "Notes for the Attorney" page after the signature is the notes region.


def test_plain_notes_page_after_the_signature(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "n.docx", kit.SetSpec(n=12, declaration=False, notes=True)))
    assert len(p.notes.ids) == 2 and len(p.items) == 12
    assert all(i not in p.closing.ids for i in p.notes.ids)


def test_notes_page_is_not_swallowed_into_the_declaration(tmp_path: Path) -> None:
    p = _paper(kit.set_docx(tmp_path / "n.docx", kit.SetSpec(notes=True)))
    assert p.declaration is not None and len(p.notes.ids) == 2
    assert not set(p.notes.ids) & set(p.declaration.ids)
    assert [c.value for c in p.declaration.count_mentions] == [40, 40, 40]  # the note's "3 more" is not one


# 4. An index of exhibits filed alone.


def test_exhibit_index_filed_alone(tmp_path: Path) -> None:
    doc = Document()
    doc.add_paragraph("Alex Example, Esq. (SBN 000001)")
    doc.add_paragraph("Index of Exhibits")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Exhibit 1", "Invented repair record"
    t.cell(1, 0).text, t.cell(1, 1).text = "Exhibit 2", "Invented photograph"
    p = _paper(_save(doc, tmp_path / "idx.docx"))
    assert p.type == "index_of_exhibits" and p.items == []
    assert len(p.caption.ids) == 1 and len(p.index.ids) == 3


# 5. Amended and supplemental papers answer only the challenged items.


def test_amended_responses_rise_without_starting_at_one(tmp_path: Path) -> None:
    title = "DEFENDANT'S AMENDED RESPONSES TO SPECIAL INTERROGATORIES, SET ONE"
    p = _paper(kit.response_docx(tmp_path / "a.docx", numbers=[2, 5, 9], title=title))
    assert [it.number for it in p.items] == [2, 5, 9]
    sup = title.replace("AMENDED", "SUPPLEMENTAL")
    assert [it.number for it in _paper(kit.response_docx(tmp_path / "s.docx", numbers=[3], title=sup)).items] == [3]


def test_amended_repeat_or_fall_and_unamended_gap_stay_unread(tmp_path: Path) -> None:
    title = "DEFENDANT'S AMENDED RESPONSES TO SPECIAL INTERROGATORIES, SET ONE"
    assert "a repeat or a fall" in _unread(kit.response_docx(tmp_path / "r.docx", numbers=[2, 5, 5], title=title)).why
    assert "a repeat or a fall" in _unread(kit.response_docx(tmp_path / "f.docx", numbers=[2, 9, 5], title=title)).why
    assert "a gap" in _unread(kit.response_docx(tmp_path / "g.docx", numbers=[1, 2, 5])).why


def test_genuine_defects_keep_failing(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE", "ONE")
    for k in (1, 2, 2, 3):
        doc.add_paragraph(f"SPECIAL INTERROGATORY NO. {k}: State invented fact {k}.")
    doc.add_paragraph("Dated: a day")
    assert "item 2 follows item 2: a repeat" in _unread(_save(doc, tmp_path / "dup.docx")).why
    off = _unread(kit.response_docx(tmp_path / "off.docx", n=4, wrong_head=5))
    assert "response heading No. 5 under item 4" in off.why


# 6. The type: a title in a table cell, a generic label, a conflict.


def _caption_table_title(doc, title: str) -> None:
    doc.add_paragraph("Alex Example, Esq. (SBN 000001)")
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Pat Sample, Plaintiff, v. River Co., Defendant."
    t.cell(0, 1).text = f"Case No. 00CV000000 {title}"
    doc.add_paragraph("PROPOUNDING PARTY: Plaintiff Pat Sample")
    doc.add_paragraph("SET NO.: ONE")


def test_title_in_a_table_cell_and_identification_and_production(tmp_path: Path) -> None:
    doc = Document()
    _caption_table_title(doc, "Plaintiff's Request for Identification and Production of Documents, Set One")
    for k in (1, 2):
        doc.add_paragraph(f"REQUEST NO. {k}: Each invented record of type {k}.")
    doc.add_paragraph("Dated: a day")
    p = _paper(_save(doc, tmp_path / "rfp.docx"))
    assert p.type == "requests_for_production" and len(p.title.ids) == 1 and p.title.ids[0].startswith("t")


def test_title_and_labels_disagreeing_is_a_type_conflict(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE", "ONE")
    for k in (1, 2):
        doc.add_paragraph(f"REQUEST FOR ADMISSION NO. {k}: Admit invented fact {k}.")
    doc.add_paragraph("Dated: a day")
    u = _unread(_save(doc, tmp_path / "c.docx"))
    assert u.region == "type" and u.why.startswith("type_conflict")


# 7. "DEMAND NO." is a production label.


def test_demand_labels_are_production(tmp_path: Path) -> None:
    title = "DEFENDANT'S RESPONSES TO DEMAND FOR PRODUCTION, SET ONE"
    p = _paper(kit.response_docx(tmp_path / "d.docx", title=title, label="DEMAND"))
    assert p.type == "responses_to_requests_for_production"
    assert [it.number for it in p.items] == [1, 2, 3] and all(it.response_head for it in p.items)


# Positive control: reason lists as ranges and written out, "No." and "Nos.".


def test_reason_lists_ranges_and_written_out(tmp_path: Path) -> None:
    doc = Document()
    kit.caption(doc, "PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE", "ONE")
    for k in range(1, 41):
        doc.add_paragraph(f"SPECIAL INTERROGATORY NO. {k}: State invented fact {k}.")
    doc.add_paragraph("Dated: a day")
    kit.bold(doc, "DECLARATION FOR ADDITIONAL DISCOVERY")
    doc.add_paragraph("I am propounding a total of 40 special interrogatories.")
    doc.add_paragraph("(A) The complexity of the issues: Nos. 1-14, 16, 17.")
    doc.add_paragraph("(B) The financial burden: No. 15 asks about it.")
    doc.add_paragraph("(E) The need for discovery: Nos. 18 through 39 and 40.")
    p = _paper(_save(doc, tmp_path / "rl.docx"))
    assert p.declaration is not None
    got = {r.reason: (r.noun, r.numbers) for r in p.declaration.reason_lists}
    assert got["A"] == ("Nos.", [*range(1, 15), 16, 17])
    assert got["B"] == ("No.", [15])
    assert got["E"] == ("Nos.", list(range(18, 41)))
