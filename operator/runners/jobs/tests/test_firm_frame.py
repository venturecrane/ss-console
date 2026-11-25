"""A firm's discovery paper made into its library file with its standard wording
kept and its case facts out (``library_setup/frame_make.py``), and a draft that
uses that wording (``drafting/firm_frame.py``). Every name, number and date is
made up."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import docx
import pytest

from jobs.drafting import firm_frame as F
from jobs.library_setup import frame_lines as L, frame_make as FM, stock as ST
from jobs.library_setup.make import Unusable

FIRM = "Example & Example, LLP"
IDENTITY = [FIRM, "One Example Way", "Exampletown, California 90000", "(000) 000-0000", "Attorney 1, Esq."]
BLOCKS = [["Attorney 1, Esq.", FIRM]]
CLIENT, OTHER_SIDE = "ROWAN EXAMPLAR", "WIDGETCO EXAMPLE, INC."
PRELIM = (
    f'Plaintiff {CLIENT} ("Responding Party") hereby responds to the Special Interrogatories, Set ONE, '
    f"propounded by Defendant {OTHER_SIDE} These responses are made solely for the purpose of this action. "
    "Responding Party has not completed discovery, investigation or preparation for trial, and these responses "
    "are given without prejudice to the right to produce evidence of any subsequently discovered fact. "
    "Responding Party reserves the right to supplement these responses pursuant to Code of Civil Procedure "
    "section 2030.310."
)
OBJECTION = (
    "Responding Party objects to each interrogatory to the extent it seeks information protected by the "
    "attorney-client privilege or the attorney work product doctrine."
)
VERIFY = (
    f"I, {CLIENT}, declare: I am the Plaintiff in this action. I have read the foregoing responses and know "
    "their contents. The matters stated are true of my own knowledge. I declare under penalty of perjury under "
    "the laws of the State of California that the foregoing is true and correct. Executed on November 4, 2026, "
    "at Exampletown, California."
)
#: What the frame keeps of them: the words that name the kind of set are marked places.
PRELIM_END = "section [[v.code_chapter]].310."
MARKED_OBJECTION = OBJECTION.replace("each interrogatory", "each [[v.item]]")
CASE_WORDS = (CLIENT, "WIDGETCO", "March 3", "Sam Server", "EX-0000-123", "Exampleville", "November 3")


def _paper(
    prelim: str = PRELIM,
    author: str = "Alex Example",
    *,
    footer: tuple[str, ...] = (),
    extra_front: tuple[str, ...] = (),
    responding: str = f"Plaintiff {CLIENT}",
    signature: tuple[str, ...] = ("Attorney 1, Esq.",),
    dated: str = "DATED: November 3, 2026",
    one_paragraph_closing: bool = False,
    tail: tuple[str, ...] = (),
    executed: str = "November 4, 2026",
    other: str = OTHER_SIDE,
) -> bytes:
    d = docx.Document()
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    d.core_properties.created = datetime(2021, 6, 7, 8, 9, 10)
    d.core_properties.modified = datetime(2022, 6, 7, 8, 9, 10)
    d.sections[0].header.paragraphs[0].text = FIRM
    for i, line in enumerate(footer):
        para = d.sections[0].footer.paragraphs[0] if i == 0 else d.sections[0].footer.add_paragraph()
        para.text = line
    lines: list[str | tuple[str, ...]] = [
        "Attorney 1, Esq.",
        FIRM,
        f"Attorneys for Plaintiff {CLIENT}",
        "SUPERIOR COURT OF THE STATE OF CALIFORNIA",
        f"{CLIENT}, Plaintiff, v. {other}, Defendant.",
        "Case No. EX-0000-123",
        "PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE",
        f"PROPOUNDING PARTY:\tDefendant {other}",
        f"RESPONDING PARTY:\t{responding}",
        "SET NO.:\tONE",
        "PRELIMINARY STATEMENT",
        prelim,
        "GENERAL OBJECTIONS",
        OBJECTION,
        *extra_front,
        "SPECIAL INTERROGATORY NO. 1:",
        "State the date of the incident.",
        "RESPONSE TO SPECIAL INTERROGATORY NO. 1:",
        "The incident occurred on March 3, 2025 at Exampleville.",
    ]
    signing = (dated, FIRM, "By: ______________", *signature, f"Attorneys for Plaintiff {CLIENT}")
    lines += [signing] if one_paragraph_closing else list(signing)
    lines += [
        "VERIFICATION",
        VERIFY.replace("November 4, 2026", executed),
        "______________",
        CLIENT,
        *tail,
        "PROOF OF SERVICE",
        "I, Sam Server, served the foregoing.",
    ]
    for line in lines:
        if isinstance(line, tuple):  # one paragraph, its lines joined by line breaks
            para = d.add_paragraph()
            for i, piece in enumerate(line):
                run = para.add_run(piece)
                if i < len(line) - 1:
                    run.add_break()
        else:
            d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _all_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith((".xml", ".rels")))


def _blank_form():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hermes-smd-overlay"))
    from establish_intake import blank_form

    return blank_form


def test_the_firms_standard_wording_is_kept_and_every_case_fact_goes():
    made = FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS)
    text = _all_text(made.data)
    for word in CASE_WORDS:
        assert word not in text, word
    for kept in ("PRELIMINARY STATEMENT", "GENERAL OBJECTIONS", "attorney work product doctrine", "VERIFICATION"):
        assert kept in text, kept
    assert "Code of Civil Procedure" in text and "penalty of perjury" in text and "By:" in text
    assert made.set_type == "special_interrogatories"
    assert made.counts["front_refused"] == 0 and made.counts["closing_refused"] == 0
    assert made.counts["kept_front"] == 4 and made.counts["kept_closing"] == 9
    checked = _blank_form().check(made.data, made.fixed_strings)
    assert checked.ok, checked.problems
    assert all(len(s) <= FM.MAX_FIXED_CHARS for s in made.fixed_strings)


def test_the_frame_reads_back_as_the_firms_words_with_its_marked_places():
    frame = F.read(FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS).data)
    assert frame is not None and frame.set_type == "special_interrogatories"
    front = [t for t, _h in frame.blocks[F.FRONT]]
    assert front[0] == "PRELIMINARY STATEMENT" and frame.blocks[F.FRONT][0][1] is True
    assert front[1].startswith('[[v.responding_party]] ("Responding Party") hereby responds')
    assert "the [[v.set_name]], Set [[v.set_number]], propounded by [[v.propounding_party]]" in front[1]
    assert front[1].endswith(PRELIM_END) and front[3] == MARKED_OBJECTION
    closing = [t for t, _h in frame.blocks[F.CLOSING]]
    assert closing[0] == "DATED: [[v.date]]" and "[[v.signer]]" in closing
    assert any(t.startswith("I, [[v.responding_name]], declare") and "Executed on [[v.date]]" in t for t in closing)
    assert frame.marks(F.FRONT) == {
        "responding_party",
        "propounding_party",
        "set_number",
        "set_name",
        "item",
        "code_chapter",
    }


def test_a_paragraph_that_names_someone_else_is_dropped_alone_and_its_block_kept():
    named = PRELIM + " Ms. Quillon reviewed these responses."
    made = FM.make_frame("discovery_response", _paper(named), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 1 and made.counts["kept_front"] == 3
    assert "Quillon" not in _all_text(made.data)
    frame = F.read(made.data)
    assert frame.blocks[F.FRONT][1] == (F.HOLE, False) and frame.blocks[F.FRONT][3][0] == MARKED_OBJECTION
    made2, answer = ST.made_for("discovery_response", _paper(named), IDENTITY, BLOCKS)
    assert made2 is not None and answer["status"] == "made"


# ---- the firm's real papers, 2026-11-25: each cause, on an invented paper shaped like it ----


def _clean(made) -> None:
    checked = _blank_form().check(made.data, made.fixed_strings)
    assert checked.ok, checked.problems


def test_a_footer_title_that_names_the_parties_keeps_its_title_words_and_a_dash_page_line_passes():
    footer = (f"PLAINTIFF {CLIENT}'S RESPONSES TO DEFENDANT {OTHER_SIDE}'S SPECIAL INTERROGATORIES, SET ONE", "- 9 -")
    made = FM.make_frame("discovery_response", _paper(footer=footer), IDENTITY, BLOCKS)
    assert made.counts["footer_titles"] == 1
    text = _all_text(made.data)
    assert CLIENT not in text and "WIDGETCO" not in text and "[[v." not in _footer_text(made.data)
    assert "RESPONSES TO DEFENDANT SPECIAL INTERROGATORIES SET ONE" in _footer_text(made.data)
    _clean(made)
    for line in ("-9-", "- 9 -", "Page 9 of 12"):
        assert _blank_form().check(_with_footer_line(made.data, line), made.fixed_strings).ok, line


def _footer_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return "\n".join(z.read(n).decode() for n in z.namelist() if n.startswith("word/footer"))


def _with_footer_line(blob: bytes, line: str) -> bytes:
    d = docx.Document(io.BytesIO(blob))
    d.sections[0].footer.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_the_drafts_title_replaces_the_footer_title_when_rendered(tmp_path, monkeypatch):
    from jobs.drafting import base_lay, library_pick

    footer = (f"PLAINTIFF {CLIENT}'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE",)
    blob = FM.make_frame("discovery_response", _paper(footer=footer), IDENTITY, BLOCKS).data
    run = _real_run(tmp_path, monkeypatch, blob)
    path, _r, _l, _c = base_lay.lay_out(
        DRAFT, tmp_path / "out.docx", "discovery_response", run.firm.data["format"], "plain", None, None,
        styles=library_pick.pick_base(run).styles,
    )  # fmt: skip
    printed = "\n".join(p.text for s in docx.Document(str(path)).sections for p in s.footer.paragraphs)
    assert CLIENT not in printed and "[[" not in printed


def test_party_names_from_the_caption_and_their_short_forms_become_marked_places():
    prelim = (
        "Plaintiff hereby responds to the Special Interrogatories, Set ONE. EXAMPLAR reserves the right to "
        "supplement. Defendant WIDGETCO's definitions are objected to."
    )
    made = FM.make_frame("discovery_response", _paper(prelim, responding="Plaintiff"), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 0 and CLIENT not in _all_text(made.data)
    assert "EXAMPLAR" not in _all_text(made.data) and "WIDGETCO" not in _all_text(made.data)
    front = [t for t, _h in F.read(made.data).blocks[F.FRONT]]
    assert "[[v.responding_name]] reserves the right" in front[1]
    assert "Defendant [[v.propounding_name]]'s definitions" in front[1]
    _clean(made)


def test_a_matter_definition_with_a_date_is_a_hole_the_drafts_own_definition_fills():
    tailored = '"INCIDENT" means the collision of March 3, 2025 on Example Road.'
    standard = '"DOCUMENT" means a writing as defined in Evidence Code section 250.'
    made = FM.make_frame("discovery_response", _paper(extra_front=(standard, tailored)), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 1 and "March 3" not in _all_text(made.data)
    frame = F.read(made.data)
    assert frame.blocks[F.FRONT][-1] == (F.HOLE, False)
    draft = DRAFT.replace(
        "The model's own opening words.",
        'The model\'s own opening words.\n\n"INCIDENT" means the fall of June 1, 2025.\n\n"DOCUMENT" means paper.',
    )
    out, _notes = F.apply(draft, frame, F.draft_values(draft, "November 24, 2026", "Attorney 1, Esq."))
    assert '"INCIDENT" means the fall of June 1, 2025.' in out and standard in out
    assert '"DOCUMENT" means paper.' not in out and "The model's own opening words." not in out
    _clean(made)


def test_the_firms_identity_run_together_in_a_signature_still_matches():
    signature = ("Attorney 1, Esq.(SBN 000001)", "Example & Example,LLPOne Example Way", "Exampletown,California 90000")
    made = FM.make_frame("discovery_response", _paper(signature=signature), IDENTITY, BLOCKS)
    assert made.counts["closing_refused"] == 0
    _clean(made)


def test_the_court_the_county_and_code_sections_are_fixed_words():
    extra = (
        "Responding Party objects under C.C.P. § 2017.010 and Code of Civil Procedure section 2030.220(c), "
        "as the Superior Court of the State of California for the County of Exampleton has held.",
        "Venue lies in Exampleton County.",
    )
    made = FM.make_frame("discovery_response", _paper(extra_front=extra), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 0 and made.counts["kept_front"] == 6
    _clean(made)


def test_a_place_a_vehicle_or_a_provider_still_drops_its_paragraph():
    extra = ("Responding Party was treated at Brightwell Clinic after the Hondata sedan struck her.",)
    made = FM.make_frame("discovery_response", _paper(extra_front=extra), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 1 and "Brightwell" not in _all_text(made.data)


def test_a_paper_the_operator_wrote_is_never_stocked():
    none, answer = ST.made_for("discovery_response", _paper(author="SMD Operator"), IDENTITY, BLOCKS)
    assert none is None and answer["code"] == "operator_authored"


def test_a_paper_with_no_identification_block_is_refused():
    d = docx.Document()
    d.add_paragraph("SPECIAL INTERROGATORY NO. 1:")
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(Unusable) as exc:
        FM.make_frame("discovery_response", buf.getvalue(), IDENTITY, BLOCKS)
    assert exc.value.code == "no_identification_block"


def test_the_command_writes_the_file_and_what_the_root_writer_reads(tmp_path):
    made, answer = ST.made_for("discovery_response", _paper(), IDENTITY, BLOCKS)
    assert answer["check"] in ("passed", "root_writer")
    ST.write(tmp_path, made, "discovery_response", "file-0001", "filed", None)
    side = json.loads((tmp_path / ST.SIDECAR).read_text())
    assert side["kind"] == "discovery_response" and side["contract"] == "styles"
    assert side["fixed_strings"] == list(made.fixed_strings)
    assert (tmp_path / ST.MADE).read_bytes() == made.data


DRAFT = """**PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET TWO**

| PROPOUNDING PARTY: | Defendant OTHERCO SAMPLE |
| RESPONDING PARTY: | Plaintiff JORDAN SAMPLE |
| SET NO.: | TWO |

**PRELIMINARY STATEMENT**

The model's own opening words.

**SPECIAL INTERROGATORY NO. 1:**

State every fact.

**RESPONSE TO SPECIAL INTERROGATORY NO. 1:**

The draft's own answer.

DATED: November 24, 2026

The model's own signature block.

**VERIFICATION**

The model's own verification.
"""


def _frame():
    return F.read(FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS).data)


def test_a_draft_takes_the_firms_opening_and_closing_and_keeps_its_own_answers():
    values = F.draft_values(DRAFT, "November 24, 2026", "Attorney 1, Esq.")
    out, notes = F.apply(DRAFT, _frame(), values, "special_interrogatories")
    assert "The model's own" not in out
    assert "**PRELIMINARY STATEMENT**" in out and "**GENERAL OBJECTIONS**" in out and OBJECTION in out
    assert 'Plaintiff JORDAN SAMPLE ("Responding Party") hereby responds' in out
    assert "Set TWO, propounded by Defendant OTHERCO SAMPLE These responses" in out
    assert "I, JORDAN SAMPLE, declare" in out and "Executed on November 24, 2026" in out
    assert "DATED: November 24, 2026" in out and "Attorney 1, Esq." in out
    assert "State every fact." in out and "The draft's own answer." in out
    assert "| SET NO.: | TWO |" in out
    assert "[[" not in out and len(notes) == 2


def test_a_block_whose_place_the_draft_cannot_fill_stays_the_drafts():
    values = F.draft_values(DRAFT, "November 24, 2026", "")
    out, notes = F.apply(DRAFT, _frame(), values, "special_interrogatories")
    assert "The model's own signature block." in out and "hereby responds" in out
    assert any("no signer" in n for n in notes)


def test_a_frame_from_one_kind_of_response_serves_another_worded_for_it():
    values = F.draft_values(DRAFT, "November 24, 2026", "Attorney 1, Esq.")
    out, notes = F.apply(DRAFT, _frame(), values, "requests_for_production")
    assert "hereby responds to the Requests for Production, Set TWO" in out
    assert "objects to each request to the extent" in out and "section 2031.310." in out
    assert "Interrogator" not in out.split("**SPECIAL INTERROGATORY NO. 1:**")[0].split("SET NO.")[1]
    assert "special_interrogatories paper, worded for this requests_for_production set" in notes[0]


def test_a_shared_frame_is_recorded_in_the_library_resolution(tmp_path, monkeypatch):
    from jobs.drafting.finish import Unit

    run = _real_run(tmp_path, monkeypatch, FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS).data)
    for variant in ("requests_for_production", "requests_for_admission"):
        F.at_gate(run, Unit("", "", variant, None, run.data), DRAFT)
    rec = json.loads((run.data / "library-resolution.json").read_text())
    assert rec["frame_shared"] == {
        "frame_from": "special_interrogatories",
        "used_for": ["requests_for_admission", "requests_for_production"],
    }


def test_a_styles_file_with_no_frame_reads_as_none():
    from jobs.library_setup.styles_make import make_styles

    assert F.read(make_styles("discovery_response", _paper(), IDENTITY).data) is None
    assert F.read(None) is None


def _stocked(digest: str):
    from operator_connector_sdk.library import resolver as R
    from operator_connector_sdk.library.ledger import Entry

    return Entry(
        kind="discovery_response",
        attorney=None,
        file_id="file-0001",
        version="filed",
        sha256=digest,
        contract="styles",
        accepted_by=f"{R.SMD_STOCKED}:file-0001",
        accepted_at="2026-11-24T17:00:00Z",
    )


class _NoLibrary:
    def list_versions(self, _fid):
        raise AssertionError("a stocked file is in no library")


def test_a_stocked_entry_resolves_from_the_seats_copy_without_asking_the_library(tmp_path):
    import hashlib

    from operator_connector_sdk.library import copies, resolver as R

    data = FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS).data
    digest = hashlib.sha256(data).hexdigest()
    cdir = tmp_path / "copies"
    cdir.mkdir()
    (cdir / f"{digest}{copies.SUFFIX}").write_bytes(data)
    check = R._check
    res = check(_NoLibrary(), "discovery_response", None, _stocked(digest), R.FIRM_FILE, cdir)
    assert res.status == R.RESOLVED and res.data == data and res.newer is None
    (cdir / f"{digest}{copies.SUFFIX}").write_bytes(data + b"x")
    assert check(_NoLibrary(), "discovery_response", None, _stocked(digest), R.FIRM_FILE, cdir).status == (
        R.INTEGRITY_FAILURE
    )
    assert check(_NoLibrary(), "discovery_response", None, _stocked(digest), R.FIRM_FILE, None).status == (
        R.COPY_MISSING
    )


def test_the_root_writers_prefix_is_the_resolvers():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hermes-smd-overlay"))
    from establish_intake import library_stock
    from operator_connector_sdk.library.resolver import SMD_STOCKED

    assert library_stock.SMD_STOCKED == SMD_STOCKED


def _real_run(tmp_path, monkeypatch, blob: bytes):
    """The real ``DraftingRun`` with ``blob`` pinned as its styles file."""
    from test_drafting_styles_base import CHECKER, _run

    from jobs.drafting import firm_rules

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    (tmp_path / "specs").mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))
    return _run(tmp_path / "r", "discovery_response", blob)


def test_the_drafting_job_puts_the_firms_words_in_at_its_gate_and_prints_no_marked_place(tmp_path, monkeypatch):
    from jobs.drafting import base_lay, library_pick
    from jobs.drafting.finish import Unit

    blob = FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS).data
    run = _real_run(tmp_path, monkeypatch, blob)
    out = F.at_gate(run, Unit("", "", "special_interrogatories", None, run.data), DRAFT)
    assert "hereby responds" in out and "The model's own opening words." not in out
    made_in = library_pick.pick_base(run)
    path, _r, _l, _c = base_lay.lay_out(
        out, tmp_path / "out.docx", "discovery_response", run.firm.data["format"], "plain", None, None,
        styles=made_in.styles,
    )  # fmt: skip
    printed = "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    assert "[[" not in printed and "hereby responds" in printed and "State every fact." in printed


def test_a_draft_made_on_a_file_with_no_frame_is_unchanged(tmp_path, monkeypatch):
    from jobs.drafting.finish import Unit
    from jobs.library_setup.styles_make import make_styles

    run = _real_run(tmp_path, monkeypatch, make_styles("discovery_response", _paper(), IDENTITY).data)
    assert F.at_gate(run, Unit("", "", "special_interrogatories", None, run.data), DRAFT) == DRAFT


# ---- the second rerun on real papers, 2026-11-25: each cause, invented ----


def _closing(made) -> list[str]:
    return [t for t, _h in F.read(made.data).blocks[F.CLOSING]]


def test_a_closing_of_one_paragraph_joined_by_line_breaks_is_read_line_by_line():
    made = FM.make_frame("discovery_response", _paper(one_paragraph_closing=True), IDENTITY, BLOCKS)
    assert made.counts["closing_refused"] == 0
    closing = _closing(made)
    assert closing[:5] == [
        "DATED: [[v.date]]",
        FIRM,
        "By: ______________",
        "[[v.signer]]",
        "Attorneys for [[v.responding_party]]",
    ]
    _clean(made)


def test_the_signer_printed_otherwise_than_authored_is_a_marked_place_never_a_drop():
    made = FM.make_frame("discovery_response", _paper(signature=("ALEX J. QUENTWORTH",)), IDENTITY, BLOCKS)
    assert "[[v.signer]]" in _closing(made) and "QUENTWORTH" not in _all_text(made.data)
    assert made.counts["closing_refused"] == 0
    _clean(made)

    def never(_line: str) -> bool:  # no line is the firm's identity
        return False

    assert L.signer_lines(["By: ______ Alex J. Quentworth"], never) == ["By: ______ [[v.signer]]"]
    assert L.signer_lines(["By: /s/ Alex J. Quentworth"], never) == ["By: /s/ [[v.signer]]"]
    assert L.signer_lines(["By:", "______", "Alex J. Quentworth"], never) == ["By:", "______", "[[v.signer]]"]
    assert L.signer_lines(["By: ____", FIRM], lambda t: t == FIRM) == ["By: ____", FIRM]


def test_a_declaration_for_additional_discovery_is_never_part_of_the_frame():
    tail = (
        "DECLARATION FOR ADDITIONAL DISCOVERY",
        "I am propounding 42 interrogatories in this motor vehicle negligence case against WIDGETCO.",
    )
    made = FM.make_frame("discovery_response", _paper(tail=tail), IDENTITY, BLOCKS)
    text = _all_text(made.data)
    assert "ADDITIONAL DISCOVERY" not in text and "42 interrogatories" not in text and "negligence" not in text
    assert made.counts["closing_refused"] == 0


def test_the_made_file_carries_none_of_the_papers_dates_in_its_properties():
    made = FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS)
    with zipfile.ZipFile(io.BytesIO(made.data)) as z:
        props = z.read("docProps/core.xml").decode() + z.read("docProps/app.xml").decode()
    assert "2021-" not in props and "2022-" not in props and "dcterms:created" not in props
    assert "TotalTime" not in props and "<cp:revision" not in props
    _clean(made)


def test_a_dated_blank_with_its_year_is_one_marked_place():
    paper = _paper(dated="DATED: ____________, 2026", executed="______________, 2026")
    made = FM.make_frame("discovery_response", paper, IDENTITY, BLOCKS)
    closing = _closing(made)
    assert closing[0] == "DATED: [[v.date]]" and made.counts["closing_refused"] == 0
    assert any("Executed on [[v.date]], at" in t for t in closing)
    assert "2026" not in "".join(closing)
    _clean(made)


def test_a_hole_never_cuts_a_kept_sentence():
    run_on = "Responding Party responds to these interrogatories subject to the following,"
    tail = "and notes the Hondata sedan was inspected at Brightwell Clinic."
    made = FM.make_frame("discovery_response", _paper(extra_front=(run_on, tail)), IDENTITY, BLOCKS)
    front = [t for t, _h in F.read(made.data).blocks[F.FRONT]]
    assert run_on not in front and front[-1] == F.HOLE and made.counts["front_refused"] == 2
    assert front[1].endswith(PRELIM_END) and front[3] == MARKED_OBJECTION


def test_a_preliminary_statement_with_line_breaks_inside_is_kept_whole():
    first, second = PRELIM.split(" These responses ", 1)
    paper = _paper(prelim="")
    d = docx.Document(io.BytesIO(paper))
    target = next(p for p in d.paragraphs if p.text == "")
    run = target.add_run(first)
    run.add_break()
    target.add_run("These responses " + second)
    buf = io.BytesIO()
    d.save(buf)
    made = FM.make_frame("discovery_response", buf.getvalue(), IDENTITY, BLOCKS)
    front = [t for t, _h in F.read(made.data).blocks[F.FRONT]]
    assert front[1].endswith(PRELIM_END) and "These responses are made solely" in front[1]


# ---- the third rerun on real papers, 2026-11-25: each cause, invented ----


def _paper_with(extra_front: tuple[str, ...] = (), closing: tuple[str, ...] | None = None, **kw) -> bytes:
    """``_paper`` with its signature block replaced by one paragraph of ``closing``
    lines joined by line breaks (each may hold tabs)."""
    blob = _paper(extra_front=extra_front, **kw)
    if closing is None:
        return blob
    d = docx.Document(io.BytesIO(blob))
    paras = d.paragraphs
    start = next(i for i, p in enumerate(paras) if p.text.startswith("DATED"))
    end = next(i for i, p in enumerate(paras) if p.text == "VERIFICATION")
    for p in paras[start + 1 : end]:
        p._element.getparent().remove(p._element)
    first = paras[start]
    for r in list(first.runs):
        r._element.getparent().remove(r._element)
    for i, line in enumerate(closing):
        run = first.add_run(line)
        if i < len(closing) - 1:
            run.add_break()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_a_pleading_closing_of_breaks_and_tabs_keeps_the_firm_the_signer_and_attorneys_for():
    closing = (
        f"DATED: November 3, 2026\t\t\t{FIRM.upper()}",
        "\t\t\tBy:\t______________",
        "\t\t\tALEX J. QUENTWORTH",
        f"\t\t\tAttorneys for Plaintiff, {CLIENT}",
    )
    made = FM.make_frame("discovery_response", _paper_with(closing=closing), IDENTITY, BLOCKS)
    assert _closing(made)[:6] == [
        "DATED: [[v.date]]",
        FIRM.upper(),
        "By:",
        "______________",
        "[[v.signer]]",
        "Attorneys for Plaintiff, [[v.responding_name]]",
    ]
    assert made.counts["closing_refused"] == 0 and "QUENTWORTH" not in _all_text(made.data)
    _clean(made)


def test_a_party_short_form_never_marks_an_ordinary_word_and_a_run_of_marks_is_a_hole():
    party = "WIDGETCO EXAMPLE COMPANY"
    plain_word = '25. "YOU" means the company that answers and its agents.'
    garbled = f"26. Refer to WIDGETCO, {party} and WIDGETCO in each answer."
    paper = _paper(PRELIM.replace(OTHER_SIDE, party), extra_front=(plain_word, garbled), other=party)
    made = FM.make_frame("discovery_response", paper, IDENTITY, BLOCKS)
    assert "COMPANY" not in _all_text(made.data)
    front = [t for t, _h in F.read(made.data).blocks[F.FRONT]]
    assert plain_word in front and front[-1] == F.HOLE
    assert made.counts["front_drop_fragment"] == 1 and "WIDGETCO" not in _all_text(made.data)


def test_a_numbered_list_with_a_dropped_item_is_numbered_afresh_with_the_drafts_definition_in_it():
    defs = (
        '1. "DOCUMENT" means a writing as defined in Evidence Code section 250.',
        '2. "INCIDENT" means the collision of March 3, 2025.',
        '3. "PERSON" means a natural person or an entity.',
    )
    made = FM.make_frame("discovery_response", _paper(extra_front=defs), IDENTITY, BLOCKS)
    frame = F.read(made.data)
    draft = DRAFT.replace("The model's own opening words.", '7. "INCIDENT" means the fall of June 1, 2025.')
    out, _n = F.apply(draft, frame, F.draft_values(draft, "November 24, 2026", "Attorney 1, Esq."))
    assert '1. "DOCUMENT" means' in out and '2. "INCIDENT" means the fall' in out and '3. "PERSON" means' in out
    assert F.renumber(["4. a", "6. b", "x", "9. c"]) == ["4. a", "5. b", "x", "9. c"]


def test_a_served_date_line_is_the_identification_blocks_and_an_attorney_line_is_the_signer():
    d = docx.Document(io.BytesIO(_paper(extra_front=("Alex J. Quentworth, Esq.",))))
    set_no = next(p for p in d.paragraphs if p.text.startswith("SET NO."))
    set_no.insert_paragraph_before("SET NO.:\tONE")
    set_no.text = "DATE SERVED:\tOctober 1, 2026"
    buf = io.BytesIO()
    d.save(buf)
    made = FM.make_frame("discovery_response", buf.getvalue(), IDENTITY, BLOCKS)
    front = [t for t, _h in F.read(made.data).blocks[F.FRONT]]
    assert made.counts["front_refused"] == 0 and front[-1] == "[[v.signer]]"
    assert "October 1" not in _all_text(made.data)


def test_the_template_name_and_the_counts_are_gone_from_the_application_properties():
    made = FM.make_frame("discovery_response", _paper(), IDENTITY, BLOCKS)
    with zipfile.ZipFile(io.BytesIO(made.data)) as z:
        app = z.read("docProps/app.xml").decode()
    for tag in ("Template", "Pages", "Words", "Characters", "Lines", "Paragraphs"):
        assert f"<{tag}>" not in app, tag
