"""A case memo's first draft in the firm's own Word file. Every fixture is synthetic: a
memo laid by hand the way a firm's own is, direct formatting and no heading styles,
with a picture, a header, a footer and a document system's properties that must not
travel."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

import docx
import pytest
import yaml
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from lxml import etree

from drafting_testkit import ScriptedClient, firm_data, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs import firmbase
from jobs.drafting import brief_base, firm, format_check, memo_base
from jobs.drafting.outcome import DraftingFailed
from jobs.drafting.run import DraftingRun
from jobs.firmbase.base import DOCUMENT, W, package

CLS = "memo"


def _png() -> bytes:
    """A one-pixel picture."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    head = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b"")
    )


MEMO = """**MEMORANDUM**

| | |
| --- | --- |
| TO: | A Requesting Attorney |
| FROM: | The Operator, for the firm |
| DATE: | January 2, 2027 |
| RE: | An Example; 000000; a question |

**PRIVILEGED AND CONFIDENTIAL.**

# I. QUESTION AND SHORT ANSWER

The question. {{NOT IN RECORD: a thing, where looked}}

DRAFT ASSESSMENT: **an answer** with *emphasis*.

## A. An Issue

The record.

- A bullet
- Another bullet

1. A numbered point

## B. Another Issue

| Name | Role | Shows | Cite |
| --- | --- | --- | --- |
| A Person | A role | A thing | A cite |
| Another | A role | A thing | A cite |

| Date | Event |
| --- | --- |
| 1/1/26 | An event |

| Date | Source | Event | Cite | Note | More |
| --- | --- | --- | --- | --- | --- |
| 1/1/26 | S | E | C | N | M |

# II. RECOMMENDATION

The end.
"""


def _run(p, text, size=None, bold=False, color=None):
    r = p.add_run(text)
    r.bold = bold or None
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = RGBColor.from_string(color)
    return r


def _firm_memo(split_headings: bool = True, font: str = "Times New Roman", size: int = 12) -> bytes:
    d = docx.Document()
    d.core_properties.author = "A Firm Person"
    d.core_properties.title = "Other Client memo"
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = font, Pt(size)
    d.sections[0].header.paragraphs[0].text = "Other Client, matter 999"
    d.sections[0].footer.paragraphs[0].text = "Page 1 of 2"

    title = d.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(title, "OTHER CLIENT MEMO", 22, True, "FFFFFF")
    _run(d.add_paragraph(), "Privileged notice about Other Client", bold=True)
    _run(d.add_paragraph(), "To the file, about Other Client", 10)
    _run(d.add_paragraph(), "Date of the other memo", 10)

    def h1(n, text):
        if split_headings:
            p = d.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(p, n, 16, True, "0B4EA2")
            q = d.add_paragraph()
            q.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(q, text, 16, True, "0B4EA2")
        else:
            _run(d.add_paragraph(), f"{n} {text}", 16, True, "0B4EA2")

    def h2(text):
        p = d.add_paragraph()
        p.paragraph_format.left_indent = Pt(18)
        _run(p, text, 13, True).underline = True

    def body(text="Other Client's plain body paragraph with facts."):
        p = d.add_paragraph()
        p.paragraph_format.space_after = Pt(9)
        _run(p, text)

    h1("I.", "OTHER QUESTION")
    body()
    body()
    h2("A. Other Issue")
    body()
    h2("B. Next Issue")
    body()
    h2("C. Third Issue")
    d.add_paragraph("A listed item about Other Client", style="List Bullet")
    t = d.add_table(rows=5, cols=4)
    t.style = "Table Grid"
    for r, row in enumerate(t.rows):
        for c in row.cells:
            _run(c.paragraphs[0], f"Other {r}", 10, r == 0)
    d.add_paragraph("")
    h1("II.", "OTHER CONCLUSION")
    body()
    d.add_picture(io.BytesIO(_png()))
    body("Signed by a firm person for Other Client.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _source(blob: bytes) -> firmbase.Source:
    return firmbase.Source("m", "f", "A firm memo.docx", hashlib.sha256(blob).hexdigest())


def _base(blob: bytes | None = None) -> firmbase.FirmBase:
    blob = blob if blob is not None else _firm_memo()
    made, _notes = memo_base.adopt(blob, _source(blob))
    return firmbase.FirmBase.load(CLS, _source(blob), made)


def _fmt() -> dict:
    return copy.deepcopy(firm_data()["format"])


def _all_text(blob: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(blob))
    return " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith((".xml", ".rels")))


def _words(blob: bytes, part: str = DOCUMENT) -> str:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(part))
    return " ".join(t.text or "" for t in root.iter(f"{W}t"))


def test_no_word_picture_or_property_of_the_other_matter_travels():
    source = _firm_memo()
    assert "Other Client" in _all_text(source) and any(
        "media/" in n for n in zipfile.ZipFile(io.BytesIO(source)).namelist()
    )
    base = _base(source)
    assert "Other" not in _all_text(base.bytes)
    assert not [n for n in zipfile.ZipFile(io.BytesIO(base.bytes)).namelist() if "media/" in n]
    assert (
        re.search(
            r'Type="[^"]*/image"', zipfile.ZipFile(io.BytesIO(base.bytes)).read("word/_rels/document.xml.rels").decode()
        )
        is None
    )


def test_a_page_number_line_stays():
    assert _words(_base().bytes, "word/footer1.xml").split() == ["Page", "1", "of", "2"]


def test_the_slots_read_the_firms_design():
    assert {"front.title", "front.notice", "front.meta", "body", "body.h1", "body.h1t", "body.h2"} <= set(_base().slots)
    assert {"body.p", "body.item", "body.chart4"} <= set(_base().slots)


def test_a_heading_on_one_line_stays_on_one_line():
    base = _base(_firm_memo(split_headings=False))
    assert "body.h1t" not in base.slots
    out, proof = memo_base.fill(base, MEMO)
    proof.require()
    assert "I. QUESTION AND SHORT ANSWER" in _words(out)


def test_a_bold_label_stays_bold_and_with_no_notice_line_the_notice_goes_last():
    d = docx.Document(io.BytesIO(_firm_memo()))
    for r in d.paragraphs[1].runs:
        r.bold = None
    label = d.paragraphs[1].runs[0]
    label.text = "To:"
    label.bold = True
    d.paragraphs[1].add_run(" the file")
    buf = io.BytesIO()
    d.save(buf)
    base = _base(buf.getvalue())
    assert "front.notice" not in base.slots
    out, proof = memo_base.fill(base, MEMO)
    proof.require()
    doc = docx.Document(io.BytesIO(out))
    line = next(p for p in doc.paragraphs if p.text.startswith("RE:"))
    assert [r.text for r in line.runs if r.bold] == ["RE:"]
    assert doc.paragraphs[5].text == "PRIVILEGED AND CONFIDENTIAL."


def test_something_other_than_a_line_in_the_body_is_read_past():
    d = docx.Document(io.BytesIO(_firm_memo()))
    body = d.element.body
    for at in (0, len(body) // 2):
        body.insert(at, etree.fromstring(f'<w:bookmarkStart xmlns:w="{W[1:-1]}" w:id="9" w:name="x"/>'))
    buf = io.BytesIO()
    d.save(buf)
    memo_base.fill(_base(buf.getvalue()), MEMO)[1].require()


def test_c_after_b_is_a_letter_and_alone_is_a_numeral():
    base = _base()
    out, proof = memo_base.fill(base, MEMO)
    proof.require()
    doc = docx.Document(io.BytesIO(out))
    sizes = {
        p.text: p.runs[0].font.size.pt
        for p in doc.paragraphs
        if p.text in ("I.", "QUESTION AND SHORT ANSWER", "A. An Issue")
    }
    assert sizes == {"I.": 16, "QUESTION AND SHORT ANSWER": 16, "A. An Issue": 13}


def test_the_memo_on_the_base_has_an_empty_proof_and_passes_the_format_check(tmp_path):
    base = _base()
    out, proof = memo_base.fill(base, MEMO)
    assert proof.lost == []
    path = tmp_path / "memo.docx"
    path.write_bytes(out)
    res = format_check.check(path, CLS, memo_base.checked_format(_fmt(), CLS, base), "", None, "plain")
    assert res.fails == []


def test_every_part_but_the_document_is_the_bases_bytes():
    base = _base()
    out, _ = memo_base.fill(base, MEMO)
    a, b = zipfile.ZipFile(io.BytesIO(base.bytes)), zipfile.ZipFile(io.BytesIO(out))
    assert [n for n in a.namelist() if n != DOCUMENT and a.read(n) != b.read(n)] == []


def test_the_look_stays_the_slots_the_words_ours():
    out, _ = memo_base.fill(_base(), MEMO)
    doc = docx.Document(io.BytesIO(out))
    title = doc.paragraphs[0]
    assert title.text == "MEMORANDUM" and title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].font.size == Pt(22) and title.runs[0].font.color.rgb == RGBColor.from_string("FFFFFF")
    assert doc.paragraphs[1].text == "PRIVILEGED AND CONFIDENTIAL."
    texts = [p.text for p in doc.paragraphs]
    assert {"TO: A Requesting Attorney", "RE: An Example; 000000; a question"} <= set(texts)
    assert "{{NOT IN RECORD: a thing, where looked}}" in " ".join(texts)
    emphasised = next(p for p in doc.paragraphs if p.text.startswith("DRAFT ASSESSMENT"))
    assert [r.text for r in emphasised.runs if r.bold] == ["an answer"]


def test_bullets_take_the_firms_list_and_a_number_keeps_its_label():
    doc = docx.Document(io.BytesIO(memo_base.fill(_base(), MEMO)[0]))
    bullet = next(p for p in doc.paragraphs if p.text == "A bullet")
    assert bullet._p.find(f"{W}pPr/{W}pStyle") is not None or bullet._p.find(f"{W}pPr/{W}numPr") is not None
    assert any(p.text == "1. A numbered point" for p in doc.paragraphs)


@pytest.mark.parametrize("columns", [(4, 0), (2, 1), (6, 2)])
def test_a_table_of_any_width_holds_the_charts_width(columns):
    n, at = columns
    doc = docx.Document(io.BytesIO(memo_base.fill(_base(), MEMO)[0]))
    t = doc.tables[at]
    assert len(t.columns) == n
    base_doc = docx.Document(io.BytesIO(_base().bytes))
    width = lambda tbl: sum(int(g.get(f"{W}w")) for g in tbl._tbl.find(f"{W}tblGrid"))  # noqa: E731
    assert width(t) == width(base_doc.tables[0])


@pytest.mark.parametrize(
    "md, why",
    [
        ("**MEMORANDUM**\n\nno heading", "no heading"),
        ("# I. A\n\nx", "no title"),
        ("**MEMORANDUM**\n\nTO: x\n\n# I. A\n\nx", "no notice"),
        ("**MEMORANDUM**\n\n**NOTICE**\n\n# I. A\n\nx", "no memo lines"),
    ],
)
def test_what_the_base_cannot_place_stops_the_job(md, why):
    with pytest.raises(DraftingFailed, match=why):
        memo_base.fill(_base(), md)


def test_a_changed_line_outside_the_slots_is_lost():
    base = _base()
    out, _ = memo_base.fill(base, MEMO)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read(DOCUMENT))
    next(root.iter(f"{W}jc")).set(f"{W}val", "right")
    assert firmbase.prove(base, package(out, root)).lost


def test_words_the_draft_never_had_are_refused(monkeypatch):
    real = memo_base._read_back
    monkeypatch.setattr(memo_base, "_read_back", lambda b: real(b) + memo_base.Counter({"INVENTED": 1}))
    assert "the file does not read as the draft" in memo_base.fill(_base(), MEMO)[1].lost


def test_a_refused_file_is_never_written(tmp_path, monkeypatch):
    real = memo_base.fill

    def lost(base, md):
        blob, proof = real(base, md)
        proof.leaves_outside_allow.append("x")
        return blob, proof

    out = tmp_path / "memo.docx"
    with pytest.raises(DraftingFailed, match="firm_base_lost"):
        brief_base.render_into(MEMO, out, _base(), "plain", lost)
    assert not out.exists()


def test_an_operator_render_nobody_accepted_is_refused():
    blob = _firm_memo()
    z = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for i in z.infolist():
            data = z.read(i.filename)
            if i.filename == "docProps/core.xml":
                data = re.sub(rb"(<dc:creator>)[^<]*", rb"\1SMD Operator", data)
            out.writestr(i, data)
    with pytest.raises(DraftingFailed, match="Operator's own"):
        memo_base.adopt(buf.getvalue(), _source(buf.getvalue()))


def test_a_memo_with_no_chart_or_heading_is_no_base():
    d = docx.Document()
    d.core_properties.author = "A Firm Person"
    d.add_paragraph("Just words")
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(DraftingFailed, match="no heading"):
        memo_base.adopt(buf.getvalue(), _source(buf.getvalue()))
    _run(d.add_paragraph(), "I. A HEADING", bold=True)
    d.add_paragraph("A body")
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(DraftingFailed, match="no chart"):
        memo_base.adopt(buf.getvalue(), _source(buf.getvalue()))


def _check(base: firmbase.FirmBase, tmp_path: Path, fmt: dict | None = None) -> list[str]:
    path = tmp_path / "memo.docx"
    path.write_bytes(memo_base.fill(base, MEMO)[0])
    return format_check.check(path, CLS, memo_base.checked_format(fmt or _fmt(), CLS, base), "", None, "plain").fails


def test_another_font_or_body_size_fails_the_format_check(tmp_path):
    assert any("are not Times New Roman" in x for x in _check(_base(_firm_memo(font="Garamond")), tmp_path))
    assert any("body is not" in x for x in _check(_base(_firm_memo(size=11)), tmp_path))


def test_without_a_base_the_check_still_reads_every_run(tmp_path):
    from jobs.drafting import render

    path, _ = render.render(MEMO, CLS, tmp_path / "plain.docx", _fmt())
    assert format_check.check(path, CLS, _fmt(), "", None, "plain").fails == []


def test_the_wrong_paper_is_refused(tmp_path):
    with pytest.raises(DraftingFailed, match="pleading paper"):
        brief_base.render_into(MEMO, tmp_path / "m.docx", _base(), "pleading", memo_base.fill)


def test_the_config_takes_a_memo_base_and_no_other_class():
    source = {"matter_id": "m", "file_id": "f", "name": "n.docx", "sha256": "0" * 64}
    data = firm_data()
    data["format"]["firm_bases"] = {CLS: {"path": "x.docx", "source": source}}
    assert firm._firm_bases(data["format"]["firm_bases"]) == []
    assert "only" in firm._firm_bases({"depo_outline": {"path": "x.docx", "source": source}})[0]


def test_the_job_files_the_memo_it_made_in_the_firms_file(tmp_path, monkeypatch):
    checker = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(checker))
    base = _base()
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    make_inputs(inputs)
    (inputs / "memo-base.docx").write_bytes(base.bytes)
    data = yaml.safe_load((inputs / "drafting-firm.yaml").read_text())
    source = {"matter_id": "m", "file_id": "f", "name": "n.docx", "sha256": "0" * 64}
    data["format"]["firm_bases"] = {CLS: {"path": "memo-base.docx", "source": source}}
    data["inputs"]["memo-base.docx"] = hashlib.sha256(base.bytes).hexdigest()
    (inputs / "drafting-firm.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    log: list[str] = []
    r = DraftingRun(
        make_job(tmp_path / "job", cls=CLS),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=ScriptedClient(draft=MEMO),
        log=log.append,
        readback_pause=0.0,
    )
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    rendered = json.loads((r.data / "rendered.json").read_text())
    assert any("firm's base" in n for n in rendered["render_report"])
    filed = Path(rendered["path"]).read_bytes()
    a, b = zipfile.ZipFile(io.BytesIO(base.bytes)), zipfile.ZipFile(io.BytesIO(filed))
    assert [n for n in a.namelist() if n != DOCUMENT and a.read(n) != b.read(n)] == []
