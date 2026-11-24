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
   whose last words they are, only where the request sends something and the
   phrase ends there; a fact ("the middle school playground", "the district
   attorney", "the school district's counsel") names no one; a short
   reference two parties share, or one no party has, stops ``party_unknown``.
3. Only a request to propound new discovery is a package: a redo, a response,
   "a set", a deposition is not, and goes to SMD as before.
4. "Also" in the asking clause takes the kinds the same requester's newest
   earlier set job on the matter planned, within 30 days.
5. The attorney notes say which, in one line, never a question.
6. ``request_incomplete`` is SMD's: never relayed, and SMD's triage line
   never says to tell the requester.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bin.lib import work_health_drafting as triage
from drafting_testkit import CAPTION, seat_with, standard_docs
from jobs.drafting import endings, parties, set_package
from test_drafting_review import Client, pricing  # noqa: F401 - pytest finds the fixture by this import
from test_drafting_served import _record, _runner, _stop_at_finish, _two_defendants

ASK = "Please also prepare sets of discovery to the middle school and school district.\n\nThanks,\nChris"
SCHOOL, DISTRICT = "NORTHWOOD EXAMPLE MIDDLE SCHOOL", "RIVERBEND EXAMPLE UNIFIED SCHOOL DISTRICT"
PEOPLE = ["ROE, ALEX", "POE, CASEY"]
VALUES = [SCHOOL, DISTRICT, *PEOPLE]
SROG, RFA, RFP = "Special Interrogatories", "Requests for Admission", "Requests for Production"
DAY = 86_400_000


# ---- 1. the plan: the standard package to each named party --------------------------------


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


def _named(text: str, values: list[str] = VALUES) -> list[str]:
    return [c.text for c in parties.named_in(text, parties.candidates(values))]


def test_the_middle_school_and_school_district_name_the_two_caption_parties():
    assert parties.resolve(ASK, VALUES).planned == [SCHOOL, DISTRICT]


@pytest.mark.parametrize(
    "text, want",
    [
        ("SROGs to Alex Roe about the injury on the middle school playground.", ["ROE, ALEX"]),
        ("RFAs to Alex Roe about what he told to the district attorney.", ["ROE, ALEX"]),
        ("RFPs to Alex Roe for the records for the school district.", ["ROE, ALEX"]),
        ("RFAs to Alex Roe, with a copy to the school district attorney.", ["ROE, ALEX"]),
        ("SROGs to Alex Roe about his duties for the district.", ["ROE, ALEX"]),
        ("RFAs to Alex Roe; send them to the school district's counsel.", ["ROE, ALEX"]),
    ],
)
def test_a_school_or_district_in_the_facts_is_never_a_party_served(text, want):
    """FALSIFIER (bug review, 11-24): each of these added the school or the
    district as a party served."""
    assert _named(text) == want


def test_a_short_reference_two_parties_share_stops():
    values = ["ALDER EXAMPLE SCHOOL DISTRICT", "BIRCH EXAMPLE SCHOOL DISTRICT", "ROE, ALEX"]
    with pytest.raises(parties.PartyUnknown, match="could be"):
        parties.resolve("Please prepare sets of discovery to the school district.", values)


def test_a_one_word_reference_another_partys_name_carries_stops():
    """FALSIFIER: "to the school" is the middle school's last word and a word of the district's name."""
    with pytest.raises(parties.PartyUnknown, match="could be"):
        _named("Please prepare sets of discovery to the school.")


def test_a_short_reference_no_party_has_stops():
    with pytest.raises(parties.PartyUnknown, match="names none"):
        parties.resolve("Please prepare sets of discovery to the high school.", VALUES)


def test_a_to_x_only_line_takes_a_short_reference():
    """FALSIFIER: the scope line was read without its "to", so it stopped as no party."""
    assert parties.resolve("Please draft RFAs to the School District only.", VALUES).planned == [DISTRICT]


# ---- 3. only a request to propound new discovery is a package --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        ASK,
        "Please draft discovery to the school district.",
        "Please prepare the discovery requests for the district.",
        "Can you propound written discovery on Alex Roe?",
        "We need a discovery package for the district.",
    ],
)
def test_a_request_to_propound_new_discovery_wants_the_package(text):
    assert set_package.wants_package(text)


@pytest.mark.parametrize(
    "text",
    [
        "Redo the discovery with Accommodation defined",
        "Please revise the discovery sets to define Accommodation.",
        "Please draft responses to the discovery served on us.",
        "Please prepare answers to the discovery.",
        "Please draft supplemental discovery to the district.",
        "Please draft amended discovery to the district.",
        "Please draft a set of discovery to the district.",
        "Please prepare a deposition notice and discovery to the district.",
        "Please draft a subpoena and discovery to the district.",
        "Please draft an inspection demand and discovery to the district.",
        "Please draft RFAs to the school district.",
        "Please draft form interrogatories to the school district.",
        "Please draft a set to the district about the bus route.",
        "Discovery is closing soon; please calendar it.",
    ],
)
def test_anything_else_is_never_a_package(text):
    assert not set_package.wants_package(text)


def test_a_redo_in_the_thread_of_a_delivered_package_is_never_a_new_package(tmp_path, pricing, monkeypatch):
    """FALSIFIER (bug review, 11-24): the broker queues a revision of a
    several-draft set delivery as a fresh discovery_set job; "the discovery"
    with no kind made it a new package of three sets. It plans as before, and
    what the compose cannot settle is SMD's (``request_incomplete``)."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, "Redo the discovery with Accommodation defined", "discovery_set", client, seat)
    r.run()
    assert seen["units"] == [{"key": "", "variant": None, "label": ""}]
    assert set_package.BASIS_KEY not in r._state()


# ---- 4. "also": the earlier request's kinds ---------------------------------------------------


def _ulid(ms: int) -> str:
    alphabet, out = "0123456789ABCDEFGHJKMNPQRSTVWXYZ", ""
    for _ in range(10):
        out, ms = alphabet[ms % 32] + out, ms // 32
    return out + "0" * 16


NOW = 1_795_000_000_000


def _job_dir(root, ms, *, matter="m-1", requester="chris@firm.example", kinds=()):
    d = root / _ulid(ms)
    (d / "data").mkdir(parents=True)
    env = {"document_class": "discovery_set", "matter_id": matter, "requester": requester}
    (d / "job.json").write_text(json.dumps(env))
    plan = [{"key": f"set-{i}", "variant": k, "label": k} for i, k in enumerate(kinds, 1)]
    (d / "data" / "state.json").write_text(json.dumps({"units": plan}))
    return d


def _here(root):
    d = _job_dir(root, NOW)
    return SimpleNamespace(job_dir=d, job_id=d.name, matter_id="m-1", requester="Chris@firm.example")


def test_ulid_time_reads_back():
    assert set_package.ulid_ms(_ulid(NOW)) == NOW and set_package.ulid_ms("job") is None


def test_also_takes_the_kinds_the_same_requesters_newest_earlier_set_job_planned(tmp_path):
    _job_dir(tmp_path, NOW - 3 * DAY, kinds=["requests_for_admission", "requests_for_production"])
    _job_dir(tmp_path, NOW - 2 * DAY, requester="someone@firm.example", kinds=["special_interrogatories"])
    _job_dir(tmp_path, NOW - DAY, matter="m-2", kinds=["special_interrogatories"])
    units, basis = set_package.plan(ASK, _here(tmp_path))
    assert ([u.key for u in units], basis) == (["requests_for_admission", "requests_for_production"], "prior")


@pytest.mark.parametrize(
    "text",
    [
        "Please prepare sets of discovery to the district. Same as before.",
        "Also, thanks for the update. Please prepare sets of discovery to the district.",
    ],
)
def test_also_outside_the_asking_clause_or_same_is_the_standard_package(tmp_path, text):
    """FALSIFIER: "same" anywhere, or "also" in another clause, took the earlier kinds."""
    _job_dir(tmp_path, NOW - DAY, kinds=["requests_for_admission"])
    units, basis = set_package.plan(text, _here(tmp_path))
    assert ([u.key for u in units], basis) == (list(set_package.STANDARD), "standard")


def test_the_newest_earlier_job_decides_even_when_it_planned_no_kind(tmp_path):
    """FALSIFIER: an older job's kinds were reached past a newer job of no kind."""
    _job_dir(tmp_path, NOW - 3 * DAY, kinds=["requests_for_admission"])
    _job_dir(tmp_path, NOW - DAY, kinds=[])
    units, basis = set_package.plan(ASK, _here(tmp_path))
    assert basis == "standard"


def test_an_earlier_job_older_than_thirty_days_is_not_reused(tmp_path):
    """FALSIFIER: a set job from months ago supplied the kinds."""
    _job_dir(tmp_path, NOW - 31 * DAY, kinds=["requests_for_admission"])
    units, basis = set_package.plan(ASK, _here(tmp_path))
    assert basis == "standard"


# ---- 5. the notes line, 6. the ending and SMD's triage line ------------------------------------


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


def test_smds_triage_line_for_request_incomplete_never_says_to_tell_the_requester():
    """FALSIFIER (bug review, 11-24): it shared smd_check, "... or tell the requester"."""
    step = triage.next_step("drafting", "request_incomplete") or ""
    assert step and "tell the requester" not in step
