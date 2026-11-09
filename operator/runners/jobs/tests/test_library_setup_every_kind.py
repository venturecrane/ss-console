"""'Set up the document library' (2026-11-09): every kind the drafting job makes,
in ONE request, each made the way it can be (the marked plan where one exists and
fits, else a styles base), the file each kind uses now, and a document the
administrator points at by matter or by name. Every name, number and document is
made up; no document system is reached, and the client fake refuses every write."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import docx
import pytest
from docx.oxml import parse_xml

import library_setup_testkit as kit
from test_drafting_memo_base import _firm_memo

from jobs.firmbase import hunt as H
from jobs.library_setup import KINDS, LIBRARY, STYLES, accept as A, job as J, plan as P

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
M1, M2 = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
NUMBERS = {M1: "2024-0113", M2: "2025-0021"}
FIRM = [kit.FIRM_IDENTITY["name"], kit.FIRM_IDENTITY["street"], kit.FIRM_IDENTITY["city_state_zip"]]
STAFF = [{"firstName": "Alex", "lastName": "Example"}]
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _ago(years: float) -> str:
    return (NOW - timedelta(days=365.25 * years)).isoformat().replace("+00:00", "Z")


def _row(fid: str, name: str, years: float) -> dict:
    return {"id": fid, "name": name, "fileExtension": ".docx", "dateModified": _ago(years), "sizeBytes": 1000}


def _paper(title: str, *, author: str = "Alex Example", header: str = "", words: int = 900) -> bytes:
    """A firm's filed paper, made up: the firm's name in the header (or ``header``),
    a page number and the running title in the footer, words of a case in the body."""
    d = docx.Document()
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    d.sections[0].header.paragraphs[0].text = header or kit.FIRM_IDENTITY["name"].upper()
    fp = d.sections[0].footer.paragraphs[0]
    fp.add_run()._r.append(
        parse_xml(f'<w:fldSimple xmlns:w="{_W}" w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>')
    )
    d.sections[0].footer.add_paragraph(title)
    d.add_paragraph("ZEBULON QUILLFEATHER v. ACME HOLDINGS, Case No. 99-1234")
    d.add_paragraph(" ".join(["word"] * words))
    d.add_paragraph(author)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class Firm:
    """A case system with two matters (numbers searchable); every call but a read fails."""

    def __init__(self, files: dict[str, tuple[str, dict, bytes]]):
        self.files = files
        self.calls: list[str] = []

    def get(self, path: str, **params):
        self.calls.append(path)
        if path == "/matters":
            if params.get("Search"):
                return {"value": [{"id": m, "number": n} for m, n in NUMBERS.items() if n == params["Search"]]}
            if params.get("Status") in ("Open", None):
                return {"value": [{"id": m, "number": n} for m, n in NUMBERS.items()]}
            return {"value": []}
        if path == "/staff":
            return {"value": STAFF}
        if path.endswith("/documents/files"):
            mid = path.split("/")[2]
            return {"value": [row for m, row, _ in self.files.values() if m == mid]}
        if path.startswith("/matters/"):
            return {"number": NUMBERS[path.split("/")[2]]}
        raise AssertionError(path)

    def download_file(self, matter_id: str, file_id: str):
        self.calls.append(f"download {file_id}")
        return {}, self.files[file_id][2]

    def __getattr__(self, name):
        raise AssertionError(f"the setup job reads only; it called {name}")


def _firm() -> Firm:
    memo = docx.Document(io.BytesIO(_firm_memo()))
    memo.core_properties.author = "Alex Example"
    memo.add_paragraph(" ".join(["word"] * 600))
    buf = io.BytesIO()
    memo.save(buf)
    return Firm(
        {
            "r-1": (M1, _row("r-1", "Pltf Responses to SROG Set One.docx", 0.4), _paper("Plaintiff's Responses to Special Interrogatories, Set One")),
            "r-2": (M2, _row("r-2", "Responses to RFP Set Two.docx", 1.2), _paper("Responses to Requests for Production, Set Two")),
            "s-1": (M2, _row("s-1", "SROG Set One to Def.docx", 0.3), _paper("Plaintiff's Special Interrogatories, Set One")),
            "d-1": (M1, _row("d-1", "Deposition Outline - treating doctor.docx", 0.8), _paper("Deposition Outline")),
            "m-1": (M1, _row("m-1", "Case Evaluation Memo.docx", 0.5), buf.getvalue()),
            "x-1": (M2, _row("x-1", "Responses to Form Interrogatories.docx", 0.1),
                    _paper("Responses", header="Rivera v. Northwind Freight, Case No. 24-CV-0001")),
        }
    )  # fmt: skip


def _run(job: dict, client: Firm | None = None, **kw) -> tuple[dict, Firm]:
    client = client or _firm()
    base = {"kind": "library_setup", "attorney_named": None, "live": True}
    out = J.run({**base, **job}, client=client, switch=kit.switch(), now=NOW, pieces=FIRM, **kw)
    return out, client


@pytest.fixture(autouse=True)
def _seat(tmp_path, monkeypatch):
    kit.customer_yaml(tmp_path / "customer.yaml", monkeypatch)
    monkeypatch.setenv("SMD_SPEC_DIR", str(tmp_path / "spec"))


# ---- the hunt knows every kind the drafting job makes -------------------------------------


@pytest.mark.parametrize(
    "name,kind",
    [
        ("Plaintiff SROG Set One.docx", "discovery_set"),
        ("Special Interrogatories to Defendant, Set One.docx", "discovery_set"),
        ("FROGs to Def.docx", "discovery_set"),
        ("Requests for Admission Set Two.docx", "discovery_set"),
        ("Demand for Production of Documents.docx", "discovery_set"),
        ("Pltf Responses to SROG Set One.docx", "discovery_response"),
        ("Def Resp to RFA.docx", "discovery_response"),
        ("Answers to Interrogatories.docx", "discovery_response"),
        ("Deposition Outline - Dr Example.docx", "depo_outline"),
        ("Outline for Depo of Example.docx", "depo_outline"),
        ("Case Evaluation Memo.docx", "memo"),
        ("Mediation Brief.docx", "mediation_brief"),
    ],
)
def test_the_hunt_names_each_kind_by_the_names_a_california_firm_files_it_under(name, kind):
    assert [k for k in KINDS if H.classify(k, name)] == [kind]


@pytest.mark.parametrize(
    "name",
    ["Meet and Confer Letter re SROGs.docx", "Notice of Deposition.docx", "Depo Transcript.docx",
     "Depo Summary.docx", "Motion to Compel Responses to RFP.docx", "Verification.docx",
     "Response to Demand Letter.docx"],
)  # fmt: skip
def test_papers_that_are_not_any_kind_are_not_named_as_one(name):
    assert [k for k in KINDS if H.classify(k, name)] == []


# ---- every kind can be made ----------------------------------------------------------------


def test_a_discovery_response_is_listed_as_a_styles_base_and_one_whose_header_names_a_case_is_not():
    out, _ = _run({"setup_kind": "discovery_response"})
    assert out["outcome"] == "delivered" and out["setup_outcome"] == J.CANDIDATES
    assert [(c["file_id"], c["contract"]) for c in out["candidates"]] == [("r-1", STYLES), ("r-2", STYLES)]
    assert out["found"] == 3 and out["dropped_files"]["styles_refused_case_words_remain"] == ["x-1"]
    assert out["current"] is None  # nothing accepted, nothing pinned: the standard format
    assert "Responses" not in json.dumps(out) and "QUILLFEATHER" not in json.dumps(out)


def test_contract_for_tries_the_marked_plan_first_and_a_styles_base_for_every_kind():
    from jobs.firmbase import Source

    paper = _paper("Deposition Outline")
    src = Source("m", "f", "", hashlib.sha256(paper).hexdigest())
    assert P.contract_for("depo_outline", paper, src, FIRM) == (STYLES, [])
    contract, refused = P.contract_for("memo", paper, src, FIRM)  # no heading for the memo plan
    assert contract == STYLES and refused and refused[0].startswith("marked_refused_plan_")
    memo = _firm().files["m-1"][2]
    assert P.contract_for("memo", memo, Source("m", "f", "", hashlib.sha256(memo).hexdigest()), FIRM)[0] in (
        "marked",
        "",
    )


# ---- "set up the document library": one request, one list per kind ------------------------


def test_the_document_library_is_one_job_one_listing_and_one_list_per_kind():
    out, client = _run({"setup_kind": LIBRARY})
    assert out["outcome"] == "delivered" and out["setup_outcome"] == J.LIBRARY_LISTS
    by = {e["kind"]: e for e in out["kinds"]}
    assert list(by) == list(KINDS)
    assert [c["file_id"] for c in by["discovery_response"]["candidates"]] == ["r-1", "r-2"]
    assert [c["file_id"] for c in by["discovery_set"]["candidates"]] == ["s-1"]
    assert [c["file_id"] for c in by["depo_outline"]["candidates"]] == ["d-1"]
    assert by["mediation_brief"]["setup_outcome"] == J.NONE_FOUND and by["mediation_brief"]["found"] == 0
    assert all(e["current"] is None for e in out["kinds"])
    # the firm's matters are listed once for all five kinds
    assert sum(1 for c in client.calls if c.endswith("/documents/files")) == len(NUMBERS)


def test_the_file_each_kind_uses_now_comes_from_the_seats_ledger(monkeypatch):
    accepted = {"file_id": "lib-9", "version": "v3", "sha256": "c" * 64}
    monkeypatch.setattr(A, "replaces", lambda kind, attorney, spec: accepted if kind == "memo" else None)
    out, _ = _run({"setup_kind": LIBRARY})
    assert {e["kind"]: e["current"] for e in out["kinds"]}["memo"] == accepted


def test_a_kind_the_firm_pinned_a_file_for_says_so(monkeypatch):
    class Pinned:
        def firm_base(self, cls):
            return ("p", {}) if cls == "mediation_brief" else None

        def styles_base(self, cls):
            return None

    monkeypatch.setattr(J, "_inputs", lambda _d: Pinned())
    out, _ = _run({"setup_kind": LIBRARY})
    assert {e["kind"]: e["current"] for e in out["kinds"]}["mediation_brief"] == {"pinned": True}


def test_a_ledger_the_seat_cannot_read_holds_rather_than_says_nothing_is_in_use(monkeypatch):
    monkeypatch.setattr(A, "replaces", lambda *_a: "")
    out, client = _run({"setup_kind": LIBRARY})
    assert out["outcome"] == "held" and out["hold"] == J.LEDGER_UNREADABLE
    assert not [c for c in client.calls if c.startswith("download")]


def test_off_the_library_job_records_the_same_facts_and_reads_only():
    client = _firm()
    out = J.run(
        {"setup_kind": LIBRARY, "live": True}, client=client, switch=kit.switch("observe"), now=NOW, pieces=FIRM
    )
    assert out["outcome"] == "observed" and len(out["kinds"]) == len(KINDS)
    assert all(c == "/matters" or c.startswith(("/matters/", "/staff", "download ")) for c in client.calls)


# ---- a document the administrator points at --------------------------------------------


def test_pointing_at_a_matter_lists_that_matters_documents_of_the_kind():
    out, _ = _run({"setup_kind": "discovery_response", "point_matter": "2025-0021"})
    assert out["pointed"] is True and [c["file_id"] for c in out["candidates"]] == ["r-2"]


def test_pointing_at_a_matter_with_no_file_named_as_the_kind_reads_every_word_file_on_it():
    out, _ = _run({"setup_kind": "memo", "point_matter": "2025-0021"})
    # nothing on 2025-0021 is named as a memo: its Word files are read as memos would be
    assert out["counts"]["name_matched"] == 3


def test_pointing_at_a_matter_the_firm_does_not_have_is_told():
    out, _ = _run({"setup_kind": "memo", "point_matter": "1999-0001"})
    assert out["outcome"] == "delivered" and out["setup_outcome"] == J.MATTER_NOT_FOUND


def test_pointing_at_a_name_lists_the_files_whose_name_holds_those_words():
    out, _ = _run({"setup_kind": "discovery_response", "point_name": "responses to rfp set two"})
    assert [c["file_id"] for c in out["candidates"]] == ["r-2"]


# ---- the chosen document made with the contract the list named ----------------------------


def test_a_chosen_styles_document_is_made_as_a_styles_base_filed_and_passes_the_root_writers_check(tmp_path):
    blob = _paper("Plaintiff's Responses to Special Interrogatories, Set One")
    tenant = kit.LibraryTenant({"r-1": ("00000000-0000-0000-0000-0000000c11e7", "A response", blob)})
    choice = {"n": 1, "file_id": "r-1", "matter_id": "00000000-0000-0000-0000-0000000c11e7",
              "matter_number": "2024-0113", "saved_on": "2026-05-08", "sha256": hashlib.sha256(blob).hexdigest(),
              "contract": STYLES}  # fmt: skip
    job = {"mode": "accept", "setup_kind": "discovery_response", "live": True, "choice": choice}
    v = A.run(job, tmp_path, client=tenant, switch=kit.switch(), pieces=FIRM, spec_dir=tmp_path / "spec",
              pause=lambda _s: None)  # fmt: skip
    assert v["outcome"] == "filed" and v["contract"] == STYLES, v
    assert tenant.writes == [("add_file", "Template - Discovery Response.docx")]
    made = (tmp_path / "data" / A.MADE_NAME).read_bytes()
    assert b"QUILLFEATHER" not in made and not [p for p in docx.Document(io.BytesIO(made)).paragraphs if p.text.strip()]
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hermes-smd-overlay"))
    from establish_intake import blank_form

    assert blank_form.check(made, tuple(v["fixed_strings"])).ok


def test_a_choice_from_a_list_made_before_contracts_were_named_is_marked_as_it_was():
    assert A.contract_of({}) == "marked" and A.contract_of({"contract": "styles"}) == STYLES
