"""Revise mode keeps the FILED brief's layout (drafting/revise/layout.py).

The $0 rehearsal on a real request (2026-11-04) found a revision could read a
firm-styled brief as having no headings, prove a flattened base, drop the
pleading paper and the chart look the attorney accepted, re-insert a marker
into a section he had filled, and leave notes describing a brief that did
not file. Each test here fails on the code before this module.

Every name, number and sentence is invented (Example / Exampletown), and no
test makes a model call: the doorways are scripted.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from docx.oxml.ns import qn

from jobs.drafting import pleading, render
from jobs.drafting.revise import checklist as ck, checks, core, prior, sections as sec
from test_drafting_revise import BRIEF, KEY, REQUEST, REVISE, RunClient, RunSeat, scripted_edit, scripted_read

CLS = "mediation_brief"
CHART_LOOK = {"table_header_fill": "D9E2F3", "table_border_pt": 1}


def _lay() -> Any:
    """The module under test, imported per test so each test fails on its own
    where it does not exist yet."""
    from jobs.drafting.revise import layout

    return layout


@pytest.fixture
def fmt(tmp_path: Path) -> dict[str, Any]:
    from drafting_testkit import make_inputs

    from jobs.drafting import firm as firm_mod

    return firm_mod.load(str(make_inputs(tmp_path / "inputs"))).data["format"]


def _render(md: str, out: Path, fmt: dict[str, Any], paper: str = "plain", **lay: Any) -> Path:
    f = copy.deepcopy(fmt)
    f.setdefault("layout", {}).setdefault(CLS, {}).update(lay)
    path = render.render(md, CLS, out, f)[0]
    if paper == "pleading":
        pleading.apply_for(path, f, CLS)
    return path


def _plain_render(fmt: dict[str, Any]):
    return lambda md, out: _render(md, out, fmt)


def firm_styled(path: Path) -> Path:
    """The filed brief as the firm's own styles carry it: "Brief Heading" /
    "Brief Body", the headings' bold in the style, not in the runs."""
    d = docx.Document(str(path))
    users: dict[str, list[Any]] = {}
    for p in d.paragraphs:
        users.setdefault(p.style.name, []).append(p)
    for old, new in (("SMD Heading 1", "Brief Heading"), ("SMD Body", "Brief Body")):
        st = d.styles[old]
        st.name, st.style_id = new, new.replace(" ", "")
        if new == "Brief Heading":
            st.font.bold = True
        for p in users.get(old, []):
            p.style = st
            if new == "Brief Heading":
                for r in p.runs:
                    r.bold = None
    d.save(str(path))
    return path


NAVY = "1F3864"


def accepted_chart(path: Path) -> Path:
    """The damages chart as the attorney accepted it: a navy header in white
    text, columns 2:1:1 (not equal), cell margins, the amount columns
    right-aligned, and no keep-with-next on its rows."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.shared import RGBColor

    from jobs.drafting.revise import charts

    d = docx.Document(str(path))
    t = d.tables[1]
    widths = [4320, 2160, 2160]
    grid = t._tbl.find(qn("w:tblGrid"))
    for col, w in zip(grid, widths):
        col.set(qn("w:w"), str(w))
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    charts.put(
        t._tbl.tblPr,
        "tblCellMar",
        parse_xml(
            f'<w:tblCellMar {ns}><w:left w:w="144" w:type="dxa"/><w:right w:w="144" w:type="dxa"/></w:tblCellMar>'
        ),
        charts.TBLPR,
    )
    for r_i, row in enumerate(t.rows):
        for c_i, c in enumerate(row.cells):
            pr = c._tc.get_or_add_tcPr()
            charts.put(pr, "tcW", parse_xml(f'<w:tcW {ns} w:w="{widths[c_i]}" w:type="dxa"/>'), charts.TCPR)
            if r_i == 0:
                charts.put(
                    pr, "shd", parse_xml(f'<w:shd {ns} w:val="clear" w:color="auto" w:fill="{NAVY}"/>'), charts.TCPR
                )
            for p in c.paragraphs:
                p.paragraph_format.keep_with_next = False
                if c_i > 0:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                for r in p.runs:
                    if r_i == 0:
                        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    d.save(str(path))
    return path


def _heads(path: Path) -> list[tuple[int, str]]:
    return _lay().read(path)["headings"]


# ---- D1 + D4: a firm-styled filed brief reads back with its headings ------------------------
def test_a_firm_styled_brief_reads_back_with_its_sections(tmp_path, fmt) -> None:
    """FALSIFIER (D1): read only "SMD Heading" and a "Brief Heading" brief has
    no sections, so every section edit is refused."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt))
    md = prior.docx_to_md(filed)
    titles = [s.title for s in sec.split(md) if s.heading]
    assert titles == [s.title for s in sec.split(BRIEF) if s.heading]
    ck.validate(KEY, REQUEST, sec.split(md))  # every item's target is a section again


def test_a_headings_bold_from_its_style_is_bold(tmp_path, fmt) -> None:
    """FALSIFIER (D4): emphasis read off the runs alone calls a style-bold
    heading plain, so the firm's brief never proves."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt))
    seen = dict(prior.printed(filed))
    assert seen["I. INTRODUCTION"] == (("I. INTRODUCTION", True, False),)
    rec = prior.resolve(filed, [], _plain_render(fmt), tmp_path)
    assert rec["source"] == "filed"


# ---- D2: the base proof compares the layout, and fails closed --------------------------------
def test_a_flattened_base_does_not_prove(tmp_path, fmt) -> None:
    """FALSIFIER (D2): words and emphasis alone prove a base with every
    heading flattened to a body line."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt))
    flat = "\n".join(ln[2:] if ln.startswith("# ") else ln for ln in BRIEF.splitlines()) + "\n"
    diffs = prior.prove(flat, filed, _plain_render(fmt), tmp_path)
    assert any(d.startswith("headings") for d in diffs), diffs


def test_a_base_that_drops_the_pleading_paper_does_not_prove(tmp_path, fmt) -> None:
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt, "pleading")
    diffs = prior.prove(BRIEF, filed, _plain_render(fmt), tmp_path)
    assert any(d.startswith("paper") for d in diffs), diffs


def test_a_base_that_drops_the_chart_look_does_not_prove(tmp_path, fmt) -> None:
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt, **CHART_LOOK)
    diffs = prior.prove(BRIEF, filed, _plain_render(fmt), tmp_path)
    assert any(d.startswith("tables") for d in diffs), diffs


# ---- D3: every first-page key is a checklist key, and checked for real -----------------------
FRONT = (
    "Alpha Example, Esq.\nExample & Example, LLP\n\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n\n"
    "**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n**COUNTY OF EXAMPLETOWN**\n\n"
    "| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE, Defendant. | Case No. CV-0001 |\n| --- | --- |\n"
    "| | Mediation Date: 11/17/26 |\n\n"
)
SIGNATURE = (
    "\n\nDated: __________\n\n**ASHTON EXAMPLE, LLP**\n\nBy: ______________________________\n"
    "Alpha Example, Esq.\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n"
)
FULL = FRONT + BRIEF.split("\n\n", 2)[2] + SIGNATURE
V7_KEYS = {
    "front_court_gap_lines": 2,
    "caption_rule_below": "left",
    "signature_date_beside": True,
    "signature_single_spaced": True,
    "signature_gap_lines": 2,
}


@pytest.mark.parametrize(
    "key", ["front_court_gap_lines", "caption_rule_below", "signature_single_spaced", "signature_gap_lines"]
)
def test_a_first_page_ask_is_a_checklist_item_and_checked_in_the_file(tmp_path, fmt, key) -> None:
    """FALSIFIER (D3): the key is refused by the checklist, and once allowed,
    nothing reads it in the file, so an unmet ask reads met."""
    ask = {"signature_date_beside": True, key: V7_KEYS[key]} if key.startswith("signature") else {key: V7_KEYS[key]}
    item = {"id": "c1", "quote": "Double space the whole brief", "kind": "formatting", "target": "document"}
    ck.validate({"items": [{**item, "params": {"layout": ask}}]}, REQUEST, sec.split(BRIEF))
    laid = _render(FULL, tmp_path / "laid.docx", fmt, **ask)
    assert checks.check_docx(laid, ask, 12, 1) == []
    control = _render(FULL, tmp_path / "control.docx", fmt)
    assert checks.check_docx(control, {key: V7_KEYS[key]}, 12, 1), f"{key} reads met on a file that lacks it"


def test_heading_space_and_total_fill_are_read_in_the_word_xml(tmp_path, fmt) -> None:
    """FALSIFIER (D2): ``check_docx`` never read these keys, so they were met on trust."""
    md = BRIEF.replace(
        "| Dr. Rowe | $8,100.00 | $3,000.00 |",
        "| Dr. Rowe | $8,100.00 | $3,000.00 |\n| Total | $49,300.00 | $15,000.00 |",
    )
    asks = {"heading_space_before_pt": 18, "table_total_fill": "FFF2CC"}
    assert checks.check_docx(_render(md, tmp_path / "met.docx", fmt, **asks), asks, 12, 1) == []
    plain = _render(md, tmp_path / "plain.docx", fmt)
    for k, v in asks.items():
        assert checks.check_docx(plain, {k: v}, 12, 1), f"{k} reads met on a file that lacks it"


# ---- D6: a filled section is the attorney's ---------------------------------------------------
FILLED = BRIEF.replace(
    "{{ATTORNEY: settlement authority, the target figure, and any bracket}}",
    "Plaintiff's settlement range at mediation is $250,000.00 to $300,000.00.",
)


def test_a_section_the_attorney_filled_gets_no_marker_back() -> None:
    """FALSIFIER (D6): the drafting render's reservation, run over a revision,
    strips the attorney's own figures and puts the marker back."""
    assert "{{ATTORNEY" in render.reserve_judgment(FILLED, CLS)[0]  # the control: the drafting rule would
    assert core.reserve(FILLED, FILLED, CLS) == FILLED
    # A section still carrying its marker keeps the drafting rule.
    assert core.reserve(BRIEF, BRIEF, CLS) == render.reserve_judgment(BRIEF, CLS)[0]


# ---- the estimate counts the section plan -----------------------------------------------------
def test_the_estimate_counts_every_section_a_document_wide_edit_makes() -> None:
    """FALSIFIER: a flat eight reads under-prices a whole-brief "other" edit."""
    items = [ck.Item("c1", "Make it more persuasive", "other", "document", {})]
    plan = core.plan_reads(BRIEF, REQUEST, items)
    headed = len([s for s in sec.split(BRIEF) if s.heading])
    assert plan["sections"] == headed and plan["calls"] == 2 + 4 * headed
    assert plan["characters"] > 8 * (len(BRIEF) + len(REQUEST))


# ---- the whole job: the filed layout survives a revision --------------------------------------
def _run(tmp_path: Path, filed: bytes, key: dict, request: str = REQUEST, edits: dict | None = None):
    from demand_testkit import MATTER
    from drafting_testkit import envelope
    from jobs_testkit import PRICING

    from jobs.demand import deliver
    from jobs.drafting.revise.run import ReviseRun

    notes = deliver.render_plain("# Attorney notes\n\nThe filed notes.\n", tmp_path, "n.docx", "Example").read_bytes()
    seat = RunSeat({"draft-0001": filed, "notes-0001": notes})
    files = [
        {"role": r, "file_id": f, "name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        for r, f, n, b in (
            ("draft", "draft-0001", "Brief.docx", filed),
            ("attorney_notes", "notes-0001", "Brief - attorney notes.docx", notes),
        )
    ]
    jd = tmp_path / "jobs" / "01JBBBBBBBBBBBBBBBBBBBBBBB"
    jd.mkdir(parents=True)
    env = {
        **envelope(job_id=jd.name),
        "request_text": request,
        "slug": "example",
        "month_cents_used": 0,
        "allowance_remaining": 0,
        "revise": {**REVISE, "files": files},
    }
    assert env["matter_id"] == MATTER
    (jd / "job.json").write_text(json.dumps(env))
    (tmp_path / "pricing.json").write_text(json.dumps(PRICING))
    run = ReviseRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(tmp_path / "pricing.json"),
        seat_factory=lambda: seat,
        client=RunClient(scripted_edit(edits), scripted_read(), key),
        log=lambda m: None,
    )
    return run.run(), seat, jd


def _header_fill(path_or_blob: Any, table: int) -> str:
    d = docx.Document(io.BytesIO(path_or_blob) if isinstance(path_or_blob, bytes) else str(path_or_blob))
    shd = d.tables[table].rows[0].cells[0]._tc.tcPr.find(qn("w:shd"))
    return shd.get(qn("w:fill")) if shd is not None else ""


def test_a_revision_keeps_the_filed_paper_chart_look_and_filled_sections(tmp_path, fmt) -> None:
    """FALSIFIER (D5, D6): the revision is rendered from the firm's config
    alone, so pleading paper and the accepted chart look are dropped and the
    attorney's case-value text gets a marker back."""
    filed = accepted_chart(firm_styled(_render(FILLED, tmp_path / "filed.docx", fmt, "pleading", **CHART_LOOK)))
    key = {"items": [i for i in KEY["items"] if i["kind"] != "formatting"]}
    v, seat, jd = _run(tmp_path, filed.read_bytes(), key)
    assert v.outcome == "delivered", (v.stage, v.reason)
    out = tmp_path / "out.docx"
    out.write_bytes(seat.files["draft-0001"][-1])
    got = _lay().read(out)
    assert got["paper"] == "pleading"
    assert _header_fill(out, 1) == NAVY == _header_fill(filed, 1)
    # N3: the whole accepted look, not just the fill: rules, widths, margins,
    # alignment, keep-with-next and the header's white text.
    assert got["tables"] == _lay().read(filed)["tables"]
    # N4: the firm's style names, not SMD's.
    names = {p.style.name for p in docx.Document(str(out)).paragraphs if p.text.strip()}
    assert {"Brief Heading", "Brief Body"} <= names and not {n for n in names if n.startswith("SMD Heading")}
    assert len(got["headings"]) >= len(_heads(filed))
    text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "$250,000.00 to $300,000.00" in text and "{{ATTORNEY" not in text
    est = json.loads((jd / "data" / "revise-estimate.json").read_text())
    assert est["calls"] >= 2 and est["sections"] >= 1


def test_a_heading_space_ask_on_pleading_paper_never_delivers_without_its_headings(tmp_path, fmt) -> None:
    """FALSIFIER (D2, the harm): a firm-styled brief on pleading paper, asked
    for space before its headings. Before: the base read as headless proved,
    the ask read met, and the brief was refiled flat, on plain paper."""
    filed = firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt, "pleading"))
    req = "Please put 12 points of space before each heading."
    key = {
        "items": [
            {
                "id": "c1",
                "quote": "put 12 points of space before each heading",
                "kind": "formatting",
                "target": "document",
                "params": {"layout": {"heading_space_before_pt": 12}},
            }
        ]
    }
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, req)
    if v.outcome == "delivered":
        blob = seat.files["draft-0001"][-1]
        heads = [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs if "Heading" in p.style.name]
        assert heads == [s.title for s in sec.split(BRIEF) if s.heading], "delivered with its headings lost"
        z = zipfile.ZipFile(io.BytesIO(blob))
        headers = b"".join(z.read(n) for n in z.namelist() if n.startswith("word/header"))
        assert b"txbxContent" in headers, "delivered off its pleading paper"
    else:
        # The base proved (headings and paper read); the ask is what code refused.
        assert v.stage == "revise-render", v.reason  # off the pleading pitch, or the ask unmet: a stop
        assert seat.sent == [], "a refused revision filed something"


# ---- the second rehearsal: N1..N6 ---------------------------------------------------------------
def test_n1_a_caption_rule_ask_is_laid_and_not_copied_back(tmp_path, fmt) -> None:
    """FALSIFIER (N1): the caption table was carried and compared like a chart,
    so an ask to end the caption's rule at its vertical line was laid, then the
    filed whole-width rule was copied back: the format check refused it and the
    tables facet refused the change."""
    from jobs.drafting import briefpage

    filed = _render(FULL, tmp_path / "filed.docx", fmt, caption_rule_below=True, signature_date_beside=True)
    req = "Please end the solid line under the caption at the vertical line."
    key = {
        "items": [
            {
                "id": "c1",
                "quote": "end the solid line under the caption at the vertical line",
                "kind": "formatting",
                "target": "document",
                "params": {"layout": {"caption_rule_below": "left"}},
            }
        ]
    }
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, req)
    assert v.outcome == "delivered", (v.stage, v.reason)
    d = docx.Document(io.BytesIO(seat.files["draft-0001"][-1]))
    assert briefpage._check_rule(briefpage._caption_table(d), "left") == []


def test_n2_the_revise_stops_are_known_reason_codes() -> None:
    """FALSIFIER (N2): an unlisted code records as "unexpected"."""
    from jobs.drafting.outcome import REASON

    assert REASON.match("revise_layout_lost: x") and REASON.match("revise_unchanged: x")


def test_n3_a_chart_that_loses_its_widths_colour_or_alignment_does_not_prove(tmp_path, fmt) -> None:
    """FALSIFIER (N3): a chart compared on fill and borders alone reads the
    same with equal columns, black header text and left-aligned amounts."""
    filed = accepted_chart(_render(BRIEF, tmp_path / "filed.docx", fmt))
    diffs = prior.prove(BRIEF, filed, _plain_render(fmt), tmp_path)
    assert any(d.startswith("tables") for d in diffs), diffs


def _inplace() -> Any:
    from jobs.drafting.revise import inplace

    return inplace


def test_n5_a_border_ask_writes_tblborders_in_schema_order(tmp_path, fmt) -> None:
    """FALSIFIER (N5): a tblBorders appended after tblLook is out of the
    CT_TblPr sequence (Word repairs or refuses the file)."""
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt)
    d = docx.Document(str(filed))
    for t in d.tables:  # a chart as some firms keep it: no borders of its own, a tblLook
        for b in t._tbl.tblPr.findall(qn("w:tblBorders")):
            t._tbl.tblPr.remove(b)
    d.save(str(filed))
    ed = _inplace().open_filed(filed)
    _inplace().edit_layout(ed, {"table_border_pt": 1})
    out = tmp_path / "out.docx"
    _inplace().save(ed, out)
    order = ("tblStyle", "tblW", "jc", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook")
    for t in docx.Document(str(out)).tables[1:]:
        tags = [c.tag.rsplit("}", 1)[-1] for c in t._tbl.tblPr]
        seen = [x for x in tags if x in order]
        assert "tblBorders" in seen and seen == sorted(seen, key=order.index), tags


def _canon_body(blob: bytes) -> list[str]:
    from lxml import etree

    body = docx.Document(io.BytesIO(blob)).element.body
    return [etree.tostring(el, method="c14n").decode() for el in body]


def _canon_parts(blob: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(io.BytesIO(blob))
    return {n: z.read(n) for n in z.namelist() if n != "word/document.xml" and not n.startswith("docProps/")}


def test_the_revision_is_his_file_with_only_the_asked_paragraph_changed(tmp_path, fmt) -> None:
    """FALSIFIER (the design): a revision re-rendered from markdown and given
    its formatting back loses what no list of carried facets names. Made in
    place, every element but the one the ask names is his XML, and so are
    his styles, numbering, section and headers: a navy header in white,
    2:1 columns, a right-aligned amount column, "Brief Heading/Body"."""
    filed = accepted_chart(firm_styled(_render(BRIEF, tmp_path / "filed.docx", fmt, "pleading", **CHART_LOOK)))
    key = {"items": [i for i in KEY["items"] if i["id"] == "c7"]}  # the conclusion's words, made by code
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key)
    assert v.outcome == "delivered", (v.stage, v.reason)
    before, after = filed.read_bytes(), seat.files["draft-0001"][-1]
    a, b = _canon_body(before), _canon_body(after)
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert len(a) == len(b) and len(changed) == 1, changed
    para = docx.Document(io.BytesIO(after)).element.body[changed[0]]
    assert "".join(para.itertext()).endswith("value her case fairly.")
    assert _canon_parts(before) == _canon_parts(after)


def _format_key(quote: str, **params) -> dict:
    return {"items": [{"id": "c1", "quote": quote, "kind": "formatting", "target": "document", "params": params}]}


def test_n6_an_ask_the_filed_brief_already_meets_files_nothing(tmp_path, fmt) -> None:
    """FALSIFIER (N6): an ask already met was filed as a new, identical version."""
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt, table_font_pt=10)
    key = _format_key("Make the table font 10 point", layout={"table_font_pt": 10})
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, "Make the table font 10 point please.")
    assert v.outcome == "held" and str(v.reason).startswith("revise_unchanged:"), (v.outcome, v.reason)
    assert "already how the filed brief reads" in str(v.reason) and seat.sent == []


def test_n6_a_relative_ask_that_goes_no_further_than_the_filed_value_files_nothing(tmp_path, fmt) -> None:
    """FALSIFIER (N6): "more space" was laid at a value below the filed one,
    which would have filed LESS space as the change he asked for."""
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt, heading_space_before_pt=18)
    req = "Put more space before the headings."
    key = _format_key("more space before the headings", layout={"heading_space_before_pt": 12}, relative="more")
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, req)
    assert v.outcome == "held" and str(v.reason).startswith("revise_unchanged:"), (v.outcome, v.reason)
    assert "18" in str(v.reason) and seat.sent == []


def test_n6_other_asks_none_made_file_nothing(tmp_path, fmt) -> None:
    """FALSIFIER (N6): every ask was "other" and none could be made, and the
    unchanged brief was still filed as a new version."""
    filed = _render(BRIEF, tmp_path / "filed.docx", fmt)
    liability = next(s.text for s in sec.split(BRIEF) if s.title == "V. LIABILITY")
    key = {"items": [i for i in KEY["items"] if i["id"] == "c9"]}
    v, seat, _jd = _run(tmp_path, filed.read_bytes(), key, edits={"V. LIABILITY": liability})
    assert v.outcome == "held" and str(v.reason).startswith("revise_unchanged:"), (v.outcome, v.reason)
    assert "could not be made" in str(v.reason) and seat.sent == []
