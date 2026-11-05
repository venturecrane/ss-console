"""W7 of ADR 0104: a case file's facts come from the matter's own record.

The answer key (``answer_keys/casefile_dates.json``) was written before the
code. The header of a File Status (clients, type, status, opened, staff, date of
loss, statute) is code's reading of the inventory; what the reader types for
those fields is replaced and the difference recorded. A date code does not have
is the reader's only when a document it read prints it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.casefile import facts, firm as firm_mod, pack, rank as rank_mod, validate
from jobs.casefile.book import render_html

import casefile_testkit as kit

KEY = json.loads((Path(__file__).parent / "answer_keys" / "casefile_dates.json").read_text(encoding="utf-8"))
REMINDERS = [r"1 week", r"1 month before statute runs", r"3 months until statute runs", r"1 week before statute runs"]
DOC = "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1"


@pytest.fixture
def firm(tmp_path: Path) -> firm_mod.CasefileFirm:
    return firm_mod.load(kit.make_inputs(tmp_path / "inputs", statute_reminder_patterns=REMINDERS))


def _inv(case: dict | None = None, **over: object) -> dict:
    case = case or {}
    values = {}
    for short, key in (("dol", KEY["layout_dol"]), ("sol", KEY["layout_sol"])):
        if short in (case.get("layout") or {}):
            values[key] = case["layout"][short]
    inv = {
        "matter": {
            "id": "11111111-1111-4111-8111-111111111111",
            "number": "100001",
            "status": "Open",
            "responsible": "Riley Example",
            "assisting": "Pat Example",
        },
        "detail": {"openedDate": "2025-08-28T00:00:00", "matterType": {"name": "Motor Vehicle Accident"}},
        "clients": [{"id": "c1", "name": "Gamma Example"}],
        "layouts": {"item": {"row": {}, "values": values}},
        "tasks_open": case.get("tasks_open") or [],
        "tasks_done": case.get("tasks_done") or [],
    }
    inv.update(over)
    return inv


@pytest.mark.parametrize("case", KEY["cases"], ids=[c["name"] for c in KEY["cases"]])
def test_the_answer_key(case: dict, firm: firm_mod.CasefileFirm) -> None:
    got = facts.header(_inv(case), firm)
    expect = case["expect"]
    for field in ("date_of_loss", "date_of_loss_source", "statute_date", "statute_source"):
        assert got[field] == expect[field], field
    assert bool(got["questions"]) is expect["asks"]


def test_the_header_is_the_inventorys(firm: firm_mod.CasefileFirm) -> None:
    got = facts.header(_inv(), firm)
    assert got["clients"] == ["Gamma Example"]
    assert got["matter_type"] == "Motor Vehicle Accident"
    assert (got["status"], got["opened"]) == ("Open", "2025-08-28")
    assert (got["responsible"], got["assisting"]) == ("Riley Example", "Pat Example")
    assert (got["number"], got["matter_id"]) == ("100001", "11111111-1111-4111-8111-111111111111")


def test_a_matter_type_read_back_as_a_plain_string_and_an_unnamed_client(firm: firm_mod.CasefileFirm) -> None:
    inv = _inv(
        detail={"openedDate": None, "matterType": "Premises"},
        clients=[{"name": "(no name entered)"}, {"name": "Delta–Example"}],
    )
    got = facts.header(inv, firm)
    assert got["matter_type"] == "Premises" and got["opened"] == ""
    # A record holding a dash is refused unfixably, so a firm's spelling is flattened in code.
    assert got["clients"] == ["Delta-Example"]


# ---- settling a typed record ------------------------------------------------------------------
def test_what_the_reader_typed_is_replaced_and_the_difference_is_recorded(firm: firm_mod.CasefileFirm) -> None:
    case = {"layout": {"dol": "2025-08-12", "sol": "2027-08-12"}}
    typed = {
        "number": "100001",
        "clients": ["Gamma Exampel"],
        "matter_type": "MVA",
        "status": "Open",
        "opened": "2025-08-28",
        "responsible": "Riley Example",
        "date_of_loss": "2025-08-21",
        "statute_date": "2027-08-21",
        "questions": ["Delete the undated copies?"],
    }
    out, differences = facts.settle(typed, _inv(case), firm)
    assert out["clients"] == ["Gamma Example"] and out["matter_type"] == "Motor Vehicle Accident"
    assert out["date_of_loss"] == "2025-08-12" and out["statute_date"] == "2027-08-12"
    assert out["statute_source"] == "case_details" and out["date_of_loss_source"] == "case_details"
    assert {d["field"] for d in differences} == {"clients", "matter_type", "date_of_loss", "statute_date"}
    assert {"field": "statute_date", "typed": "2027-08-21", "code": "2027-08-12"} in differences
    assert out["questions"] == ["Delete the undated copies?"]
    assert typed["clients"] == ["Gamma Exampel"]  # the reader's answer is not mutated


def test_settling_twice_changes_nothing(firm: firm_mod.CasefileFirm) -> None:
    case = {
        "layout": {"sol": "2027-08-12"},
        "tasks_open": [{"subject": "1 week before statute runs", "dueDate": "2027-10-05"}],
    }
    once, _ = facts.settle({"questions": ["Mine?"]}, _inv(case), firm)
    twice, differences = facts.settle(once, _inv(case), firm)
    assert twice == once and differences == []
    assert len(once["questions"]) == 2 and once["questions"][0] == "Mine?"


def test_a_conflict_the_firm_settled_stops_being_asked(firm: firm_mod.CasefileFirm) -> None:
    case = {
        "layout": {"sol": "2027-08-12"},
        "tasks_open": [{"subject": "1 week before statute runs", "dueDate": "2027-10-05"}],
    }
    asked, _ = facts.settle({"questions": []}, _inv(case), firm)
    assert asked["questions"] and asked["questions"][0].startswith(facts.ASKS)
    fixed = {**case, "tasks_open": [{"subject": "1 week before statute runs", "dueDate": "2027-08-05"}]}
    later, _ = facts.settle(asked, _inv(fixed), firm)
    assert later["questions"] == []


def test_the_statute_window_is_ranks_alone(firm: firm_mod.CasefileFirm) -> None:
    """The contract asks the reader for "a statute inside 120 days" while
    ``rank`` holds 60 and 180. Code has the date, so the reader's restatement
    goes, and a finding that states no date (a reminder with none) stays."""
    typed = {
        "time_sensitive": [
            {"finding": "Statute runs 8/12/27; no complaint in the file.", "source_doc_ids": []},
            {"finding": "A statute reminder has no due date.", "source_doc_ids": []},
            {"finding": "Government claim deadline 2/1/27.", "source_doc_ids": []},
        ]
    }
    out, _ = facts.settle(typed, _inv({"layout": {"sol": "2027-08-12"}}), firm)
    assert [f["finding"] for f in out["time_sensitive"]] == [
        "A statute reminder has no due date.",
        "Government claim deadline 2/1/27.",
    ]
    kept, _ = facts.settle(typed, _inv(), firm)  # code has no date: the reader's finding is all there is
    assert len(kept["time_sensitive"]) == 3


def test_rank_reads_the_settled_date(firm: firm_mod.CasefileFirm) -> None:
    """The window is ``rank``'s and the date is code's: a typed date far off
    does not move the file out of "Pull today"."""
    import datetime as dt

    today = dt.date(2027, 7, 1)
    inv = _inv({"layout": {"sol": "2027-08-12"}})
    out, _ = facts.settle({"statute_date": "2029-01-01", "posture": "pre_demand"}, inv, firm)
    assert rank_mod.rank(out, inv, firm, today, set())["priority"] == 1


# ---- a date code does not have ----------------------------------------------------------------
def _meta() -> dict:
    return {DOC: {"state": "text 400 chars", "name": "Coverage letter.pdf"}}


def _record(**over: object) -> dict:
    return {
        "number": "100001",
        "headline": "Synthetic.",
        "posture": "pre_demand",
        "posture_why": "The intake memo.",
        "facilities": [],
        "open_tasks": [],
        "done_tasks_checked": [],
        "records_to_order": [],
        "medicals": [],
        "insurance": {},
        "injury_flags": [],
        "time_sensitive": [],
        "letters": [],
        "questions": [],
        **over,
    }


def _problems(firm: firm_mod.CasefileFirm, inv: dict, typed: dict, text: str = "") -> list[str]:
    settled, _ = facts.settle(_record(**typed), inv, firm)
    return validate.check(settled, "100001", inv, _meta(), firm, lambda _d: text)


@pytest.mark.parametrize("field,cite", [("statute_date", "statute_source_doc_id"), ("date_of_loss", "date_of_loss_source_doc_id")])
def test_a_date_code_lacks_stands_only_on_a_document_printing_it(
    firm: firm_mod.CasefileFirm, field: str, cite: str
) -> None:
    inv = _inv()
    assert not _problems(firm, inv, {}), "no date at all is no violation"
    assert any(p.startswith("header_date:") for p in _problems(firm, inv, {field: "2027-08-12"}))
    assert any(
        p.startswith("header_date:")
        for p in _problems(firm, inv, {field: "2027-08-12", cite: DOC}, "The statute is two years from the loss.")
    )
    assert not _problems(firm, inv, {field: "2027-08-12", cite: DOC}, "The statute runs August 12, 2027.")
    assert not _problems(firm, inv, {field: "2027-08-12", cite: DOC}, "Limitations: 8/12/27.")


def test_a_cited_document_that_was_not_read_proves_no_date(firm: firm_mod.CasefileFirm) -> None:
    inv = _inv()
    settled, _ = facts.settle(_record(statute_date="2027-08-12", statute_source_doc_id=DOC), inv, firm)
    meta = {DOC: {"state": "SCAN, no text layer (3 pages)", "name": "x"}}
    problems = validate.check(settled, "100001", inv, meta, firm, lambda _d: "August 12, 2027")
    assert any(p.startswith("header_date:") for p in problems)


def test_a_date_that_is_not_a_date_is_refused(firm: firm_mod.CasefileFirm) -> None:
    assert any(p.startswith("header_date:") for p in _problems(firm, _inv(), {"statute_date": "soon"}))


def test_the_validator_compares_the_header_to_the_record(firm: firm_mod.CasefileFirm) -> None:
    """A record that skipped ``settle`` or lost a fact after it is refused."""
    inv = _inv({"layout": {"sol": "2027-08-12"}})
    settled, _ = facts.settle(_record(), inv, firm)
    assert validate.check(settled, "100001", inv, _meta(), firm, lambda _d: "") == []
    typed = {**settled, "statute_date": "2029-01-01"}
    assert any(p.startswith("header:") and "statute_date" in p for p in validate.check(typed, "100001", inv, _meta(), firm, lambda _d: ""))
    typed = {**settled, "clients": ["Someone Else"]}
    assert any(p.startswith("header:") and "clients" in p for p in validate.check(typed, "100001", inv, _meta(), firm, lambda _d: ""))


# ---- state already on the seat ----------------------------------------------------------------
def test_a_stored_record_with_an_uncited_date_reads_as_it_stood_when_code_has_none(
    firm: firm_mod.CasefileFirm,
) -> None:
    """A delivered record from before this change has typed dates and no
    source. Re-ranking must not lose a statute date it holds: a deadline that
    silently leaves the ranking is worse than one with no source, so it stays,
    source empty, until a re-read must cite it or clear it."""
    stored = {"statute_date": "2027-08-12", "date_of_loss": "2025-08-12", "clients": ["Typed Name"], "posture": "pre_demand"}
    out, _ = facts.settle(stored, _inv(), firm)
    assert out["statute_date"] == "2027-08-12" and out["statute_source"] == ""
    assert out["clients"] == ["Gamma Example"]


def test_code_wins_over_a_stored_typed_date(firm: firm_mod.CasefileFirm) -> None:
    stored = {"statute_date": "2027-08-21"}
    out, differences = facts.settle(stored, _inv({"layout": {"sol": "2027-08-12"}}), firm)
    assert out["statute_date"] == "2027-08-12" and differences


# ---- what the reader and the firm see ----------------------------------------------------------
def test_the_pack_gives_the_reader_the_header_and_says_who_owns_it(firm: firm_mod.CasefileFirm) -> None:
    case = {"layout": {"dol": "2025-08-12"}, "tasks_open": [{"subject": "1 week before statute runs", "dueDate": "2027-08-05"}]}
    lines = "\n".join(pack._header(_inv(case), None, firm=firm))
    assert "- date of loss: 2025-08-12 (the case details)" in lines
    assert "- statute: 2027-08-12 (a statute reminder task)" in lines
    assert "recorded by code" in lines
    bare = "\n".join(pack._header(_inv(), None, firm=firm))
    assert "- statute: not in the case details or a reminder task" in bare


@pytest.mark.parametrize("done,word", [(14, "every completed task"), (15, "the opening-workflow ones")])
def test_the_count_of_completed_tasks_to_check_is_codes(done: int, word: str) -> None:
    inv = _inv(tasks_done=[{"id": str(i), "subject": "x"} for i in range(done)])
    assert word in "\n".join(pack._activity(inv))


def test_the_file_status_says_when_a_statute_date_is_not_from_the_case_details(firm: firm_mod.CasefileFirm) -> None:
    case = {"tasks_open": [{"subject": "1 week before statute runs", "dueDate": "2027-08-05"}]}
    rec, _ = facts.settle(_record(), _inv(case), firm)
    page = render_html(rec, "2026-11-05", {}, {"priority": 4, "reason": "x"}, {})
    assert "statute August 12, 2027 (from a statute reminder task)" in page
    rec, _ = facts.settle(_record(), _inv({"layout": {"sol": "2027-08-12"}}), firm)
    page = render_html(rec, "2026-11-05", {}, {"priority": 4, "reason": "x"}, {})
    assert "statute August 12, 2027 |" in page
