"""A people scope (2026-10-24): who a litigation request is about, resolved in
the job against Smokeball's staff list, and every matter those people hold ANY
staff role on, in the status list and the deadlines report alike.

The incident: an attorney asked for "every litigated file I'm tagged on", and
"my cases" had no scope to become, so the request ran firm-wide. Every name,
address and number here is invented."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from litigation_testkit import M1, M2, STAFF_A, STAFF_B, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron import arrivals
from medchron.litigation import book, job as job_mod, people, review
from medchron.litigation.outcome import LitigationHold
from medchron.litigation.run import LitigationRun, court_only
from medchron.proof import holds
import datetime as dt

TODAY = dt.date(2026, 10, 7)
#: A second, disabled record carrying the same full name as STAFF_A, and no email.
STAFF_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ME = "alpha@firm.example"


def _job(scope, tmp_path: Path, **over):
    return job_mod.load(make_job(tmp_path / "j", scope=scope, **over))


def _staff():
    return [
        {"id": STAFF_A, "firstName": "Alpha", "lastName": "Example", "email": ME, "enabled": True},
        {"id": STAFF_B, "firstName": "Beta", "lastName": "Example", "email": "beta@firm.example", "enabled": True},
        {"id": STAFF_C, "firstName": "Alpha", "lastName": " Example", "enabled": False},
    ]


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


# ---- the envelope -----------------------------------------------------------------------------
def test_my_cases_parses_as_a_people_scope_never_the_whole_firm(tmp_path):
    j = _job({"people": {"names": [], "requester": ME.upper()}}, tmp_path)
    assert not j.scope_all and j.people == job_mod.People((), ME)
    assert j.attorney_staff_ids == () and j.paralegal_staff_ids == ()
    for bad in ({"people": {"names": [], "requester": None}}, {"people": {"names": ["x"]}}):
        with pytest.raises(job_mod.LitigationJobError):
            _job(bad, tmp_path / "bad")


# ---- resolution -------------------------------------------------------------------------------
def test_the_requester_by_email_expands_to_every_record_of_the_same_name():
    who = people.resolve(job_mod.People((), ME), _staff(), {})
    assert who == (people.Person("Alpha Example", tuple(sorted([STAFF_A, STAFF_C]))),)


def test_a_name_matches_by_full_name_else_by_the_firm_rosters_id():
    staff = _staff()
    assert people.resolve(job_mod.People(("beta example",), None), staff, {})[0].ids == (STAFF_B,)
    # The roster calls her "Bea Example"; Smokeball's record says Beta. The roster's id settles it.
    found = people.resolve(job_mod.People(("Bea Example",), None), staff, {"Bea Example": STAFF_B.upper()})
    assert found == (people.Person("Beta Example", (STAFF_B,)),)
    with pytest.raises(LitigationHold, match=r"^people_unresolved: "):
        people.resolve(job_mod.People(("Nobody Here",), None), staff, {})


def test_an_unmatched_requester_holds_as_the_firms_question_and_pages_nothing():
    with pytest.raises(LitigationHold) as exc:
        people.resolve(job_mod.People((), "stranger@firm.example"), _staff(), {})
    reason = str(exc.value)
    assert reason.startswith("requester_unresolved: ")
    assert holds.classify("litigation", reason) == (holds.REQUESTER_UNRESOLVED, None)
    assert holds.REQUESTER_UNRESOLVED in holds.FIRM_QUESTION


# ---- the one scope rule -----------------------------------------------------------------------
def test_any_staff_role_is_in_scope_responsible_assisting_or_originating():
    rows = {
        "resp": {"personResponsibleStaffId": STAFF_A.upper()},
        "assist": {"personResponsibleStaffId": STAFF_B, "personAssistingStaffs": [{"id": STAFF_A}]},
        "orig": {"personResponsibleStaffId": STAFF_B, "originatingStaffs": [{"id": STAFF_A}]},
        "none": {"personResponsibleStaffId": STAFF_B, "originatingStaffId": None},
    }
    job = SimpleNamespace(scope_all=False, attorney_staff_ids=(), paralegal_staff_ids=(), people=object())
    views = {k: {"roles": arrivals.staff_roles(r)} for k, r in rows.items()}
    assert sorted(k for k, v in views.items() if people.in_scope(v, job, frozenset({STAFF_A}))) == [
        "assist",
        "orig",
        "resp",
    ]
    assert people.role_labels(arrivals.staff_roles(rows["orig"]), [STAFF_A]) == ["Originating"]


def test_a_list_with_no_originating_key_on_any_row_holds_rather_than_under_count():
    job = SimpleNamespace(people=object())
    with pytest.raises(LitigationHold, match=r"^roles_unlisted: none of the 2 "):
        people.check_roles_listed(job, [False, False], 2)
    people.check_roles_listed(job, [False, True], 2)
    people.check_roles_listed(SimpleNamespace(people=None), [False], 1)


# ---- the status list, end to end ------------------------------------------------------------------
def _run(tmp_path: Path, seat: LitSeat, scope: dict) -> LitigationRun:
    jd = make_job(tmp_path / "job", scope=scope)
    return LitigationRun(
        jd,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=ScriptedLit(),
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _seat() -> LitSeat:
    docs2 = [("m2" + fid[1:], n, b, d) for fid, n, b, d in matter_docs()]
    seat = LitSeat({M1: matter_docs(), M2: docs2})
    seat.staff = _staff()
    # M1: Alpha responsible. M2: Beta responsible, Alpha's second (disabled) record assisting.
    seat.matters[1]["personAssistingStaffs"] = [{"id": STAFF_C}]
    seat.matters[1]["originatingStaffId"] = None
    return seat


def test_my_cases_lists_every_matter_i_hold_a_role_on_with_the_tagged_on_tab(tmp_path):
    r = _run(tmp_path, _seat(), {"people": {"names": [], "requester": ME}})
    v = r.run()
    assert v.verdict == "delivered", v.reason
    inv = json.loads((r.data / "inventory.json").read_text())
    assert sorted(m["id"] for m in inv["matters"]) == sorted([M1, M2])
    assert inv["people"] == [{"name": "Alpha Example", "ids": sorted([STAFF_A, STAFF_C])}]
    sheets = review.read_sheets(r._out().read_bytes())
    tab = sheets[book.TAGGED]
    assert [(row[0], row[2]) for row in tab] == [("100001", "Responsible"), ("100002", "Assisting")]
    assert book.tagged_header(False) == ["Matter #", "Case", "Role", "Case status", "Complaint filed"]


def test_an_unmatched_requester_holds_the_job_and_files_nothing(tmp_path):
    seat = _seat()
    v = _run(tmp_path, seat, {"people": {"names": [], "requester": "stranger@firm.example"}}).run()
    assert v.verdict == "held" and v.reason.startswith("requester_unresolved: ")
    assert seat.sent == []


def test_the_review_holds_a_tagged_tab_that_disagrees_with_its_data(tmp_path):
    r = _run(tmp_path, _seat(), {"people": {"names": [], "requester": ME}})
    assert r.run().verdict == "delivered"
    blob = r._out().read_bytes()
    want = r._tagged()
    assert review.findings(blob, r._json("final.json"), r._matters(), want) == []
    drifted = (want[0], want[1][:1])
    found = review.findings(blob, r._json("final.json"), r._matters(), drifted)
    assert [f for f in found if f.check == "tagged"]
    assert [f for f in review.findings(blob, r._json("final.json"), r._matters(), None) if "no people" in f.finding]


# ---- the deadlines report shares the rule ---------------------------------------------------------
def test_the_deadlines_report_with_a_people_scope_lists_their_matters(tmp_path):
    from test_litigation_deadlines import WORLD, make_run

    world = {m: (dict(v[0]), *v[1:]) for m, v in WORLD.items()}
    world[M2][0]["originatingStaffId"] = STAFF_A  # Beta's matter, brought in by Alpha
    r, _ = make_run(tmp_path, world, scope={"people": {"names": ["Alpha Example"], "requester": None}})
    v = r.run()
    assert v.verdict == "delivered", v.reason
    listing = json.loads((r.data / "pull" / "listing.json").read_text())
    assert sorted(s["id"] for s in listing["stubs"]) == sorted(WORLD)
    assert listing["people"] == [{"name": "Alpha Example", "ids": [STAFF_A]}]


# ---- the two-email sequence -----------------------------------------------------------------------
def test_the_follow_up_alone_is_not_a_court_only_request_the_first_email_is():
    """The columns came in the first email, "every litigated file I'm tagged on"
    in the second. The skill submits from the email that states the columns."""
    rx = [re.compile(r"interrogator")]
    first = "Complaint filed, sent out for service, each defendant served, not served, answered or no answer."
    assert court_only("request", first, rx) is True
    assert court_only("request", "Every litigated file I'm tagged on.", rx) is False
