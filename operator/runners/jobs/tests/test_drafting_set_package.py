""" "Sets of discovery" naming no kind, to parties named by a short reference.

The 2026-11-23 request (every value here invented): an attorney replied in a
matter's thread "Please also prepare sets of discovery to the middle school
and school district." No kind of set was named and the firm had authored no
default sets, so the plan made one document of no kind; the job read the
whole file for 42 minutes, then held ``request_incomplete`` with a question
for the attorney. The falsifiers:

1. The request plans the standard package (special interrogatories, requests
   for admission, requests for production) to EACH party it names, with no
   question; on main it planned one document of no kind and no party.
2. "The middle school" and "the school district" name the caption's parties
   whose last words they are; a short reference two parties share, or one no
   party has, still stops ``party_unknown`` (never a guess).
3. "Also" takes the kinds the same requester's earlier set job on the matter
   planned, when there is one.
4. The attorney notes say which, in one line, never a question.
5. ``request_incomplete`` is SMD's: it never relays a question to the firm.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from drafting_testkit import CAPTION, seat_with, standard_docs
from jobs.drafting import endings, parties, set_package
from test_drafting_review import Client, pricing  # noqa: F401 - the fixture
from test_drafting_served import _record, _runner, _stop_at_finish

ASK = "Please also prepare sets of discovery to the middle school and school district.\n\nThanks,\nChris"
SCHOOL, DISTRICT = "NORTHWOOD EXAMPLE MIDDLE SCHOOL", "RIVERBEND EXAMPLE UNIFIED SCHOOL DISTRICT"
PEOPLE = ["ROE, ALEX", "POE, CASEY"]
VALUES = [SCHOOL, DISTRICT, *PEOPLE]
SROG, RFA, RFP = "Special Interrogatories", "Requests for Admission", "Requests for Production"


# ---- 1. the plan: the standard package to each named party --------------------------------


def test_sets_of_discovery_naming_no_kind_wants_the_package():
    assert set_package.wants_package(ASK)
    assert set_package.wants_package("Please draft discovery to the school district.")
    assert not set_package.wants_package("Please draft RFAs to the school district.")
    assert not set_package.wants_package("Please draft form interrogatories to the school district.")
    assert not set_package.wants_package("Please draft a set to the district about the bus route.")


def test_the_incident_request_plans_three_sets_to_each_named_party_with_no_question(tmp_path, pricing, monkeypatch):
    """FALSIFIER: on main this planned [{"key": "", "variant": None}] and the
    compose asked the attorney which kind, after the whole file was read."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    cap = json.dumps({**CAPTION, "defendants": VALUES})
    client = Client(caption=[cap])
    seat = seat_with(standard_docs(), record=_record("Northwood Example Middle School", *PEOPLE))
    r = _runner(tmp_path, pricing, ASK, "discovery_set", client, seat)
    v = r.run()
    assert "request_incomplete" not in (v.reason or "") and "party_unknown" not in (v.reason or ""), v.reason
    plan = seen["units"]
    assert [(p["label"], p["party"]) for p in plan] == [(k, p) for p in (SCHOOL, DISTRICT) for k in (SROG, RFA, RFP)]
    assert r._state()[set_package.BASIS_KEY] == "standard"


# ---- 2. short references to a caption party ---------------------------------------------------


def test_the_middle_school_and_school_district_name_the_two_caption_parties():
    got = parties.resolve(ASK, VALUES)
    assert got.planned == [SCHOOL, DISTRICT]


def test_a_short_reference_two_parties_share_stops():
    values = ["ALDER EXAMPLE SCHOOL DISTRICT", "BIRCH EXAMPLE SCHOOL DISTRICT", "ROE, ALEX"]
    with pytest.raises(parties.PartyUnknown, match="could be"):
        parties.resolve("Please prepare sets of discovery to the school district.", values)


def test_a_short_reference_no_party_has_stops():
    with pytest.raises(parties.PartyUnknown, match="names none"):
        parties.resolve("Please prepare sets of discovery to the high school.", VALUES)


def test_a_school_in_the_facts_is_not_a_party():
    """Only after a sending word and "the": "the incident at the middle school" names no one."""
    got = parties.named_in("Discovery about the incident at the middle school.", parties.candidates(VALUES))
    assert got == []


# ---- 3. "also": the earlier request's kinds ---------------------------------------------------


def _job_dir(root, job_id, *, matter="m-1", requester="chris@firm.example", kinds=()):
    d = root / job_id
    (d / "data").mkdir(parents=True)
    env = {"document_class": "discovery_set", "matter_id": matter, "requester": requester}
    (d / "job.json").write_text(json.dumps(env))
    plan = [{"key": f"set-{i}", "variant": k, "label": k} for i, k in enumerate(kinds, 1)]
    (d / "data" / "state.json").write_text(json.dumps({"units": plan}))
    return d


def test_also_takes_the_kinds_the_same_requesters_earlier_set_job_planned(tmp_path):
    _job_dir(tmp_path, "01A", kinds=["requests_for_admission", "requests_for_production"])
    _job_dir(tmp_path, "01B", requester="someone@firm.example", kinds=["special_interrogatories"])
    _job_dir(tmp_path, "01C", matter="m-2", kinds=["special_interrogatories"])
    here = _job_dir(tmp_path, "01D")
    job = SimpleNamespace(job_dir=here, job_id="01D", matter_id="m-1", requester="Chris@firm.example")
    units, basis = set_package.plan(ASK, job)
    assert ([u.key for u in units], basis) == (["requests_for_admission", "requests_for_production"], "prior")
    # Without "also" (or with no earlier job) it is the standard package.
    units, basis = set_package.plan("Please prepare sets of discovery to the district.", job)
    assert ([u.key for u in units], basis) == (list(set_package.STANDARD), "standard")


# ---- 4. the notes line, 5. the ending ---------------------------------------------------------


def test_the_notes_say_which_sets_in_one_line_and_ask_nothing():
    kinds = [*set_package.STANDARD, *set_package.STANDARD]  # three sets to each of two parties
    line = set_package.note("standard", kinds)
    assert line == (
        "You asked for sets of discovery without naming the kinds; these are the special interrogatories, "
        "requests for admission and requests for production."
    )
    assert "?" not in line and set_package.note(None, kinds) is None


def test_request_incomplete_is_smds_and_relays_nothing_to_the_firm():
    e = endings.ending("request_incomplete")
    assert (e.owner, e.message) == ("smd", "none") and endings.FIRM_MESSAGES[e.message] == ""
