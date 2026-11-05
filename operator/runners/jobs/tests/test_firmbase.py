"""firmbase: a document made in a firm's own Word file, every fixture synthetic and
marked by hand, so nothing here knows what a mediation brief is."""

from __future__ import annotations

import hashlib
import io
import zipfile

import docx
import pytest
from docx.enum.text import WD_ALIGN_PARAGRAPH
from lxml import etree

from jobs import firmbase
from jobs.firmbase import FirmBase, FirmBaseLost, FirmBaseUnfit, Rows, Source
from jobs.firmbase.base import DOCUMENT, W, Run, package

SOURCE = Source("m-1", "f-1", "A firm form.docx", "0" * 64)


def _file(core_creator: str = "A Person") -> bytes:
    d = docx.Document()
    d.core_properties.author = core_creator
    d.core_properties.title = "A Matter's Title"
    d.sections[0].footer.paragraphs[0].text = "The firm's footer"
    d.add_paragraph("The firm's own fixed line")
    d.add_paragraph("[[who]]")
    d.add_paragraph("")
    d.add_paragraph("Re: {{matter_name}}, dated {{when}}")
    d.add_paragraph("[[body.h]]").paragraph_format.left_indent = 914400
    d.add_paragraph("[[body.p]]").paragraph_format.first_line_indent = 457200
    t = d.add_table(rows=4, cols=2)
    for tr in t.rows:
        for c in tr.cells:
            c.paragraphs[0].text = "[[body.t]]"
        tr.cells[-1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("")
    d.add_paragraph("[[sign]]")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def base() -> FirmBase:
    return FirmBase.load("a_class", SOURCE, _file())


def _runs(text: str, **kw) -> tuple[Run, ...]:
    return (Run(text, **kw),)


def _rows() -> Rows:
    rows = (
        (_runs("Head"), _runs("Amt")),
        (_runs("a"), _runs("$1")),
        (_runs("b"), _runs("$2")),
        (_runs("c"), _runs("$3")),
        (_runs("Total"), _runs("$6")),
    )
    return Rows(rows, True, lambda r: r[0][0].text == "Total")


def _complete(fill) -> None:
    fill.put("who", [_runs("One"), _runs("Two")])
    fill.put("matter_name", "Example")
    fill.put("when", "today")
    fill.put(
        "body",
        [("body.h", _runs("A")), ("body.p", _runs("Text", bold=True)), ("body.t", _rows()), ("body.p", _runs("More"))],
    )
    fill.put("sign", [_runs("Signed")])


def _texts(blob: bytes) -> list[str]:
    return [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs]


def test_the_base_reads_each_kind_of_slot(base):
    kinds = {n: s.kind for n, s in base.slots.items()}
    assert kinds["who"] == "para_proto" and kinds["matter_name"] == "field" and kinds["when"] == "field"
    assert kinds["body"] == "region" and kinds["body.t"] == "table_proto" and kinds["body.h"] == "para_proto"


def test_a_file_with_no_slots_is_no_base():
    d = docx.Document()
    d.add_paragraph("Nothing marked")
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(FirmBaseUnfit):
        FirmBase.load("a_class", SOURCE, buf.getvalue())


def test_a_complete_fill_proves_and_keeps_every_other_part_by_its_bytes(base):
    f = firmbase.open(base)
    _complete(f)
    blob, proof = f.save()
    assert proof.lost == [], proof
    proof.require()
    assert {"who", "body", "body.t", "matter_name"} <= set(proof.slots_filled)
    a, b = zipfile.ZipFile(io.BytesIO(base.bytes)), zipfile.ZipFile(io.BytesIO(blob))
    assert a.namelist() == b.namelist()
    assert [n for n in a.namelist() if a.read(n) != b.read(n)] == [DOCUMENT]
    texts = _texts(blob)
    assert texts[:3] == ["The firm's own fixed line", "One", "Two"]
    assert "Re: Example, dated today" in texts and "Signed" in texts


def test_the_regions_clones_keep_their_protos_layout_and_order(base):
    f = firmbase.open(base)
    _complete(f)
    blob, _ = f.save()
    d = docx.Document(io.BytesIO(blob))
    by = {p.text: p for p in d.paragraphs}
    assert by["A"].paragraph_format.left_indent == 914400
    assert by["Text"].paragraph_format.first_line_indent == 457200 and by["Text"].runs[0].bold
    assert by["More"].paragraph_format.first_line_indent == 457200
    texts = [p.text for p in d.paragraphs]
    assert texts.index("A") < texts.index("Text") < texts.index("More")
    assert [r.cells[0].text for r in d.tables[0].rows] == ["Head", "a", "b", "c", "Total"]


def test_a_slot_no_put_reached_is_reported_and_fails_closed(base):
    f = firmbase.open(base)
    f.put("who", [_runs("One")])
    blob, proof = f.save()
    assert {"when", "matter_name", "body", "sign"} <= set(proof.slots_unfilled)
    with pytest.raises(FirmBaseLost, match="firm_base_lost"):
        proof.require()
    assert blob  # the bytes exist; the caller files nothing


def test_a_slot_the_base_lacks_or_a_member_it_lacks_is_unfit(base):
    f = firmbase.open(base)
    with pytest.raises(FirmBaseUnfit, match="no slot nothing"):
        f.put("nothing", [])
    with pytest.raises(FirmBaseUnfit, match="body.x"):
        f.put("body", [("body.x", _runs("A"))])


def test_the_table_proto_goes_through_its_region(base):
    with pytest.raises(FirmBaseUnfit, match="table proto"):
        firmbase.open(base).put("body.t", _rows())


def test_a_wider_table_holds_its_width_and_right_aligns_only_its_money(base):
    rows = (
        (_runs("A"), _runs("B"), _runs("C")),
        (_runs("x"), _runs("$1"), _runs("y")),
    )
    g = firmbase.open(base)
    g.put("who", [_runs("One")])
    g.put("matter_name", "E")
    g.put("when", "t")
    g.put("body", [("body.t", Rows(rows, True))])
    g.put("sign", [_runs("S")])
    blob, proof = g.save()
    assert proof.lost == []
    t = docx.Document(io.BytesIO(blob)).tables[0]
    assert [c.paragraphs[0].alignment for c in t.rows[1].cells] == [None, WD_ALIGN_PARAGRAPH.RIGHT, None]
    grid = [int(c.get(f"{W}w")) for c in t._tbl.find(f"{W}tblGrid")]
    assert len(grid) == 3 and sum(grid) == sum(
        int(c.get(f"{W}w"))
        for c in next(etree.fromstring(zipfile.ZipFile(io.BytesIO(base.bytes)).read(DOCUMENT)).iter(f"{W}tblGrid"))
    )


# ---- the proof must be able to fail --------------------------------------------------
def _para_with(root, text):
    return next(p for p in root.iter(f"{W}p") if text in "".join(t.text or "" for t in p.iter(f"{W}t")))


def _fixed_word(root):
    _para_with(root, "fixed line").find(f"{W}r/{W}t").text = "Changed"


def _blank_gone(root):
    body = root.find(f"{W}body")
    body.remove(next(e for e in body if e.tag == f"{W}p" and not "".join(e.itertext()).strip()))


def _sect(root):
    root.find(f"{W}body").find(f"{W}sectPr").find(f"{W}pgMar").set(f"{W}left", "1")


def _proto_layout(root):
    ppr = _para_with(root, "Text").find(f"{W}pPr/{W}ind")
    ppr.set(f"{W}firstLine", "1")


def _run_size(root):
    r = _para_with(root, "More").find(f"{W}r")
    rpr = r.find(f"{W}rPr") if r.find(f"{W}rPr") is not None else etree.SubElement(r, f"{W}rPr")
    r.insert(0, rpr)
    etree.SubElement(rpr, f"{W}sz").set(f"{W}val", "99")


def _table_look(root):
    tc = next(root.iter(f"{W}tcPr"))
    etree.SubElement(tc, f"{W}shd").set(f"{W}fill", "000000")


def _mark_left(root):
    _para_with(root, "More").find(f"{W}r/{W}t").text = "[[body.p]]"


@pytest.mark.parametrize("tamper", [_fixed_word, _blank_gone, _sect, _proto_layout, _run_size, _table_look, _mark_left])
def test_the_proof_refuses_each_tamper_of_the_saved_file(base, tamper):
    f = firmbase.open(base)
    _complete(f)
    blob, _ = f.save()
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT))
    tamper(root)
    assert firmbase.prove(base, package(blob, root)).lost != []


def test_the_recorded_allow_refuses_a_change_no_put_recorded(base):
    f = firmbase.open(base)
    _complete(f)
    p = _para_with(f.root, "fixed line")
    p.find(f"{W}r/{W}t").text = "Changed behind the fill's back"
    _, proof = f.save()
    assert any("no ask names" in x for x in proof.leaves_outside_allow)


def test_a_changed_part_is_refused(base):
    f = firmbase.open(base)
    _complete(f)
    blob, _ = f.save()
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i.filename)
            out.writestr(i, data.replace(b"footer", b"FOOTER") if i.filename == "word/footer1.xml" else data)
    assert any("footer" in x for x in firmbase.prove(base, buf.getvalue()).parts_differ)


def test_the_layout_diff_sees_layout_and_not_words(base):
    f = firmbase.open(base)
    _complete(f)
    a = f.save()[0]
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(a)).read(DOCUMENT))
    _para_with(root, "More").find(f"{W}r/{W}t").text = "Other words"
    assert firmbase.layout_diff(a, package(a, root)) == []
    _proto_layout(root)
    assert firmbase.layout_diff(a, package(a, root)) != []


# ---- adoption ---------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["SMD Operator", "python-docx", "the smd operator"])
def test_a_file_the_operator_authored_is_named(who):
    assert firmbase.operator_authored(_file(who))


def test_a_persons_file_is_not_the_operators():
    assert not firmbase.operator_authored(_file("A Person"))


def _candidate(creator: str, saved_at: str, version: str = "", accepted: str = "", other_bytes: bool = False):
    blob = _file(creator)
    sha = "0" * 64 if other_bytes else hashlib.sha256(blob).hexdigest()
    return firmbase.Candidate(Source("m", "f", saved_at, sha, version, accepted), blob, saved_at)


FIRM_OLD, FIRM_NEW = _candidate("A Person", "2026-09-01"), _candidate("A Person", "2026-10-01")
OURS = _candidate("SMD Operator", "2026-11-04")
OURS_ACCEPTED_OLD = _candidate("SMD Operator", "2026-08-01", "v1", "his written yes")
OURS_ACCEPTED_NEW = _candidate("SMD Operator", "2026-11-03", "v2", "his Word save")
ACCEPTED_OTHER_BYTES = _candidate("SMD Operator", "2026-11-05", "v3", "his Word save", other_bytes=True)


@pytest.mark.parametrize(
    ("candidates", "want"),
    [
        ([FIRM_NEW, OURS_ACCEPTED_OLD], OURS_ACCEPTED_OLD),  # an accepted save outranks a newer firm file
        ([OURS_ACCEPTED_OLD, OURS_ACCEPTED_NEW, FIRM_NEW], OURS_ACCEPTED_NEW),  # the most recent accepted
        ([FIRM_OLD, FIRM_NEW, OURS], FIRM_NEW),  # no accepted save: the firm's, never the newer render
        ([OURS, ACCEPTED_OTHER_BYTES, FIRM_OLD], FIRM_OLD),  # a record for other bytes accepts nothing
        ([OURS, ACCEPTED_OTHER_BYTES], None),  # nothing usable: no base
        ([], None),
    ],
)
def test_the_base_is_the_most_recent_accepted_save_else_the_firms_file(candidates, want):
    assert firmbase.choose(candidates) == want


def test_cleaning_clears_what_names_a_matter_or_a_person():
    src = _file()
    buf = io.BytesIO()
    custom = (
        b'<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
        b'<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="MatterId"/></Properties>'
    )
    with zipfile.ZipFile(io.BytesIO(src)) as z, zipfile.ZipFile(buf, "w") as out:
        for i in z.infolist():
            out.writestr(i, z.read(i.filename))
        out.writestr("docProps/custom.xml", custom)
    z = zipfile.ZipFile(io.BytesIO(firmbase.clean_package(buf.getvalue())))
    core = z.read("docProps/core.xml")
    assert b"Matter's Title" not in core and b"A Person" not in core
    assert b"MatterId" not in z.read("docProps/custom.xml")
    assert not [n for n in z.namelist() if "thumbnail" in n]
    assert b"thumbnail" not in z.read("_rels/.rels")
    docx.Document(io.BytesIO(firmbase.clean_package(buf.getvalue())))
