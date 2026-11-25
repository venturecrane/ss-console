"""A firm's discovery paper made into its library file with its standard wording
kept and its case facts out (``library_setup/frame_make.py``), and a draft that
uses that wording (``drafting/firm_frame.py``). Every name, number and date is
made up."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import docx
import pytest

from jobs.drafting import firm_frame as F
from jobs.library_setup import frame_make as FM, stock as ST
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
CASE_WORDS = (CLIENT, "WIDGETCO", "March 3", "Sam Server", "EX-0000-123", "Exampleville", "November 3")


def _paper(prelim: str = PRELIM, author: str = "Alex Example") -> bytes:
    d = docx.Document()
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    d.sections[0].header.paragraphs[0].text = FIRM
    lines = [
        "Attorney 1, Esq.",
        FIRM,
        f"Attorneys for Plaintiff {CLIENT}",
        "SUPERIOR COURT OF THE STATE OF CALIFORNIA",
        f"{CLIENT}, Plaintiff, v. {OTHER_SIDE}, Defendant.",
        "Case No. EX-0000-123",
        "PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE",
        f"PROPOUNDING PARTY:\tDefendant {OTHER_SIDE}",
        f"RESPONDING PARTY:\tPlaintiff {CLIENT}",
        "SET NO.:\tONE",
        "PRELIMINARY STATEMENT",
        prelim,
        "GENERAL OBJECTIONS",
        OBJECTION,
        "SPECIAL INTERROGATORY NO. 1:",
        "State the date of the incident.",
        "RESPONSE TO SPECIAL INTERROGATORY NO. 1:",
        "The incident occurred on March 3, 2025 at Exampleville.",
        "DATED: November 3, 2026",
        FIRM,
        "By: ______________",
        "Attorney 1, Esq.",
        f"Attorneys for Plaintiff {CLIENT}",
        "VERIFICATION",
        VERIFY,
        "______________",
        CLIENT,
        "PROOF OF SERVICE",
        "I, Sam Server, served the foregoing.",
    ]
    for line in lines:
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
    assert "Set [[v.set_number]], propounded by [[v.propounding_party]]" in front[1]
    assert front[1].endswith("section 2030.310.") and front[3] == OBJECTION
    closing = [t for t, _h in frame.blocks[F.CLOSING]]
    assert closing[0] == "DATED: [[v.date]]" and "[[v.signer]]" in closing
    assert any(t.startswith("I, [[v.responding_name]], declare") and "Executed on [[v.date]]" in t for t in closing)
    assert frame.marks(F.FRONT) == {"responding_party", "propounding_party", "set_number"}


def test_a_paragraph_that_names_someone_else_drops_its_block_and_the_command_refuses():
    named = PRELIM + " Ms. Quillon reviewed these responses."
    made = FM.make_frame("discovery_response", _paper(named), IDENTITY, BLOCKS)
    assert made.counts["front_refused"] == 1 and made.counts["kept_front"] == 0
    assert "Quillon" not in _all_text(made.data)
    none, answer = ST.made_for("discovery_response", _paper(named), IDENTITY, BLOCKS)
    assert none is None and answer["code"] == "standard_wording_not_kept"
    partial, answer = ST.made_for("discovery_response", _paper(named), IDENTITY, BLOCKS, allow_partial=True)
    assert partial is not None and answer["status"] == "made"


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


def test_the_wording_of_one_kind_of_set_is_not_put_in_another():
    values = F.draft_values(DRAFT, "November 24, 2026", "Attorney 1, Esq.")
    out, notes = F.apply(DRAFT, _frame(), values, "requests_for_production")
    assert out == DRAFT and "requests_for_production" in notes[0]


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
