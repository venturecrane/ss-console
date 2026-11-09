"""A firm's STYLES base pinned per document type (2026-11-09, firm format for
every drafted type): ``format.firm_bases.<type>.contract: styles``.

The firm's own Word file supplies the page setup, headers, footers, pleading
paper and styles; the renderer writes the body fresh. Proven here:

* the config takes ``contract: styles`` for every class, and ``marked`` stays
  the default with its two-class gate (the mediation brief pin is unchanged);
* the job renders on the pinned styles file on the composing AND the placing
  path, and the record names the pin and its contract;
* a styles base whose header already numbers the lines gets no second set;
* the format the check reads keeps every key the attorney authored, and the
  base's own styles do not override it;
* a discovery or deposition running title is not refused as case words;
* ``python -m jobs firmbase make --contract styles`` clears the body, cleans the
  package, refuses any client word left, and prints counts only.

Every fixture is made in the test; no client file is read.
"""

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
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Pt

import library_testkit as lt
from drafting_testkit import ScriptedClient, firm_data, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.__main__ import main as jobs_main
from jobs.drafting import base_lay, firm, firm_rules, format_check, library_pick, pleading
from jobs.drafting.run import DraftingRun
from jobs.library_setup import make as make_mod

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
SOURCE = {"matter_id": "m", "file_id": "f", "name": "", "sha256": "0" * 64}
STYLES_TYPES = ("discovery_set", "discovery_response", "memo", "depo_outline", "mediation_brief")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    spec = tmp_path / "specs"
    spec.mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(spec))


def _save(d) -> bytes:
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _firm_file(header: str = "EXAMPLE FIRM LLP") -> bytes:
    """A firm's Word file: its own Normal style (Arial 10, single spaced), a
    letterhead header, and a body the base must not keep."""
    d = docx.Document()
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(10)
    normal.paragraph_format.line_spacing = 1.0
    d.sections[0].header.paragraphs[0].text = header
    d.add_paragraph("A body paragraph of some matter.")
    return _save(d)


def _numbered_header(lines: int = 28) -> bytes:
    """A firm's pleading paper: its header draws the line numbers 1..N in a text
    box of its own (not ours: another name, no margin rules)."""
    d = docx.Document()
    paras = "".join(f"<w:p><w:r><w:t>{n}</w:t></w:r></w:p>" for n in range(1, lines + 1))
    body = (
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></wps:spPr>'
        f"<wps:txbx><w:txbxContent>{paras}</w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp>"
    )
    xml = pleading._anchor(9, "Firm Pleading Numbers", -500000, 0, 365760, 8000000, "margin", body)
    d.sections[0].header.paragraphs[0].add_run()._r.append(parse_xml(xml))
    return _save(d)


def _line_numbering_setting() -> bytes:
    """A firm's pleading paper that numbers lines with Word's own setting."""
    d = docx.Document()
    d.sections[0]._sectPr.append(
        parse_xml(
            '<w:lnNumType xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'w:countBy="1" w:restart="newPage"/>'
        )
    )
    return _save(d)


def _with_styles_pin(inputs: Path, cls: str, blob: bytes, layout: dict | None = None) -> None:
    rel = f"{cls}-styles-base.docx"
    (inputs / rel).write_bytes(blob)
    data = yaml.safe_load((inputs / "drafting-firm.yaml").read_text())
    data["format"]["firm_bases"] = {cls: {"path": rel, "contract": "styles", "source": SOURCE}}
    if layout is not None:
        data["format"]["layout"][cls] = layout
    data["inputs"][rel] = hashlib.sha256(blob).hexdigest()
    (inputs / "drafting-firm.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _run(root: Path, cls: str, blob: bytes, layout: dict | None = None) -> DraftingRun:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pricing.json").write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs")
    _with_styles_pin(inputs, cls, blob, layout)
    return DraftingRun(
        make_job(root / "job", cls=cls),
        inputs_dir=str(inputs),
        pricing=str(root / "pricing.json"),
        seat_factory=lambda: seat_with(standard_docs()),
        client=ScriptedClient(),
        log=lambda _m: None,
        readback_pause=0.0,
    )


# ---- 1. the config ------------------------------------------------------------------------


@pytest.mark.parametrize("cls", STYLES_TYPES)
def test_a_styles_pin_is_valid_for_every_drafted_type(cls):
    data = firm_data()
    data["format"]["firm_bases"] = {cls: {"path": "b.docx", "contract": "styles", "source": SOURCE}}
    data["inputs"] = {"b.docx": "0" * 64}
    assert not [e for e in firm._firm_bases(data["format"]["firm_bases"])]


def test_marked_stays_the_default_and_keeps_its_two_class_gate():
    marked = {"depo_outline": {"path": "b.docx", "source": SOURCE}}
    assert "marked file" in firm._firm_bases(marked)[0]
    explicit = {"discovery_set": {"path": "b.docx", "contract": "marked", "source": SOURCE}}
    assert "marked file" in firm._firm_bases(explicit)[0]
    assert firm._firm_bases({"mediation_brief": {"path": "b.docx", "source": SOURCE}}) == []
    wrong = {"memo": {"path": "b.docx", "contract": "letterhead", "source": SOURCE}}
    assert "contract: expected one of" in firm._firm_bases(wrong)[0]


def test_firm_base_answers_marked_pins_only_and_styles_base_the_styles_pins(tmp_path):
    data = firm_data()
    data["format"]["firm_bases"] = {
        "mediation_brief": {"path": "brief.docx", "source": SOURCE},
        "discovery_set": {"path": "set.docx", "contract": "styles", "source": SOURCE},
    }
    f = firm.DraftingFirm(tmp_path, data)
    assert f.firm_base("mediation_brief") == (tmp_path / "brief.docx", SOURCE)
    assert f.styles_base("mediation_brief") is None
    assert f.firm_base("discovery_set") is None
    assert f.styles_base("discovery_set") == (tmp_path / "set.docx", SOURCE)


# ---- 1. the job renders on it, composing and placing, and says so -------------------------


@pytest.mark.parametrize("marked_ok", [True, False], ids=["composing", "placing"])
@pytest.mark.parametrize("cls", ["discovery_set", "discovery_response", "memo", "depo_outline"])
def test_the_job_is_made_in_the_pinned_styles_file_on_both_paths(cls, marked_ok, tmp_path):
    blob = _firm_file()
    r = _run(tmp_path, cls, blob)
    made_in = library_pick.pick_base(r, marked_ok=marked_ok)
    assert made_in.styles == blob and made_in.held is None
    rec = lt.record(r.data)
    assert rec["resolution"]["status"] == "none_accepted"
    assert rec["base"] == {
        "source": "pinned_firm_input",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "name": f"{cls}-styles-base.docx",
        "contract": "styles",
    }
    path, _report, _laid, _checked = base_lay.lay_out(
        firm_rules.PROBE_MD, tmp_path / "out.docx", cls, r.firm.data["format"], "plain", made_in.held, None,
        styles=made_in.styles,
    )  # fmt: skip
    filed = docx.Document(str(path))
    assert filed.sections[0].header.paragraphs[0].text == "EXAMPLE FIRM LLP"
    assert "A body paragraph of some matter." not in [p.text for p in filed.paragraphs]


# ---- 2. no second set of line numbers -----------------------------------------------------


def _numbered_boxes(path: Path) -> int:
    z = zipfile.ZipFile(path)
    n = 0
    for name in z.namelist():
        if re.fullmatch(r"word/header\d*\.xml", name):
            for box in re.findall(r"<w:txbxContent\b[^>]*>(.*?)</w:txbxContent>", z.read(name).decode(), re.S):
                words = " ".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", box)).split()
                n += words[:3] == ["1", "2", "3"]
    return n


def _lay_pleading(tmp_path: Path, base: bytes | None) -> tuple[Path, list[str], dict]:
    fmt = copy.deepcopy(firm_data()["format"])
    path, _r, laid, checked = base_lay.lay_out(
        firm_rules.PROBE_MD, tmp_path / "p.docx", "discovery_set", fmt, "pleading", None, None, styles=base
    )
    return path, laid, checked


def _paper_fails(path: Path, fmt: dict) -> list[str]:
    blob = path.read_bytes()
    return pleading.check(docx.Document(io.BytesIO(blob)), blob, pleading.spec_for(fmt), None)


def test_a_styles_base_that_numbers_its_lines_in_a_text_box_gets_no_second_set(tmp_path):
    path, laid, fmt = _lay_pleading(tmp_path, _numbered_header())
    assert _numbered_boxes(path) == 1
    assert pleading.BOX_NAME not in zipfile.ZipFile(path).read("word/header1.xml").decode()
    assert any("no second set" in n for n in laid)
    assert not [f for f in _paper_fails(path, fmt) if "number" in f or "box" in f or "rules" in f]


def test_a_styles_base_that_numbers_its_lines_by_setting_ends_with_one_set(tmp_path):
    """Word's line-numbering setting is read as the firm's numbering, and the
    renderer drops it from a base (``docx_base``: plain paper, never pleading),
    so the pleading paper laid on the rendered file is the only set."""
    base = _line_numbering_setting()
    assert pleading.drawn_numbers(base) == (pleading.SETTING_DRAWN, 0)
    path, laid, fmt = _lay_pleading(tmp_path, base)
    assert docx.Document(str(path)).sections[0]._sectPr.find(qn("w:lnNumType")) is None
    assert _numbered_boxes(path) == 1
    assert not [f for f in _paper_fails(path, fmt) if "number" in f or "box" in f or "rules" in f]


def test_a_base_that_numbers_nothing_still_gets_ours(tmp_path):
    path, laid, fmt = _lay_pleading(tmp_path, _firm_file())
    assert _numbered_boxes(path) == 1
    assert pleading.BOX_NAME in zipfile.ZipFile(path).read("word/header1.xml").decode()
    assert not any("no second set" in n for n in laid)
    assert not [f for f in _paper_fails(path, fmt) if "number" in f or "box" in f or "rules" in f]


def test_the_firms_paper_with_another_line_count_is_refused(tmp_path):
    path, _laid, fmt = _lay_pleading(tmp_path, _numbered_header(26))
    assert any("numbers 26 lines" in f for f in _paper_fails(path, fmt))


# ---- 3. a stated rule wins over the base ---------------------------------------------------

#: The attorney's spacing rules, as Chris's D3 and R1 state them for his papers.
STATED = {"line_spacing": 2.0, "justify": True, "first_line_indent_in": 0.5, "page_numbers_always": True}


def test_the_format_the_check_reads_keeps_every_authored_key_and_the_base_does_not_override_it(tmp_path):
    cls = "memo"
    r = _run(tmp_path, cls, _firm_file(), layout=dict(STATED))
    made_in = library_pick.pick_base(r)
    fmt = r.firm.data["format"]
    path, _r, _l, checked = base_lay.lay_out(
        firm_rules.PROBE_MD, tmp_path / "m.docx", cls, fmt, "plain", made_in.held, None, styles=made_in.styles
    )
    assert made_in.styles is not None
    assert checked["layout"][cls] == STATED
    assert format_check.check(path, cls, checked, "", None, "plain").fails == []
    body = [p for p in docx.Document(str(path)).paragraphs if p.text.startswith("A first paragraph")]
    assert body and body[0].paragraph_format.line_spacing == 2.0


def test_the_check_would_see_a_base_that_overrode_the_stated_rule(tmp_path):
    """The falsifier: the same file checked against a stricter stated spacing fails."""
    cls = "memo"
    r = _run(tmp_path, cls, _firm_file(), layout=dict(STATED))
    made_in = library_pick.pick_base(r)
    fmt = r.firm.data["format"]
    path, _r, _l, checked = base_lay.lay_out(
        firm_rules.PROBE_MD, tmp_path / "m.docx", cls, fmt, "plain", None, None, styles=made_in.styles
    )
    stricter = copy.deepcopy(checked)
    stricter["layout"][cls]["line_spacing"] = 1.5
    assert format_check.check(path, cls, stricter, "", None, "plain").fails


def test_the_body_on_a_firms_file_carries_the_role_the_check_reads_and_the_firms_styles_stay(tmp_path):
    firm_file = _firm_file()
    path, _r, _l, _c = base_lay.lay_out(
        firm_rules.PROBE_MD, tmp_path / "m.docx", "memo", copy.deepcopy(firm_data()["format"]), "plain", None, None,
        styles=firm_file,
    )  # fmt: skip
    filed = docx.Document(str(path))
    assert "SMD Body" in {p.style.name for p in filed.paragraphs if p.text.strip()}
    assert filed.styles["SMD Body"].base_style.name == "Normal"
    assert filed.styles["Normal"].font.name == "Arial" and filed.styles["Normal"].font.size == Pt(10)


def test_the_chat_tools_render_on_a_firms_file_is_unchanged():
    """Only the drafting job asks for role styles; the connector's default render
    of a firm's file still defines none (``docx_base``: their styles are theirs)."""
    from smokeball_connector.docx_format import render_document

    blob, _report = render_document("# I. Heading\n\nBody.", "memo", _firm_file())
    assert "SMD Body" not in {s.name for s in docx.Document(io.BytesIO(blob)).styles}


# ---- 4. running-title words ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Plaintiff's Special Interrogatories, Set One",
        "Plaintiff's Form Interrogatories, Set Two",
        "Defendant's Requests for Production of Documents, Set Three",
        "Plaintiff's Request for Admissions, Set One",
        "Plaintiff's Responses to Special Interrogatories, Set One",
        "Response to Request for Admission",
        "Deposition Outline",
        "Notice of Deposition",
        "Propounding Party",
        "Responding Party",
    ],
)
def test_a_discovery_or_deposition_running_title_is_not_case_words(line):
    assert make_mod.identity_only(line, [])


@pytest.mark.parametrize("line", ["Jane Roe's Special Interrogatories, Set One", "Deposition Outline of Acme Corp"])
def test_a_running_title_that_names_a_party_is_still_refused(line):
    assert not make_mod.identity_only(line, [])


# ---- 5. firmbase make ----------------------------------------------------------------------


def _filed_discovery(footer: str, *, sections: int = 1) -> bytes:
    """A firm's filed discovery set, made up: client words in the body, the
    core properties, a custom XML part and a document variable; a letterhead
    header; a footer with a page number and its running title."""
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "EXAMPLE FIRM LLP"
    fp = d.sections[0].footer.paragraphs[0]
    fp.add_run()._r.append(parse_xml(
        '<w:fldSimple xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:instr=" PAGE ">'
        "<w:r><w:t>1</w:t></w:r></w:fldSimple>"
    ))  # fmt: skip
    d.sections[0].footer.add_paragraph(footer)
    d.add_paragraph("ZEBULON QUILLFEATHER v. ACME HOLDINGS, Case No. 99-1234")
    for _ in range(sections - 1):
        d.add_section()
    cp = d.core_properties
    cp.title, cp.author, cp.subject, cp.keywords = "Quillfeather SROG", "Paralegal Person", "Quillfeather", "Acme"
    settings = d.settings.element
    settings.append(parse_xml(
        '<w:docVars xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docVar w:name="MatterName" w:val="Quillfeather"/></w:docVars>'
    ))  # fmt: skip
    blob = _save(d)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            out.writestr(i, src.read(i.filename))
        out.writestr("customXml/item9.xml", "<matter>Quillfeather v. Acme</matter>")
    return buf.getvalue()


def _customer(tmp_path: Path) -> Path:
    p = tmp_path / "customer.yaml"
    p.write_text(yaml.safe_dump({"firm_identity": {"name": "Example Firm LLP"}}))
    return p


def _make(tmp_path: Path, blob: bytes, kind: str = "discovery_set") -> tuple[int, Path]:
    src, out = tmp_path / "in.docx", tmp_path / "base.docx"
    src.write_bytes(blob)
    code = jobs_main(
        ["firmbase", "make", "--kind", kind, "--contract", "styles", "--in", str(src), "--out", str(out),
         "--customer", str(_customer(tmp_path))]
    )  # fmt: skip
    return code, out


CLIENT_WORDS = ("Quillfeather", "ZEBULON", "ACME", "Acme", "Paralegal", "99-1234")


def test_firmbase_make_writes_a_clean_styles_base_and_prints_counts_only(tmp_path, capsys):
    code, out = _make(tmp_path, _filed_discovery("Plaintiff's Special Interrogatories, Set One"))
    printed = capsys.readouterr().out
    assert code == 0 and out.is_file()
    said = json.loads(printed)
    assert said["made"] is True and said["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert said["residue"]["page_numbering"] >= 1 and said["residue"]["firm_identity"] >= 1
    assert said["dropped"]["custom_xml_parts"] >= 1 and said["dropped"]["document_variables"] == 1
    assert not any(w in printed for w in CLIENT_WORDS) and "Interrogatories" not in printed
    z = zipfile.ZipFile(out)
    everything = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "replace")
    assert not any(w in everything for w in CLIENT_WORDS)
    assert not [n for n in z.namelist() if n.startswith("customXml/")]
    made = docx.Document(str(out))
    assert [p.text for p in made.paragraphs if p.text.strip()] == []
    assert made.sections[0].header.paragraphs[0].text == "EXAMPLE FIRM LLP"
    assert made.core_properties.title == "" and made.core_properties.author == ""


def test_firmbase_make_refuses_a_footer_that_names_the_case_and_writes_nothing(tmp_path, capsys):
    code, out = _make(tmp_path, _filed_discovery("Quillfeather v. Acme Holdings"))
    printed = capsys.readouterr().out
    assert code == 1 and not out.exists()
    said = json.loads(printed)
    assert said["made"] is False and said["code"] == "case_words_remain"
    assert not any(w in printed for w in CLIENT_WORDS)


def test_firmbase_make_refuses_a_file_of_more_than_one_section(tmp_path, capsys):
    code, out = _make(tmp_path, _filed_discovery("Deposition Outline", sections=2), kind="depo_outline")
    assert code == 1 and not out.exists()
    assert json.loads(capsys.readouterr().out)["code"] == "multi_section"
