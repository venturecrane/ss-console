"""A mediation brief's first draft in the firm's own Word file: its layout, our words,
proven. Every fixture is synthetic: a brief this repo's renderer lays, saved under a
person's name, then adopted the way a firm's brief is."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import docx
import pytest
import yaml
from lxml import etree

from drafting_testkit import SECTIONS, ScriptedClient, firm_data, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs import firmbase
from jobs.drafting import brief_base, firm, format_check, pleading, render
from jobs.drafting.outcome import REASON, DraftingFailed
from jobs.firmbase.base import DOCUMENT, SLOT, W, _column_widths, package, slot_names
from jobs.drafting.revise.layout import paper_of
from jobs.drafting.run import DraftingRun

CLS = "mediation_brief"
ACCEPTED_LAYOUT = {
    "front_single_spaced": True,
    "front_attorney_gap_lines": 1,
    "front_court_gap_lines": 2,
    "front_caption_gap_lines": 1,
    "caption_rule_below": "left",
    "signature_date_beside": True,
    "signature_single_spaced": True,
    "signature_gap_lines": 2,
    "table_font_pt": 14,
    "table_header_fill": "1F3864",
    "table_total_fill": "D9E2F3",
    "table_border_pt": 1.0,
}
FRONT = (
    "Alpha Example, Esq.\nExample & Example, LLP\nOne Example Way\n\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n\n"
    "**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n\n**COUNTY OF EXAMPLETOWN**\n\n"
    "| | |\n| --- | --- |\n"
    "| GAMMA EXAMPLE, | Case No. CV-0001 |\n"
    "| Plaintiff, | |\n"
    "| v. | **PLAINTIFF'S MEDIATION BRIEF** |\n"
    "| DELTA EXAMPLE, | Mediator: Epsilon Example |\n"
    "| Defendant. | Mediation Date: 11/17/26 |\n"
    "| | Time: 1:30 pm |\n"
    "| | Trial Date: 2/9/27 |\n\n"
)
SIGNATURE = (
    "\n\nDated: __________\n\n**EXAMPLE & EXAMPLE, LLP**\n\nBy: ______________________________\n"
    "Alpha Example, Esq.\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n"
)
CHART = (
    "| Category | Amount |\n| --- | --- |\n| Past | $1.00 |\n| Future | $2.00 |\n| Other | $3.00 |\n| TOTAL | $6.00 |"
)
BODY = (
    "# I. INTRODUCTION\n\nThis case arises from **an example** and *another*.\n\n"
    "## A. A Lettered Heading\n\nA plain paragraph.\n\n"
    "### 1. A Numbered Heading\n\nAnother paragraph.\n\n"
    "***Example Provider***\n\nA provider's paragraph.\n\n"
    f"{CHART}\n\n# II. CONCLUSION\n\nThe last paragraph."
)
MD = FRONT + BODY + SIGNATURE
OTHER = (
    "Beta Other, Esq.\nOther Firm, LLP\nTwo Other Road\nOtherville, California\nTelephone: (000) 000-0000\n\n"
    "Attorneys for Plaintiffs\nZETA OTHER and ETA OTHER\n\n"
    "**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n\n**COUNTY OF OTHERTOWN**\n\n"
    "| | |\n| --- | --- |\n"
    "| ZETA OTHER, | Case No. CV-0002 |\n"
    "| Plaintiff, | |\n"
    "| v. | **PLAINTIFF'S MEDIATION BRIEF** |\n"
    "| THETA OTHER, | Mediator: Iota Other |\n"
    "| Defendant. | Mediation Date: 1/2/27 |\n"
    "| | Time: 9:00 am |\n"
    "| | Trial Date: 3/4/27 |\n\n"
    "# I. INTRODUCTION\n\nA different brief, longer, with {{ATTORNEY: a decision}} in it.\n\n"
    "A second paragraph.\n\n"
    "| Category | Billed | Howell Amount |\n| --- | --- | --- |\n| Past | $4.00 | $3.00 |\n| TOTAL | $4.00 | $3.00 |\n\n"
    "| Provider | Dates | Billed | Howell | Status |\n| --- | --- | --- | --- | --- |\n"
    "| Clinic | 1/1/26 | $4.00 | $3.00 | Paid |\n| TOTAL | | $4.00 | $3.00 | |\n\n"
    "# II. CONCLUSION\n\nThe end."
    "\n\nDated: __________\n\n**OTHER FIRM, LLP**\n\nBy: ______________________________\n"
    "Beta Other, Esq.\nAttorneys for Plaintiffs\nZETA OTHER and ETA OTHER\n"
)


def _fmt():
    f = copy.deepcopy(firm_data()["format"])
    f["layout"][CLS] = {**f["layout"][CLS], **ACCEPTED_LAYOUT}
    return f


@pytest.fixture(scope="module")
def accepted(tmp_path_factory) -> bytes:
    """The brief an attorney accepted: laid by the renderer on pleading paper."""
    fmt = _fmt()
    path, _ = render.render(MD, CLS, tmp_path_factory.mktemp("accepted") / "accepted.docx", fmt)
    pleading.apply_for(path, fmt, CLS)
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i.filename)
            if i.filename == "docProps/core.xml":
                data = re.sub(rb"(<dc:creator>)[^<]*", rb"\1A Firm Person", data)
                data = re.sub(rb"(<cp:lastModifiedBy>)[^<]*", rb"\1A Firm Person", data)
            out.writestr(i, data)
    return buf.getvalue()


@pytest.fixture(scope="module")
def tmpl(accepted) -> firmbase.FirmBase:
    blob, _notes = brief_base.adopt(accepted, _accepted(accepted))
    return firmbase.FirmBase.load(CLS, firmbase.Source("m", "f", "A firm brief.docx", "0" * 64), blob)


def _accepted(blob: bytes, **over) -> firmbase.Source:
    """The record of an attorney's acceptance of exactly these bytes."""
    fields = {
        "matter_id": "m",
        "file_id": "f",
        "name": "A firm brief.docx",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "version": "v1",
        "accepted": "his Word save",
    }
    return firmbase.Source(**{**fields, **over})


def _fill(base, md) -> bytes:
    blob, proof = brief_base.fill(base, md)
    assert proof.lost == [], proof.lost
    return blob


def _doc(blob: bytes):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT))


def _words(blob: bytes) -> str:
    return " ".join(t.text or "" for t in _doc(blob).iter(f"{W}t"))


def _edit(blob: bytes, change) -> bytes:
    root = _doc(blob)
    change(root)
    return package(blob, root)


# ---- adoption ---------------------------------------------------------------------------
def test_the_base_holds_no_words_of_the_brief(tmpl):
    for t in _doc(tmpl.bytes).iter(f"{W}t"):
        assert SLOT.match(t.text or "") or re.fullmatch(r"\{\{[a-z.]+\}\}", t.text or ""), "a word survived"
    assert {"body.h1", "body.h2", "body.h3", "body.p", "body.provider", "body.chart2", "body", "cap.0", "cap.1"} <= set(
        tmpl.slots
    )


def test_an_operator_render_nobody_accepted_is_refused(accepted):
    """Saved under a person's name, as an attorney's Word save of our file is: its
    styles still say whose it is, and no record says he accepted it."""
    assert b"A Firm Person" in zipfile.ZipFile(io.BytesIO(accepted)).read("docProps/core.xml")
    assert firmbase.operator_authored(accepted)
    with pytest.raises(DraftingFailed, match="firm_base_unfit: the file is the Operator's own"):
        brief_base.adopt(accepted, _accepted(accepted, version="", accepted=""))


@pytest.mark.parametrize(
    "over", [{"version": ""}, {"accepted": ""}, {"file_id": ""}, {"matter_id": ""}, {"sha256": "0" * 64}]
)
def test_an_acceptance_record_missing_a_field_or_for_other_bytes_is_refused(accepted, over):
    with pytest.raises(DraftingFailed, match="no accepted save is recorded"):
        brief_base.adopt(accepted, _accepted(accepted, **over))


def test_a_firms_own_file_needs_no_acceptance(tmp_path):
    d = docx.Document()
    d.core_properties.author = "A Firm Person"
    buf = io.BytesIO()
    d.save(buf)
    assert firmbase.may_adopt(buf.getvalue(), firmbase.Source("m", "f", "n", "0" * 64))


def test_a_role_laid_two_ways_keeps_the_commonest_and_says_so(accepted):
    def twist(root):
        p = next(
            p for p in root.iter(f"{W}p") if "A provider's paragraph" in "".join(t.text or "" for t in p.iter(f"{W}t"))
        )
        p.find(f"{W}pPr/{W}ind").set(f"{W}firstLine", "99")

    blob, notes = _adopt_edited(accepted, twist)
    assert any(n.startswith("p:") for n in notes), notes


def _adopt_edited(blob, change):
    root = _doc(blob)
    change(root)
    edited = package(blob, root)
    return brief_base.adopt(edited, _accepted(edited))


def test_a_brief_with_no_heading_style_reads_its_roles_from_their_formatting(accepted):
    """A firm's brief often lays its headings by hand."""

    def unstyle(root):
        for p in root.iter(f"{W}p"):
            for st in p.findall(f"{W}pPr/{W}pStyle"):
                if "Heading" in (st.get(f"{W}val") or ""):
                    st.getparent().remove(st)
            text = "".join(t.text or "" for t in p.iter(f"{W}t"))
            if re.match(r"^(I|II)\. |^A\. |^1\. ", text):
                for r in p.findall(f"{W}r"):
                    rpr = r.find(f"{W}rPr")
                    if rpr.find(f"{W}b") is None:
                        rpr.insert(0, etree.Element(f"{W}b"))

    blob, _ = _adopt_edited(accepted, unstyle)
    base = firmbase.FirmBase.load(CLS, firmbase.Source("m", "f", "n", "0"), blob)
    assert {"body.h1", "body.h2", "body.h3"} <= set(base.slots)


# ---- the render ----------------------------------------------------------------------
def test_the_same_words_on_the_base_are_the_accepted_layout(accepted, tmpl):
    out = _fill(tmpl, MD)
    assert firmbase.layout_diff(accepted, out) == []
    a, b = docx.Document(io.BytesIO(accepted)), docx.Document(io.BytesIO(out))
    assert [p.text for p in a.paragraphs] == [p.text for p in b.paragraphs]
    assert [[c.text for r in t.rows for c in r.cells] for t in a.tables] == [
        [c.text for r in t.rows for c in r.cells] for t in b.tables
    ]


def test_every_part_but_the_document_is_the_bases_bytes(tmpl):
    out = _fill(tmpl, OTHER)
    a, b = zipfile.ZipFile(io.BytesIO(tmpl.bytes)), zipfile.ZipFile(io.BytesIO(out))
    assert a.namelist() == b.namelist()
    assert [n for n in a.namelist() if a.read(n) != b.read(n)] == [DOCUMENT]
    assert paper_of(out) == "pleading"


def test_a_different_brief_keeps_the_front_gaps_the_rule_and_the_signature(tmpl):
    out = _fill(tmpl, OTHER)
    d = docx.Document(io.BytesIO(out))
    texts = [p.text for p in d.paragraphs]
    assert texts[:5] == [
        "Beta Other, Esq.",
        "Other Firm, LLP",
        "Two Other Road",
        "Otherville, California",
        "Telephone: (000) 000-0000",
    ]
    assert texts[5] == ""  # the attorney gap, where the base has it
    at = texts.index("ZETA OTHER and ETA OTHER")
    assert texts[at + 1 : at + 3] == ["", ""]  # the two lines to the court
    assert any(t.startswith("Dated: __________") and "\tBy: ___" in t for t in texts)
    assert format_check.frontpage.check(d, _fmt(), CLS) == []
    assert format_check.briefpage.check(d, _fmt(), CLS) == []
    assert "GAMMA" not in _words(out) and "EXAMPLETOWN" not in _words(out)


def test_the_caption_lands_the_same_whichever_way_the_draft_divides_its_rows(tmpl):
    rows = MD.replace(
        "| Defendant. | Mediation Date: 11/17/26 |\n| | Time: 1:30 pm |\n",
        "| Defendant. | Mediation Date: 11/17/26<br>Time: 1:30 pm |\n",
    )
    assert rows != MD
    assert etree.tostring(_doc(_fill(tmpl, MD))) == etree.tostring(_doc(_fill(tmpl, rows)))


def test_more_caption_lines_than_slots_stay_on_the_layout(tmpl):
    md = MD.replace("| | Trial Date: 2/9/27 |", "| | Trial Date: 2/9/27 |\n| | Department: 42 |")
    assert "Department: 42" in _words(_fill(tmpl, md))


def test_a_wider_chart_keeps_the_width(accepted, tmpl):
    d = docx.Document(io.BytesIO(_fill(tmpl, OTHER)))
    wide = next(t for t in d.tables if t.rows[0].cells[0].text == "Provider")
    grid = [int(c.get(f"{W}w")) for c in wide._tbl.find(f"{W}tblGrid")]
    chart = next(t for t in _doc(tmpl.bytes).iter(f"{W}tbl") if "body.chart2" in slot_names(t))
    assert sum(grid) == sum(int(c.get(f"{W}w")) for c in chart.find(f"{W}tblGrid")) and len(grid) == 5


def test_a_wider_chart_keeps_the_label_columns_ratio():
    widths = _column_widths([6000, 3000], 3)
    assert widths == [4500, 2250, 2250] and sum(widths) == 9000


def test_a_total_row_takes_the_total_and_the_header_its_own(tmpl):
    rows = docx.Document(io.BytesIO(_fill(tmpl, MD))).tables[1].rows

    def fill(r):
        return r.cells[0]._tc.tcPr.find(f"{W}shd")

    assert fill(rows[0]).get(f"{W}fill") == "1F3864"
    assert fill(rows[-1]).get(f"{W}fill") == "D9E2F3"


@pytest.mark.parametrize(
    ("md", "why"),
    [
        (MD.replace("A plain paragraph.", "- a bullet"), "bullet"),
        (FRONT + "# I. ONE\n\nBody.", "no signature block"),
        ("# I. ONE\n\nBody." + SIGNATURE, "no caption table"),
    ],
)
def test_what_the_base_cannot_place_stops_the_job(tmpl, md, why):
    with pytest.raises(DraftingFailed, match=f"firm_base_unfit: .*{why}") as e:
        brief_base.fill(tmpl, md)
    assert REASON.match(str(e.value))


def test_a_refused_file_is_never_written(tmp_path, tmpl, monkeypatch):
    def lost(base, md):
        return b"x", firmbase.Proof(parts_differ=["forced"])

    monkeypatch.setattr(brief_base, "fill", lost)
    with pytest.raises(DraftingFailed, match="firm_base_lost: forced") as e:
        brief_base.render_into(MD, tmp_path / "out.docx", tmpl, "pleading")
    assert REASON.match(str(e.value))
    assert not (tmp_path / "out.docx").exists()


def test_words_the_draft_never_had_are_refused(tmpl, monkeypatch):
    real = firmbase.Fill.save

    def save(self):
        p = next(
            p for p in self.body.iter(f"{W}p") if "A plain paragraph." in "".join(t.text or "" for t in p.iter(f"{W}t"))
        )
        p.find(f"{W}r/{W}t").text = "Words the draft never had."
        return real(self)

    monkeypatch.setattr(firmbase.Fill, "save", save)
    _, proof = brief_base.fill(tmpl, MD)
    assert "the file does not read as the draft" in proof.lost


def test_the_wrong_paper_is_refused(tmp_path, tmpl):
    with pytest.raises(DraftingFailed, match="firm_base_unfit: the request is for plain paper"):
        brief_base.render_into(MD, tmp_path / "out.docx", tmpl, "plain")


# ---- the format check -----------------------------------------------------------------
def test_the_base_outranks_the_configs_chart_keys(tmp_path, tmpl):
    """The config says another colour and size; the base's brief still passes, and the
    same file against the config's keys is what would have failed."""
    path, _ = brief_base.render_into(MD, tmp_path / "out.docx", tmpl, "pleading")
    fmt = _fmt()
    fmt["layout"][CLS].update(table_header_fill="AAAAAA", table_total_fill="BBBBBB", table_font_pt=9)
    assert format_check.check(path, CLS, fmt, "", None, "pleading").fails != []
    res = format_check.check(path, CLS, brief_base.checked_format(fmt, CLS, tmpl), "", None, "pleading")
    assert [f for f in res.fails if f.startswith(("chart", "font", "first page", "signature", "pleading"))] == []


# ---- the config and the job -----------------------------------------------------------
def _with_base(inputs: Path, blob: bytes) -> None:
    (inputs / "mediation-brief-base.docx").write_bytes(blob)
    data = yaml.safe_load((inputs / "drafting-firm.yaml").read_text())
    data["format"]["firm_bases"] = {CLS: {"path": "mediation-brief-base.docx", "source": SOURCE}}
    data["format"]["layout"][CLS] = {**data["format"]["layout"][CLS], **ACCEPTED_LAYOUT}
    data["format"]["pleading_classes"] = [CLS]
    data["inputs"]["mediation-brief-base.docx"] = hashlib.sha256(blob).hexdigest()
    (inputs / "drafting-firm.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


SOURCE = {"matter_id": "m", "file_id": "f", "name": "n.docx", "sha256": "0" * 64}


def test_the_config_pins_the_base_and_names_its_class_and_source():
    data = firm_data()
    data["format"]["firm_bases"] = {"memo": {"path": "x.docx", "source": SOURCE}}
    assert any("format.firm_bases.memo" in e for e in firm.validate(_valid(data)))
    data["format"]["firm_bases"] = {CLS: {"path": "x.docx", "source": SOURCE}}
    assert any("format.firm_bases.mediation_brief: x.docx is not pinned" in e for e in firm.validate(_valid(data)))
    data["format"]["firm_bases"] = {CLS: {"path": "x.docx", "source": {"name": "n"}}}
    assert any("format.firm_bases.mediation_brief.source" in e for e in firm.validate(_valid(data)))
    data["format"]["firm_bases"] = {CLS: {"path": "x.docx", "source": {**SOURCE, "version": "v1"}}}
    assert any("an accepted save records both" in e for e in firm.validate(_valid(data)))


def _valid(data: dict) -> dict:
    """``data`` with every other file it names pinned."""
    data = copy.deepcopy(data)
    data["inputs"] = {"house-style.md": "0" * 64, "decl-2030-050.md": "0" * 64, "pos.md": "0" * 64}
    for entry in data["classes"].values():
        data["inputs"][entry["skeleton"]] = "0" * 64
        for p in entry["prompts"].values():
            data["inputs"][p] = "0" * 64
    return data


def test_a_firm_with_no_base_renders_as_before(tmp_path):
    assert firm.DraftingFirm(tmp_path, firm_data()).firm_base(CLS) is None


CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


def _job_brief() -> str:
    """The sections the format check requires, in a brief the gate passes."""
    body = "\n\n".join(f"# {s}\n\nBody text (ER record 1-15-26, p. 1)." for s in SECTIONS)
    return FRONT + body + "\n\n{{ATTORNEY: settlement authority}}" + SIGNATURE


def test_the_job_files_the_brief_it_made_in_the_firms_file(tmp_path, tmpl, monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    make_inputs(inputs)
    _with_base(inputs, tmpl.bytes)
    log: list[str] = []
    r = DraftingRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=ScriptedClient(draft=_job_brief()),
        log=log.append,
        readback_pause=0.0,
    )
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    rendered = json.loads((r.data / "rendered.json").read_text())
    assert any("firm's base" in n for n in rendered["render_report"])
    filed = Path(rendered["path"]).read_bytes()
    a, b = zipfile.ZipFile(io.BytesIO(tmpl.bytes)), zipfile.ZipFile(io.BytesIO(filed))
    assert [n for n in a.namelist() if n != DOCUMENT and a.read(n) != b.read(n)] == []
