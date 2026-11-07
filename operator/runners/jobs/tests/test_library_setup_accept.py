"""The administrator's choice made into the firm's library file: the round trip,
the allow list, the custom XML, the one write, the switch, the hand-off to the
root writer, and the next draft made in the new file. Every document is made up:
a brief laid by this repo's renderer and saved under a made-up person's name (the
brief kit's ``accepted``), and a firm memo laid by hand (the memo kit's)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import docx
import pytest

import library_setup_testkit as kit
import library_testkit as lt
from operator_connector_sdk.library import MARKED, MemoryLibrary
from jobs import firmbase, library_setup_handoff as H
from jobs.drafting import brief_base, library_pick
from jobs.library_setup import accept as A, file as F, make as M
from test_drafting_brief_base import CLS, MD, accepted  # noqa: F401 - the fixture, used by name
from test_drafting_library_pick import _env, _run  # noqa: F401 - the autouse env fixture, used by name
from test_drafting_memo_base import _firm_memo

FIRM = ["Example & Example, LLP", "One Example Way", "Exampletown, California 90000", "(000) 000-0000"]


@pytest.fixture(scope="module")
def firm_brief(accepted) -> bytes:
    """The kit's brief as a firm's own save: its style names are the firm's, not
    ours, so nothing marks it as the Operator's (``adopt.operator_authored``)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(accepted)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for i in src.infolist():
            data = src.read(i.filename)
            if i.filename == "word/styles.xml":
                data = re.sub(rb'(w:name w:val=")(SMD |Brief ?)', rb"\1Firm ", data)
            out.writestr(i, data)
    blob = buf.getvalue()
    assert not firmbase.operator_authored(blob)
    return blob


def _src(blob: bytes) -> firmbase.Source:
    return firmbase.Source("m-1", "f-1", "", hashlib.sha256(blob).hexdigest())


def _with_footer(blob: bytes, text: str) -> bytes:
    d = docx.Document(io.BytesIO(blob))
    d.sections[0].footer.paragraphs[0].text = text
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _members(blob: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(blob)).namelist()


# ---- the made file ------------------------------------------------------------------------


def test_the_round_trip_the_sources_own_words_in_the_made_file_differ_from_it_in_nothing(firm_brief):
    made = M.make(CLS, firm_brief, _src(firm_brief), FIRM)
    base = firmbase.FirmBase.load(CLS, _src(firm_brief), made.data)
    out, proof = brief_base.fill(base, MD)
    assert proof.lost == []
    source = M.drop_custom_xml(firm_brief)
    assert firmbase.layout_diff(source, out) == []
    a, b = docx.Document(io.BytesIO(source)), docx.Document(io.BytesIO(out))
    assert [p.text for p in a.paragraphs] == [p.text for p in b.paragraphs]
    assert [[c.text for r in t.rows for c in r.cells] for t in a.tables] == [
        [c.text for r in t.rows for c in r.cells] for t in b.tables
    ]


def test_a_briefs_kept_footer_is_judged_and_what_remained_is_listed_by_category(firm_brief):
    made = M.make(CLS, firm_brief, _src(firm_brief), FIRM)
    assert made.residue["page_numbering"] > 0 and made.residue["firm_identity"] == 1
    assert made.residue["refused_words"] == made.residue["refused_digits"] == 0
    assert made.fixed_strings == ("Plaintiff's Mediation Brief",)


@pytest.mark.parametrize(
    "footer,category",
    [
        ("Gamma Example v. Delta Example", "refused_words"),
        ("Case No. CV-0001", "refused_digits"),
        ("Example & Example, LLP  |  Gamma Example", "refused_words"),
        ("Filed 11/17/26", "refused_digits"),
    ],
)
def test_a_planted_party_name_number_or_date_refuses_the_file(firm_brief, footer, category):
    with pytest.raises(M.Unusable) as exc:
        M.make(CLS, _with_footer(firm_brief, footer), _src(firm_brief), FIRM)
    assert exc.value.code == "case_words_remain" and category in str(exc.value)
    assert "Gamma" not in str(exc.value) and "CV-0001" not in str(exc.value)  # counts, never the words


def test_the_firms_own_letterhead_line_and_page_numbering_may_remain(firm_brief):
    made = M.make(
        CLS, _with_footer(firm_brief, "Example & Example, LLP  |  Telephone (000) 000-0000"), _src(firm_brief), FIRM
    )
    assert made.residue["firm_identity"] >= 1 and made.residue["refused_words"] == 0


def test_a_note_or_comment_with_any_words_refuses(accepted):
    notes = (
        '<?xml version="1.0"?><w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:id="1"><w:p><w:r><w:t>see the deposition</w:t></w:r></w:p></w:footnote></w:footnotes>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(accepted)) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            out.writestr(i, src.read(i.filename))
        out.writestr("word/footnotes.xml", notes)
    with pytest.raises(M.Unusable) as exc:
        M.judge_stories(buf.getvalue(), FIRM)
    assert "notes_words 1" in str(exc.value)


def test_custom_xml_parts_and_their_pointers_go():
    memo = _firm_memo()
    assert any(n.startswith("customXml/") for n in _members(memo))
    clean = M.drop_custom_xml(memo)
    assert not any(n.startswith("customXml/") for n in _members(clean))
    z = zipfile.ZipFile(io.BytesIO(clean))
    assert all(b"customXml" not in z.read(n) for n in z.namelist() if n.endswith(".rels") or n == M.CONTENT_TYPES)
    docx.Document(io.BytesIO(clean))  # still a Word document


def test_identity_only_reads_whole_lines():
    pieces = M._allowed(FIRM)
    assert M.identity_only("Example & Example, LLP", pieces)
    assert M.identity_only("Telephone (000) 000-0000  |  Fax (000) 000-0000", pieces)
    assert not M.identity_only("Example & Example, LLP 2026", pieces)
    assert not M.identity_only("Jordan Placeholder", pieces)


# ---- the one write -------------------------------------------------------------------------
#
# Every test below goes through the REAL objects the job reads (library_setup_testkit):
# the real SmokeballLibrary and load_library_config over a made-up seat config, the
# real drafting loader over a made-up inputs directory, logic_in_code's own Switch,
# and the real DraftingLane for the hand-off. A look-alike stub once hid a property
# called as a method (``inputs.attorneys()``) until a firm's real inputs raised.

CLIENT_MATTER = "00000000-0000-0000-0000-0000000c11e7"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    kit.customer_yaml(tmp_path / "customer.yaml", monkeypatch)
    return tmp_path


def test_the_wrapper_lets_one_library_write_through_and_nothing_else(seat):
    w = F.WriteLimited(kit.LibraryTenant())
    with pytest.raises(F.WriteRefused):
        w.delete_file("m", "f")
    with pytest.raises(F.WriteRefused):
        w.request("POST", "/matters/m/documents/files")
    w.add_file(kit.LIB_MATTER, "n.docx", b"x", folder_id=kit.FOLDER)
    with pytest.raises(F.WriteRefused):
        w.add_file_version(kit.LIB_MATTER, "new-1", b"y", expected_version="v1")


def test_a_new_slot_files_a_new_file_and_reads_it_back(seat):
    tenant = kit.LibraryTenant()
    filed = F.file_library(F.WriteLimited(tenant), "memo", None, b"made", pause=lambda _s: None)
    assert tenant.writes == [("add_file", "Template - Memo.docx")]
    assert (filed.file_id, filed.version, filed.new_file) == ("new-1", "v1", True)


def test_a_slot_with_a_file_gets_a_new_version_of_it(seat):
    tenant = kit.LibraryTenant({"lib-7": (kit.LIB_MATTER, "Template - Memo", b"old")})
    filed = F.file_library(F.WriteLimited(tenant), "memo", None, b"made", pause=lambda _s: None)
    assert tenant.writes == [("add_file_version", "lib-7")] and (filed.version, filed.new_file) == ("v2", False)
    assert tenant.sha("lib-7") == hashlib.sha256(b"made").hexdigest()


def test_an_attorneys_file_is_filed_under_the_attorneys_name(seat):
    tenant = kit.LibraryTenant()
    F.file_library(F.WriteLimited(tenant), "memo", "Blair Sample", b"made", pause=lambda _s: None)
    assert tenant.writes == [("add_file", "Template - Memo - Blair Sample.docx")]


def test_a_filing_whose_bytes_do_not_read_back_is_unproven(seat, monkeypatch):
    tenant = kit.LibraryTenant({"lib-7": (kit.LIB_MATTER, "Template - Memo", b"old")})
    monkeypatch.setattr(tenant, "download_file", lambda m, f: ({}, b"someone else's save"))
    with pytest.raises(F.FilingUnproven):
        F.file_library(F.WriteLimited(tenant), "memo", None, b"made", pause=lambda _s: None)


# ---- the job, both switch states -------------------------------------------------------------


def _job(blob: bytes, **over) -> dict:
    choice = {"n": 1, "file_id": "f-1", "matter_id": CLIENT_MATTER, "matter_number": "1001", "saved_on": "2026-05-08"}
    return {
        "mode": "accept",
        "setup_kind": CLS,
        "live": True,
        "choice": {**choice, "sha256": hashlib.sha256(blob).hexdigest()},
        **over,
    }


def _tenant(blob: bytes) -> kit.LibraryTenant:
    return kit.LibraryTenant({"f-1": (CLIENT_MATTER, "A brief", blob)})


def test_live_the_chosen_document_is_made_filed_and_left_for_the_lane(firm_brief, seat):
    tenant = _tenant(firm_brief)
    v = A.run(_job(firm_brief), seat, client=tenant, switch=kit.switch(), pieces=FIRM, spec_dir=seat / "spec",
              pause=lambda _s: None)  # fmt: skip
    assert v["outcome"] == "filed", v
    assert tenant.writes == [("add_file", "Template - Mediation Brief.docx")]
    made = (seat / "data" / A.MADE_NAME).read_bytes()
    assert v["filed"]["sha256"] == hashlib.sha256(made).hexdigest() and v["replaces"] is None
    assert v["fixed_strings"] == ["Plaintiff's Mediation Brief"]


def test_the_allow_list_comes_from_the_real_loader_and_the_seats_firm_identity(seat):
    """Regression (the 2026-11-07 rehearsal): ``DraftingFirm.attorneys`` is a
    property, and ``accept.identity`` called it, so every accept raised
    TypeError on a firm's real inputs. Fails on that code."""
    inputs = kit.drafting_inputs(seat / "inputs", "alex@firm.example")
    pieces = A.identity(inputs)
    assert kit.FIRM_IDENTITY["name"] in pieces and kit.FIRM_IDENTITY["phone"] in pieces
    assert "Attorney 1, Esq." in pieces


def test_live_with_the_real_allow_list_end_to_end(firm_brief, seat):
    tenant = _tenant(firm_brief)
    inputs = kit.drafting_inputs(seat / "inputs", "alex@firm.example")
    v = A.run(_job(firm_brief), seat, client=tenant, switch=kit.switch(), inputs_dir=inputs, spec_dir=seat / "spec",
              pause=lambda _s: None)  # fmt: skip
    assert v["outcome"] == "filed", v


@pytest.mark.parametrize("mode,error,outcome", [("observe", None, "observed"), ("enforce", "unparseable", "held")])
def test_off_or_unreadable_nothing_is_read_made_or_written(firm_brief, seat, mode, error, outcome):
    tenant = _tenant(firm_brief)
    v = A.run(_job(firm_brief), seat, client=tenant, switch=kit.switch(mode, error), pieces=FIRM)
    assert v["outcome"] == outcome and tenant.writes == [] and not (seat / "data").exists()


def test_a_document_saved_since_the_list_is_told_and_nothing_is_written(firm_brief, seat):
    tenant = _tenant(firm_brief + b"x")
    v = A.run(_job(firm_brief), seat, client=tenant, switch=kit.switch(), pieces=FIRM)
    assert (v["outcome"], v["setup_outcome"]) == ("delivered", A.CHANGED) and tenant.writes == []


def test_a_document_that_keeps_words_of_its_case_is_told_and_nothing_is_written(firm_brief, seat):
    leaky = _with_footer(firm_brief, "Gamma Example v. Delta Example")
    tenant = _tenant(leaky)
    v = A.run(_job(leaky), seat, client=tenant, switch=kit.switch(), pieces=FIRM)
    assert (v["outcome"], v["setup_outcome"]) == ("delivered", A.UNUSABLE) and tenant.writes == []


def test_a_ledger_the_seat_cannot_read_holds_before_any_write(firm_brief, seat):
    spec = seat / "spec"
    (spec / "library").mkdir(parents=True)
    (spec / "library" / "accepted.json").write_text("{}")  # present, and no manifest names it
    tenant = _tenant(firm_brief)
    v = A.run(_job(firm_brief), seat, client=tenant, switch=kit.switch(), pieces=FIRM, spec_dir=spec)
    assert (v["outcome"], v["hold"]) == ("held", "ledger_unreadable") and tenant.writes == []


# ---- the hand-off to the root writer ------------------------------------------------------------


class Broker:
    """The broker's answers to the two root verbs, behind the real BrokerClient's
    ``_request`` (the DraftingBroker the lane is built with calls exactly that)."""

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []

    def _request(self, req):
        self.requests.append(req)
        if req["action"] == "library_setup_accept_submit":
            return {"ok": True, "run_id": "run-1"}
        return self.answers.pop(0) if self.answers else {"ok": True, "status": "pending"}


def _lane(tmp_path, broker):
    from jobs.drafting_lane import DraftingBroker, DraftingLane

    clock = iter(range(0, 100_000, 60))
    (tmp_path / "run" / "q").mkdir(parents=True)
    return DraftingLane(
        run_dir=tmp_path / "run",
        broker=DraftingBroker(broker),
        runner_cmd=["true"],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: next(clock),
        queue_dir=tmp_path / "run" / "q",
    )


def _handed(tmp_path, answers, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / A.MADE_NAME).write_bytes(b"made")
    verdict = {"filed": {"file_id": "new-1", "version": "v1", "sha256": hashlib.sha256(b"made").hexdigest()},
               "fixed_strings": [], "replaces": None}  # fmt: skip
    broker = Broker(answers)
    monkeypatch.setattr(H, "SLEEP", lambda _s: None)
    return H.hand_over(_lane(tmp_path, broker), "01J", tmp_path, verdict), broker


@pytest.mark.parametrize(
    "status,outcome", [("installed", "accepted"), ("accepted_pending_install", "accepted_pending")]
)
def test_an_accepted_file_is_told(tmp_path, status, outcome, monkeypatch):
    (state, fields), broker = _handed(
        tmp_path, [{"ok": True, "status": "complete", "result": {"status": status}}], monkeypatch
    )
    assert (state, fields["report"]["setup_outcome"]) == ("delivered", outcome)
    sent = broker.requests[0]
    assert base64.b64decode(sent["data_b64"]) == b"made" and sent["filed"]["file_id"] == "new-1"


def test_a_rejected_or_unanswered_acceptance_holds_for_smd(tmp_path, monkeypatch):
    (state, fields), _ = _handed(
        tmp_path, [{"ok": True, "status": "complete", "result": {"status": "rejected"}}], monkeypatch
    )
    assert (state, fields["report"]["hold"]) == ("held", "accept_rejected")


def test_an_answer_that_never_comes_holds(tmp_path, monkeypatch):
    (state, fields), _ = _handed(tmp_path, [], monkeypatch)
    assert (state, fields["report"]["hold"]) == ("held", "accept_unconfirmed")


# ---- the next draft is made in the new file ------------------------------------------------------


def test_the_first_draft_after_it_records_the_new_file_in_library_resolution_json(firm_brief, tmp_path, monkeypatch):
    from test_drafting_brief_base import _job_brief

    made = M.make(CLS, firm_brief, _src(firm_brief), FIRM).data
    lib = MemoryLibrary()
    spec = tmp_path / "specs"
    lt.install(spec, lt.entry(lib, "new-1", CLS, made, contract=MARKED))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    r, v, log = _run(tmp_path / "job", base=made, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    rec = lt.record(r.data)
    assert rec["base"]["source"] == "accepted_library_file" and rec["base"]["contract"] == "marked"
    assert rec["resolution"]["file"]["file_id"] == "new-1"
    assert rec["resolution"]["file"]["sha256"] == hashlib.sha256(made).hexdigest()


def test_the_made_file_holds_no_client_text_by_the_root_writers_own_check(firm_brief):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hermes-smd-overlay"))
    from establish_intake import blank_form

    made = M.make(CLS, firm_brief, _src(firm_brief), FIRM)
    assert blank_form.check(made.data, made.fixed_strings).ok
    planted = _with_footer(made.data, "Gamma Example v. Delta Example")
    assert not blank_form.check(planted, made.fixed_strings).ok
    assert json.dumps(made.residue)  # counts only
