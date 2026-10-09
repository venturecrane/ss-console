"""The caption corrects the matter record where the court's own paper settles
it: the misspelling and case-number rules, the two proven write paths (a
contact PUT and a layout PATCH that re-sends set dates), the restore when
anything else moves, and the job never blocked by any of it.

Every name and number here is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import (
    CAPTION,
    CASE_KEY,
    COMPLAINT,
    SOL_KEY,
    FakeSmokeball,
    ScriptedClient,
    contact,
    make_inputs,
    make_job,
    seat_with,
    standard_docs,
)
from medchron_testkit import PRICING
from medchron.drafting import caption, caption_fix, caption_read, caption_rules as rules
from medchron.drafting.run import DraftingRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    monkeypatch.setattr(caption_fix, "SLEEP", lambda _s: None)


# ---- the rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "court, record, fixable",
    [
        ("Greta Brantovez", "Greta Brantorovez", True),
        ("Zenya Example", "Zneya Example", True),
        ("Pellian Example", "Pelian Example", True),
        ("Corin Example", "Corine Example", False),  # a prefix: a different name
        ("Gamma Q. Example", "Gamma Example", False),  # a middle initial added
        ("Gamma Example", "Gamma Q Example", False),  # dropped
        ("Anthony Example", "Tony Example", False),  # a nickname
        ("EXAMPLECO, INC.", "ExampleCo", False),  # a company suffix
        ("Gamma Example", "GAMMA EXAMPLE.", False),  # capitals and punctuation are no error
        ("Example & Sons", "Example and Sonz", True),  # "&" reads as "and"
        ("Beta Example", "Bo Example", False),  # a two-letter word is never corrected
        ("Gamma Example", "Gamma Elpmaxe", False),  # more than two edits
    ],
)
def test_only_a_misspelling_is_fixable(court, record, fixable):
    assert rules.is_misspelling(court, record) is fixable


def test_capitals_and_punctuation_alone_are_the_same_name():
    assert rules.same_name("EXAMPLECO, INC.", "ExampleCo Inc") and not rules.same_name("ExampleCo", "EXAMPLECO, INC.")


@pytest.mark.parametrize(
    "court, record, ok",
    [
        ("24CV012345", "24CV12345", True),
        ("24-0012-00012345", "24-12-12345", True),
        ("S-CV-0012345", "S-CV-12345", True),
        ("24CV012345", "24CV012345", False),  # nothing dropped
        ("24CV012345", "24CV012346", False),  # a different number
        ("24CV012345", "25CV012345", False),
        ("24CV012345", "", False),
    ],
)
def test_a_dropped_zero_is_the_only_case_number_correction(court, record, ok):
    assert rules.dropped_zeros(court, record) is ok


def test_only_a_court_format_is_ever_filled():
    for good in ("24CV012345", "24-1234-56789012", "S-CV-0012345"):
        assert rules.fillable(good)
    for bad in ("CV-0001", "CLAIM 123456", "24CV12345", "24CV012345 / 24CV012346"):
        assert not rules.fillable(bad)


# ---- the comparison tags only what the rules settle ----------------------------------------------


def _v(value: str, verified: bool = True) -> dict:
    return {"value": value, "verified": verified, "quote": value if verified else ""}


def _fields(**over) -> dict:
    f = {
        "case_number": _v("24CV012345"),
        "court_name": _v("SUPERIOR COURT OF THE STATE OF CALIFORNIA"),
        "county": _v("EXAMPLETOWN"),
        "plaintiffs": [_v("GRETA BRANTOVEZ")],
        "defendants": [_v("DELTA EXAMPLE")],
    }
    f.update(over)
    return f


def _record(**over) -> dict:
    r = {
        "case_number": "",
        "court": "Superior Court",
        "county": "Exampletown",
        "attorney_email": None,
        "parties": [
            {"side": "client", "name": "Greta Brantorovez", "contact_id": "c-client"},
            {"side": "other", "name": "Delta Example", "contact_id": "c-other"},
        ],
    }
    r.update(over)
    return r


def _fixes(diffs: list[dict]) -> dict:
    return {d["field"]: d.get("fix") for d in diffs}


def test_an_empty_case_number_is_filled_and_a_misspelled_client_is_fixed():
    diffs, compared = caption.compare(_fields(), _record(), "Complaint")
    fixes = _fixes(diffs)
    assert fixes["case_number"] == {"kind": "case_number"}
    assert fixes["plaintiff"] == {"kind": "contact", "contact_id": "c-client"}
    assert {"court", "county", "plaintiff", "defendant"} <= set(compared)


def test_a_different_case_number_is_reported_never_written():
    diffs, _ = caption.compare(_fields(), _record(case_number="24CV012346"), "Complaint")
    assert [(d["field"], d.get("fix")) for d in diffs if d["field"] == "case_number"] == [("case_number", None)]
    zeros, _ = caption.compare(_fields(), _record(case_number="24CV12345"), "Complaint")
    assert _fixes(zeros)["case_number"] == {"kind": "case_number"}


def test_an_unverified_value_is_never_a_fix():
    f = _fields(case_number=_v("24CV012345", False), plaintiffs=[_v("GRETA BRANTOVEZ", False)])
    diffs, _ = caption.compare(f, _record(), "Complaint")
    assert diffs and all("fix" not in d for d in diffs)


def test_the_court_and_county_are_compared_by_their_own_keys_and_only_reported():
    diffs, compared = caption.compare(_fields(), _record(court="Justice Court", county="Othertown"), "c")
    out = {d["field"]: d for d in diffs}
    assert set(compared) >= {"court", "county"} and {"court", "county"} <= set(out)
    assert "fix" not in out["court"] and "fix" not in out["county"]
    same, _ = caption.compare(_fields(), _record(county="County of Exampletown"), "c")
    assert "county" not in {d["field"] for d in same}


def test_the_client_side_is_whichever_caption_side_matches_the_client_roles():
    # a cross-complaint: the firm's client is printed as the defendant
    f = _fields(plaintiffs=[_v("DELTA EXAMPLE")], defendants=[_v("GRETA BRANTOVEZ")])
    assert caption.client_side(f, _record()) == "defendants"
    diffs, _ = caption.compare(f, _record(), "Cross-Complaint")
    assert _fixes(diffs)["defendant"] == {"kind": "contact", "contact_id": "c-client"}


def test_the_record_reader_reads_each_court_key_alone():
    """A pattern on "court" matched AuthorityCourt and JurisdictionCourt, so the
    court was never read; each key is now read by its own name."""
    layout = {
        CASE_KEY: "",
        "Matter/CaseDetails/StandardCaseDetails/AuthorityCourt": "id-123",
        "Matter/CaseDetails/StandardCaseDetails/JurisdictionCourt": "Superior Court",
        "Matter/CaseDetails/StandardCaseDetails/LocationCounty": "Exampletown",
        "Matter/CaseDetails/StandardCaseDetails/LocationDivision": "Civil",
    }

    class Client:
        def get(self, path, **_p):
            if path.endswith("/layouts"):
                return {"value": [{"id": "i1"}]}
            if "/layouts/" in path:
                return {"values": [{"key": k, "value": v} for k, v in layout.items()]}
            if path.endswith("/roles"):
                return {"roles": [{"isClient": True, "contactId": "c1"}, {"isOtherSide": True, "contactId": "c2"}]}
            if path.startswith("/contacts/c1"):
                return {"person": {"firstName": "Greta", "lastName": "Brantorovez"}}
            if path.startswith("/contacts/c2"):
                return {"company": {"name": "Delta Example"}}
            return {}

    rec = caption.record_from_client(Client(), "m", {})
    assert rec["case_number"] == "" and rec["court"] == "Superior Court" and rec["county"] == "Exampletown"
    assert rec["parties"] == [
        {"side": "client", "name": "Greta Brantorovez", "contact_id": "c1"},
        {"side": "other", "name": "Delta Example", "contact_id": "c2"},
    ]


# ---- the contact write -----------------------------------------------------------------------------


def test_a_contact_correction_changes_only_the_misspelled_word_and_keeps_every_other_field():
    sb = FakeSmokeball(contacts={"c1": contact("c1", "GRETA", "BRANTOROVEZ", middle="Q")})
    before = json.loads(json.dumps(sb.contacts["c1"]))
    got = caption_fix.fix_contact(sb, "c1", "Greta Brantovez", "GRETA BRANTOROVEZ")
    assert got["status"] == "applied"
    after = sb.get("/contacts/c1")
    assert after["person"]["lastName"] == "BRANTOVEZ"  # the record's all-caps style kept
    for k in ("href", "versionId", "lastUpdated"):
        before.pop(k), after.pop(k)
    before["person"]["lastName"] = "BRANTOVEZ"
    assert after == before
    method, path, body = sb.writes[0]
    assert (method, path) == ("PUT", "/contacts/c1") and body["person"]["birthDate"] == "1980-01-01"


def test_a_title_case_record_stays_title_case():
    sb = FakeSmokeball(contacts={"c1": contact("c1", "Zneya", "Example")})
    assert caption_fix.fix_contact(sb, "c1", "ZENYA EXAMPLE", "Zneya Example")["status"] == "applied"
    assert sb.get("/contacts/c1")["person"]["firstName"] == "Zenya"


def test_a_contact_write_that_moves_another_field_is_put_back():
    def mangle(kind, body):
        body["person"]["email"] = None  # the vendor drops a field

    sb = FakeSmokeball(contacts={"c1": contact("c1", "Pelian", "Example")}, mangle=mangle)
    before = json.loads(json.dumps(sb.contacts["c1"]["person"]))
    got = caption_fix.fix_contact(sb, "c1", "Pellian Example", "Pelian Example")
    assert got["status"] == "restored" and "more than the name" in got["why"]
    assert sb.get("/contacts/c1")["person"] == before
    assert [w[0] for w in sb.writes] == ["PUT", "PUT"]


def test_a_restore_that_does_not_hold_is_restore_incomplete():
    def mangle(kind, body):
        body["person"]["email"] = None

    sb = FakeSmokeball(contacts={"c1": contact("c1", "Pelian", "Example")}, mangle=mangle, mangle_restores=True)
    assert caption_fix.fix_contact(sb, "c1", "Pellian Example", "Pelian Example")["status"] == "restore_incomplete"


def test_a_name_that_is_not_a_misspelling_is_never_written():
    sb = FakeSmokeball(contacts={"c1": contact("c1", "Corin", "Example")})
    assert caption_fix.fix_contact(sb, "c1", "Corine Example", "Corin Example")["status"] == "not_applied"
    assert sb.writes == []


# ---- the case-number write ---------------------------------------------------------------------


def _layout() -> dict:
    return {CASE_KEY: "24CV12345", SOL_KEY: "2027-01-15", "Matter/CaseDetails/StandardCaseDetails/TrialDate": ""}


def test_a_case_number_correction_re_sends_every_set_date():
    sb = FakeSmokeball(layout=_layout())
    got = caption_fix.fix_case_number(sb, "m-1", "24CV012345")
    assert got["status"] == "applied"
    assert sb.items["item-case"][CASE_KEY] == "24CV012345" and sb.items["item-case"][SOL_KEY] == "2027-01-15"
    sent = {v["key"]: v["value"] for v in sb.writes[0][2]["values"]}
    assert sent == {CASE_KEY: "24CV012345", SOL_KEY: "2027-01-15"}  # set dates only; an empty one is not sent


def test_without_the_date_re_send_the_write_is_caught_and_put_back(monkeypatch):
    """The incident, replayed: a PATCH carrying only the case number clears the
    statute date. The write must NOT count as applied, and the date must come
    back. (This is the test that fails if the dates are not re-sent: drop
    ``resent_dates`` from the first PATCH and the applied test above fails.)"""
    real = caption_fix.resent_dates
    calls = {"n": 0}

    def first_write_forgets(values):
        calls["n"] += 1
        return {} if calls["n"] == 1 else real(values)

    monkeypatch.setattr(caption_fix, "resent_dates", first_write_forgets)
    sb = FakeSmokeball(layout=_layout())
    got = caption_fix.fix_case_number(sb, "m-1", "24CV012345")
    assert got["status"] == "restored" and SOL_KEY in got["why"]
    assert sb.items["item-case"] == _layout()  # every moved key back, the case number included


def test_a_layout_restore_that_does_not_hold_is_loud():
    def mangle(kind, values):
        values["Matter/CaseDetails/StandardCaseDetails/Notes"] = "changed"

    sb = FakeSmokeball(layout=_layout(), mangle=mangle, mangle_restores=True)
    got = caption_fix.fix_case_number(sb, "m-1", "24CV012345")
    assert got["status"] == "restore_incomplete" and got["still_differs"]


# ---- the read ------------------------------------------------------------------------------------


def test_the_answer_is_parsed_from_the_outermost_object():
    got = caption_read.parse("Here it is:\n```json\n" + json.dumps(CAPTION) + "\n```\nDone.")
    assert got is not None and got["plaintiffs"] == ["GAMMA EXAMPLE"] and got["attorney"]["name"] == "ALPHA EXAMPLE"
    assert caption_read.parse("no json here") is None and caption_read.parse("[1, 2]") is None


def test_a_value_is_verified_only_by_contiguous_words_of_the_text_layer():
    page = caption_read.words(COMPLAINT)
    assert caption_read.verified("Gamma Example", page) and caption_read.verified("Case No. CV-0001", page)
    assert not caption_read.verified("Example Gamma", page) and not caption_read.verified("Gamma Exampel", page)


def test_a_scanned_page_verifies_nothing_so_nothing_is_fixed():
    got = {"caption": caption_read.parse(json.dumps(CAPTION)), "text_chars": 40, "text": "GAMMA EXAMPLE CV-0001"}
    fields = caption_read.fields(got, ("firm.example",), True)
    assert all(not v["verified"] for v in fields["plaintiffs"]) and not fields["case_number"]["verified"]
    rec = {"case_number": "", "parties": [{"side": "client", "name": "Gamma Exampel", "contact_id": "c1"}]}
    diffs, _ = caption.compare(fields, rec, "Complaint")
    assert diffs and all("fix" not in d for d in diffs)


def test_an_answers_attorney_block_is_opposing_counsels_and_is_not_used():
    got = {"caption": caption_read.parse(json.dumps(CAPTION)), "text_chars": 400, "text": COMPLAINT}
    assert "attorney_email" in caption_read.fields(got, ("firm.example",), True)
    theirs = caption_read.fields(got, ("firm.example",), False)
    assert "attorney_email" not in theirs and "attorney" not in theirs


# ---- the job ---------------------------------------------------------------------------------------


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, seat, client):
    inputs = tmp_path / "inputs"
    make_inputs(inputs)
    r = DraftingRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
    )
    return r, r.run()


def _seat_with_record(sb: FakeSmokeball):
    seat = seat_with(
        standard_docs(),
        record={
            "case_number": "CV-0001",
            "court": "Superior Court",
            "county": "Exampletown",
            "attorney_email": "alpha@firm.example",
            "parties": [
                {"side": "client", "name": "Gamma Exampel", "contact_id": "c1"},
                {"side": "other", "name": "Delta Example", "contact_id": "c2"},
            ],
        },
    )
    seat.client = sb
    return seat


def test_the_job_corrects_a_misspelled_client_and_reports_it(tmp_path, pricing):
    sb = FakeSmokeball(contacts={"c1": contact("c1", "Gamma", "Exampel")})
    client = ScriptedClient()
    r, v = _run(tmp_path, pricing, _seat_with_record(sb), client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert v.caption_corrections == [
        {"field": "plaintiff", "from": "Gamma Exampel", "to": "GAMMA EXAMPLE", "source_document": "Complaint 2-1-26"}
    ]
    assert sb.get("/contacts/c1")["person"]["lastName"] == "Example"
    assert v.caption_discrepancies == [] and not v.reason
    notes = r._notes_md([], [])
    assert (
        'Corrected in Smokeball from the court\'s Complaint 2-1-26: plaintiff "Gamma Exampel" -> "GAMMA EXAMPLE"'
        in notes
    )
    reads = [c for c in client.calls if "COPY THE CAPTION" in json.dumps(c.get("system"))]
    assert len(reads) == 1 and reads[0]["messages"][0]["content"][0]["type"] == "image"
    ledger = (r.data / "usage-ledger.jsonl").read_text(encoding="utf-8")
    assert '"stage": "caption"' in ledger  # the read is charged to the job like every paid call
    journal = (r.data / "caption-writes.jsonl").read_text(encoding="utf-8")
    assert '"event": "attempt"' in journal and '"status": "applied"' in journal


def test_an_unparseable_caption_retries_once_then_the_job_still_delivers(tmp_path, pricing):
    sb = FakeSmokeball(contacts={"c1": contact("c1", "Gamma", "Exampel")})
    client = ScriptedClient(caption=["I could not find a caption.", "Still no."])
    r, v = _run(tmp_path, pricing, _seat_with_record(sb), client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    reads = [c for c in client.calls if "COPY THE CAPTION" in json.dumps(c.get("system"))]
    assert len(reads) == 2 and reads[1]["messages"][-1]["content"] == caption_read.RETRY
    cap = json.loads((r.data / "caption.json").read_text(encoding="utf-8"))
    assert cap["fields"] == {} and cap["corrections"] == [] and "could not be read" in cap["none_because"]
    assert v.caption_corrections == [] and sb.writes == []


def test_a_restore_incomplete_is_loud_for_smd_and_never_fails_the_document(tmp_path, pricing):
    def mangle(kind, body):
        body["person"]["email"] = None

    sb = FakeSmokeball(contacts={"c1": contact("c1", "Gamma", "Exampel")}, mangle=mangle, mangle_restores=True)
    _r, v = _run(tmp_path, pricing, _seat_with_record(sb), ScriptedClient())
    assert v.outcome == "delivered" and v.reason.startswith("caption_restore_incomplete: ")
    assert v.caption_corrections == [] and v.caption_restore_incomplete
    assert [d["field"] for d in v.caption_discrepancies] == ["plaintiff"]
