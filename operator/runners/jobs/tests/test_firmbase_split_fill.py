"""A marker Word split across runs is filled, on Word's real run structure, and every
structure no Word-saved fixture shows fails closed (firmbase/markers.py, merge.py).

Each of the 21 fixtures in ``fixtures/word_saved_markers/`` is put in a document of
its own beside a marker in one run, and the document is filled the way a lane fills
it. ``VERDICTS`` holds what each must do, all 21 in one place.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from pathlib import Path

import docx
import pytest
from lxml import etree

import firmbase_word_saved_scrub as scrub
from jobs import firmbase
from jobs.firmbase import FirmBase, FirmBaseUnfit, Source, markers, merge
from jobs.firmbase import base as fb
from jobs.firmbase.base import DOCUMENT, W, Run

FIXTURES = Path(__file__).parent / "fixtures" / "word_saved_markers"
SOURCE = Source("m-1", "f-1", "A firm form.docx", "0" * 64)
FILLED = "Filled text"
CHANGE = "has an unfinished change by the firm (a tracked insertion)"
NO_SLOT = "the base has no slot"

#: Every fixture's verdict: ("fill", what must survive) or ("refuse", the reason).
VERDICTS: dict[str, tuple[str, str]] = {
    "one-proof-mark-spelling": ("fill", "the spelling marks go"),
    "two-proof-mark-grammar": ("fill", "the grammar marks go"),
    "three-revision-id-run": ("fill", ""),
    "four-revision-id-format": ("fill", ""),
    "five-bold-on-part": ("fill", ""),
    "six-underline-on-part": ("fill", ""),
    "seven-superscript-on-part": ("fill", ""),
    "eight-bookmark-start": ("fill", "the bookmark start stays"),
    "nine-bookmark-end": ("fill", "the bookmark end stays"),
    "ten-rendered-page-break": ("fill", "the rendered page break goes with its run"),
    "eleven-tab-in-line": ("refuse", NO_SLOT),
    "twelve-line-break-in-line": ("refuse", NO_SLOT),
    "thirteen-two-texts-one-run": ("refuse", NO_SLOT),
    "fourteen-no-break-hyphen": ("refuse", NO_SLOT),
    "fifteen-smart-tag-on-part": ("fill", "the smart tags go"),
    "sixteen-tracked-insertion": ("refuse", CHANGE),
    "seventeen-tracked-deletion": ("refuse", CHANGE),
    "eighteen-hyperlink-on-part": ("fill", "the hyperlink stays, holding the filled text; the tabs before it stay"),
    "nineteen-field-on-part": ("fill", "the field goes whole"),
    "twenty-content-control-on-part": ("fill", "the content control, which held only the marker's end, goes"),
    "twentyone-identical-runs": ("fill", ""),
}
FILL = [k for k, (v, _) in VERDICTS.items() if v == "fill"]
REFUSE = [k for k, (v, _) in VERDICTS.items() if v == "refuse"]


def _fixture(stem: str):
    return etree.fromstring((FIXTURES / f"{stem}.xml").read_bytes())


def _name(stem: str) -> str:
    return scrub.marker_for(f"{stem}.xml")[2:-2]


def _doc_around(paragraph) -> bytes:
    """A document: a fixed line, a marker in one run, then ``paragraph``."""
    d = docx.Document()
    d.add_paragraph("The firm's own fixed line")
    d.add_paragraph("[[other]]")
    d.element.body.find(f"{W}sectPr").addprevious(copy.deepcopy(paragraph))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _body(blob: bytes):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT)).find(f"{W}body")


def _fill(blob: bytes, name: str, lines=((Run(FILLED),),)):
    fill = firmbase.open(FirmBase.load("a_class", SOURCE, blob))
    fill.put(name, list(lines), carry=True)
    fill.put("other", [(Run("x"),)])
    return fill.save()


def _first_run(p):
    return next(t for t in markers.own_texts(p) if t.text).getparent()


def _canon(el):
    """An element as a tree of tags, attributes and text: the namespaces it inherits aside."""
    if el is None:
        return None
    return (el.tag, sorted(el.attrib.items()), (el.text or "").strip(), [_canon(c) for c in el])


def test_the_table_holds_every_fixture_once():
    assert sorted(VERDICTS) == sorted(p.stem for p in scrub.fixture_files(FIXTURES))
    assert (len(FILL), len(REFUSE)) == (15, 6)


@pytest.mark.parametrize("stem", FILL)
def test_a_fillable_fixture_fills_with_its_first_runs_look_and_proves(stem):
    was = _fixture(stem)
    blob, proof = _fill(_doc_around(was), _name(stem))
    assert proof.lost == [], proof.lost
    now = _body(blob)[2]
    assert markers.paragraph_text(now) == FILLED
    look = _canon(_first_run(was).find(f"{W}rPr"))
    assert [_canon(r.find(f"{W}rPr")) for r in {t.getparent() for t in markers.own_texts(now)}] == [look]
    assert _canon(now.find(f"{W}pPr")) == _canon(was.find(f"{W}pPr"))
    for tag in ("bookmarkStart", "bookmarkEnd", "del"):
        assert [_canon(e) for e in now.iter(f"{W}{tag}")] == [_canon(e) for e in was.iter(f"{W}{tag}")]
    for tag in ("proofErr", "smartTag", "fldChar", "instrText", "lastRenderedPageBreak", "sdt"):
        assert not list(now.iter(f"{W}{tag}")), f"a {tag} stayed"


def test_a_hyperlink_holding_the_markers_start_holds_the_filled_text_and_the_tabs_before_it_stay():
    was = _fixture("eighteen-hyperlink-on-part")
    now = _body(_fill(_doc_around(was), _name("eighteen-hyperlink-on-part"))[0])[2]
    link = now.find(f"{W}hyperlink")
    assert link is not None and dict(link.attrib) == dict(was.find(f"{W}hyperlink").attrib)
    assert "".join(t.text for t in link.iter(f"{W}t")) == FILLED
    assert len(now.findall(f"{W}r/{W}tab")) == 6


@pytest.mark.parametrize("stem", [s for s in REFUSE if VERDICTS[s][1] == NO_SLOT])
def test_a_tab_break_or_hyphen_inside_the_marker_is_no_marked_place(stem):
    blob = _doc_around(_fixture(stem))
    assert markers.read(_fixture(stem)).names == ()
    base = FirmBase.load("a_class", SOURCE, blob)
    assert _name(stem) not in base.slots
    with pytest.raises(FirmBaseUnfit, match=f"{NO_SLOT} {_name(stem)}"):
        firmbase.open(base).put(_name(stem), [(Run(FILLED),)])


@pytest.mark.parametrize("stem", [s for s in REFUSE if VERDICTS[s][1] == CHANGE])
def test_a_pending_insertion_refuses_the_file_with_its_plain_reason(stem):
    base = FirmBase.load("a_class", SOURCE, _doc_around(_fixture(stem)))
    with pytest.raises(FirmBaseUnfit, match=r"the file cannot be written into: the marked line \[\[fixture\.s") as e:
        firmbase.open(base)
    assert CHANGE in str(e.value) and "accepted or rejected in Word" in str(e.value)


def test_text_in_a_pending_deletion_is_not_part_of_the_line_and_a_deletion_after_the_marker_stays():
    assert markers.paragraph_text(_fixture("seventeen-tracked-deletion")) == "[[fixture.seventeen]]"
    p = _fixture("twentyone-identical-runs")
    gone = etree.SubElement(p, f"{W}del", {f"{W}id": "1", f"{W}author": "Fixture Author"})
    etree.SubElement(etree.SubElement(gone, f"{W}r"), f"{W}delText").text = "###"
    blob, proof = _fill(_doc_around(p), "fixture.twentyone")
    assert proof.lost == []
    assert [_canon(e) for e in _body(blob)[2].iter(f"{W}del")] == [_canon(gone)]


# ---- what no Word-saved file shows: each refuses, by name -------------------------------
def _wrap(tag: str, **attrs):
    def change(p):
        tail = p.findall(f"{W}r")[1]
        wrapper = etree.Element(f"{W}{tag}", {f"{W}{k}": v for k, v in attrs.items()})
        tail.addprevious(wrapper)
        wrapper.append(tail)

    return change


def _comment(p):
    p.findall(f"{W}r")[1].addprevious(etree.Element(f"{W}commentRangeStart", {f"{W}id": "1"}))
    p.append(etree.Element(f"{W}commentRangeEnd", {f"{W}id": "1"}))


def _hide(which):
    def change(p):
        for r in [p.findall(f"{W}r")[i] for i in which]:
            etree.SubElement(r.find(f"{W}rPr") if r.find(f"{W}rPr") is not None else _rpr(r), f"{W}vanish")

    return change


def _rpr(r):
    rpr = etree.Element(f"{W}rPr")
    r.insert(0, rpr)
    return rpr


def _deleted_inside(p):
    gone = etree.Element(f"{W}del", {f"{W}id": "1"})
    etree.SubElement(etree.SubElement(gone, f"{W}r"), f"{W}delText").text = "#"
    p.findall(f"{W}r")[1].addprevious(gone)


def _format_change(p):
    etree.SubElement(_rpr(p.findall(f"{W}r")[1]), f"{W}rPrChange", {f"{W}id": "1"})


SYNTHETIC = {
    "a tracked move from": (_wrap("moveFrom", id="1"), r"a tracked move \(w:moveFrom\)"),
    "a tracked move to": (_wrap("moveTo", id="1"), r"a tracked move \(w:moveTo\)"),
    "a customXml wrapper": (_wrap("customXml", element="x"), r"custom XML \(w:customXml\)"),
    "a comment range inside the line": (_comment, r"a comment anchored in it \(w:commentRangeStart\)"),
    "a simple field with text": (_wrap("fldSimple", instr=" REF bm1 "), r"a field \(w:fldSimple\)"),
    "part of the line hidden": (_hide([1]), "is partly hidden text"),
    "the whole line hidden": (_hide([0, 1]), "a marker nobody can see"),
    "a pending deletion inside the marker": (_deleted_inside, r"\(a tracked deletion inside the marker\)"),
    "a tracked format change": (_format_change, r"\(a tracked format change\)"),
}


@pytest.mark.parametrize("what", SYNTHETIC)
def test_structure_no_word_saved_fixture_shows_refuses_the_file_by_name(what):
    change, reason = SYNTHETIC[what]
    p = _fixture("twentyone-identical-runs")
    change(p)
    assert markers.slot_names(p) == ["fixture.twentyone"], "the change keeps the line reading as the marker"
    base = FirmBase.load("a_class", SOURCE, _doc_around(p))
    with pytest.raises(FirmBaseUnfit, match="the file cannot be written into: the marked line") as e:
        firmbase.open(base)
    assert re.search(reason, str(e.value)), str(e.value)


def test_an_unknown_element_in_a_one_run_marker_line_refuses_too():
    p = etree.Element(f"{W}p")
    etree.SubElement(p, f"{W}permStart", {f"{W}id": "1"})
    etree.SubElement(etree.SubElement(p, f"{W}r"), f"{W}t").text = "[[who]]"
    with pytest.raises(FirmBaseUnfit, match=r"\(w:permStart\)"):
        firmbase.open(FirmBase.load("a_class", SOURCE, _doc_around(p)))


# ---- the allowed lists are what the fixtures hold ---------------------------------------
def _structure(stem: str) -> set[str]:
    out = set()
    for el in _fixture(stem).iter():
        parent = el.getparent()
        if parent is not None and parent.tag in (f"{W}rPr", f"{W}pPr") or el.tag == f"{W}p":
            continue
        if parent is not None and parent.tag in (f"{W}sdt",):
            continue
        if parent is not None and any(a.tag == f"{W}pPr" for a in el.iterancestors()):
            continue
        out.add(etree.QName(el).localname)
    return out


def test_every_allowed_entry_is_held_by_a_fixture_and_every_fixture_element_is_allowed():
    held = {stem: _structure(stem) for stem in VERDICTS}
    assert set().union(*held.values()) == set(markers.CONTENT) | set(markers.IN_RUN)
    for name, proof in [*markers.CONTENT.items(), *markers.IN_RUN.items()]:
        cited = [stem for stem in VERDICTS if stem in proof]
        assert cited or proof == "every fixture", name
        assert all(name in held[stem] for stem in cited), f"{name}: {proof}"


# ---- where else a split marker stands ---------------------------------------------------
def test_a_split_marker_in_a_table_cell_fills_in_place_and_proves():
    d = docx.Document()
    t = d.add_table(rows=1, cols=2)
    cell = t.rows[0].cells[0]._tc
    cell.remove(cell.find(f"{W}p"))
    cell.append(copy.deepcopy(_fixture("one-proof-mark-spelling")))
    t.rows[0].cells[1].paragraphs[0].text = "[[other]]"
    buf = io.BytesIO()
    d.save(buf)
    blob, proof = _fill(buf.getvalue(), "fixture.one")
    assert proof.lost == [], proof.lost
    assert markers.paragraph_text(_body(blob).find(f"{W}tbl").find(f".//{W}tc/{W}p")) == FILLED


def test_more_lines_than_marked_ones_keep_the_bookmark_on_the_firms_own_line_only():
    blob, proof = _fill(_doc_around(_fixture("eight-bookmark-start")), "fixture.eight", ((Run("one"),), (Run("two"),)))
    assert proof.lost == [], proof.lost
    first, second = list(_body(blob))[2:4]
    assert [markers.paragraph_text(first), markers.paragraph_text(second)] == ["one", "two"]
    assert len(first.findall(f"{W}bookmarkStart")) == 1 and second.find(f"{W}bookmarkStart") is None


def test_a_bookmark_in_a_line_the_region_repeats_refuses():
    p = _fixture("eight-bookmark-start")
    texts = p.findall(f".//{W}t")
    texts[0].text, texts[1].text = "[[bo", "dy.p]]"
    with pytest.raises(FirmBaseUnfit, match="a bookmark sits in a line the Operator repeats"):
        firmbase.open(FirmBase.load("a_class", SOURCE, _doc_around(p)))


def test_a_refused_file_surfaces_as_firm_base_unfit_through_the_lane(tmp_path):
    from jobs.drafting import brief_base
    from jobs.drafting.outcome import DraftingFailed
    from jobs.drafting.revise.layout import paper_of

    base = FirmBase.load("a_class", SOURCE, _doc_around(_fixture("sixteen-tracked-insertion")))
    with pytest.raises(DraftingFailed, match=r"^firm_base_unfit: the file cannot be written into: .*nothing was filed"):
        brief_base.render_into(
            "", tmp_path / "out.docx", base, paper_of(base.bytes), make=lambda b, _md: firmbase.open(b)
        )
    assert not (tmp_path / "out.docx").exists()


# ---- a file whose markers each sit in one run fills byte for byte as before -------------
def _legacy(monkeypatch):
    """The single-run reader, no structure read, and a merge that must never run."""
    monkeypatch.setattr(markers, "marked", fb.slot_names)
    monkeypatch.setattr(markers, "unwritable", lambda _el: [])
    monkeypatch.setattr(merge, "collapse", _never)


def _never(*_a):
    raise AssertionError("a one-run marker was merged")


def _both_ways(monkeypatch, make) -> tuple[bytes, bytes]:
    """(filled now, filled by the single-run reader with no structure read)."""
    with monkeypatch.context() as m:
        m.setattr(merge, "collapse", _never)
        new = make()
    with monkeypatch.context() as m:
        _legacy(m)
        old = make()
    return new, old


def test_a_one_run_firmbase_file_fills_byte_for_byte_as_before(monkeypatch):
    import test_firmbase as tf

    # One source for both ways. Word (and python-docx) stamp the zip entries with
    # the wall clock at two-second DOS resolution, so a source built per side
    # differs at byte 10 whenever the two builds straddle a boundary; CI on main
    # b4d97f03 failed exactly there with the fill itself identical.
    source = tf._file()

    def make():
        fill = firmbase.open(FirmBase.load("a_class", tf.SOURCE, source))
        tf._complete(fill)
        return fill.save()[0]

    new, old = _both_ways(monkeypatch, make)
    assert new == old


def test_a_one_run_brief_and_memo_base_fill_byte_for_byte_as_before(monkeypatch, tmp_path):
    import test_drafting_brief_base as tb
    import test_drafting_memo_base as tm

    from jobs.drafting import brief_base, memo_base, pleading, render

    fmt = tb._fmt()
    path, _ = render.render(tb.MD, tb.CLS, tmp_path / "accepted.docx", fmt)
    pleading.apply_for(path, fmt, tb.CLS)
    accepted = path.read_bytes()
    adopted, _notes = brief_base.adopt(accepted, tb._accepted(accepted))
    memo_blob = tm._firm_memo()
    memo_made, _notes = memo_base.adopt(memo_blob, tm._source(memo_blob))

    def brief() -> bytes:
        return brief_base.fill(FirmBase.load(tb.CLS, SOURCE, adopted), tb.MD)[0]

    def memo() -> bytes:
        return memo_base.fill(FirmBase.load(tm.CLS, SOURCE, memo_made), tm.MEMO)[0]

    for make in (brief, memo):
        new, old = _both_ways(monkeypatch, make)
        assert new == old


# ---- a file that is no base, through the lane's own entry ------------------------------
def _no_markers() -> bytes:
    d = docx.Document()
    d.add_paragraph("The firm's own fixed line")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _every_marker_tab_split() -> bytes:
    d = docx.Document()
    d.add_paragraph("The firm's own fixed line")
    d.element.body.find(f"{W}sectPr").addprevious(_fixture("eleven-tab-in-line"))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("make", [_no_markers, _every_marker_tab_split], ids=["no markers", "every marker tab-split"])
def test_a_file_that_is_no_base_ends_the_brief_job_as_firm_base_unfit(make, tmp_path, monkeypatch):
    import json

    import test_drafting_brief_base as tb
    from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
    from jobs_testkit import PRICING

    from jobs.drafting.run import DraftingRun

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(tb.CHECKER))
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    make_inputs(inputs)
    tb._with_base(inputs, make())
    run = DraftingRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=ScriptedClient(draft=tb._job_brief()),
        log=lambda _line: None,
        readback_pause=0.0,
    )
    verdict = run.run()
    assert (verdict.outcome, verdict.stage) == ("failed", "rendered")
    assert verdict.reason == "firm_base_unfit: the file marks no slots; nothing was filed", verdict.reason
