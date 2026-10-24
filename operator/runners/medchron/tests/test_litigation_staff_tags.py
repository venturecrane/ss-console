"""Staff tags count as a role (2026-10-24).

A person's files are every matter they hold ANY staff role on, and Smokeball
tags staff on a matter apart from its roles. The tags are not on the /matters
LIST row; only ``GET /matters/{id}`` returns them. A requester had matters
reachable only through a Staff tag, so a people scope reads every open
matter's tags. Every name, address and number here is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from litigation_testkit import M1, M2, STAFF_A, STAFF_B, custom_tag, staff_tag
from medchron import arrivals
from medchron.litigation import book, people, review
from medchron.litigation.outcome import LitigationHold
from medchron.proof import holds
from test_litigation_people import ME, _run, _seat


class ApiError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


PEOPLE = SimpleNamespace(people=object(), scope_all=False, attorney_staff_ids=(), paralegal_staff_ids=())


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _tag_only_seat():
    """M1: Alpha responsible. M2: Beta responsible, no role for Alpha, but
    Alpha is tagged on it as Staff (and it carries a coloured Custom tag)."""
    seat = _seat()
    seat.matters[1].pop("personAssistingStaffs", None)
    seat.tags[M2] = [staff_tag(STAFF_A.upper()), custom_tag("Priority")]
    return seat


# ---- reading the tags --------------------------------------------------------------------------
def test_only_staff_tags_are_read_and_a_custom_tag_is_ignored():
    payload = {"id": M2, "tags": [staff_tag(STAFF_A.upper()), custom_tag("Priority"), staff_tag(STAFF_A)]}
    assert arrivals.tag_staff_ids(payload) == [STAFF_A]
    assert arrivals.tag_staff_ids({"id": M2, "tags": [custom_tag(STAFF_B)]}) == []
    assert arrivals.tag_staff_ids({"id": M2}) is None


def test_a_429_then_a_200_is_retried_and_succeeds():
    calls, slept = [], []

    def get(path):
        calls.append(path)
        if len(calls) == 1:
            raise ApiError(429)
        return {"id": M1, "tags": [staff_tag(STAFF_B)]}

    assert arrivals.staff_tags(get, [M1], sleep=slept.append) == {M1: [STAFF_B]}
    assert calls == [f"/matters/{M1}"] * 2 and slept == [arrivals.TAG_BACKOFF_SECONDS]


def test_a_4xx_is_not_retried_and_a_persistent_failure_raises_after_the_others_are_read():
    calls = []

    def get(path):
        calls.append(path)
        if path.endswith(M1):
            raise ApiError(503)
        if path.endswith(M2):
            raise ApiError(404)
        return {"id": "m3", "tags": []}

    with pytest.raises(arrivals.StaffTagsError, match=r"^2 of 3 matters"):
        arrivals.staff_tags(get, [M1, M2, "m3"], sleep=lambda s: None)
    assert calls.count(f"/matters/{M1}") == arrivals.TAG_ATTEMPTS
    assert calls.count(f"/matters/{M2}") == 1 and calls.count("/matters/m3") == 1


# ---- the hold, the cache, the non-people scope ------------------------------------------------------
def test_a_persistent_failure_holds_staff_tags_unlisted_and_pages_smd(tmp_path):
    def get(path):
        raise ApiError(500)

    with pytest.raises(LitigationHold) as exc:
        people.staff_tags(PEOPLE, get, [M1], tmp_path / "t.json")
    reason = str(exc.value)
    assert reason.startswith("staff_tags_unlisted: 1 of 1 matters")
    assert holds.classify("litigation", reason) == (holds.SMD_FINISHING, "gate_held")
    assert not (tmp_path / "t.json").exists()


def test_a_read_where_no_matter_carries_a_tags_list_holds(tmp_path):
    with pytest.raises(LitigationHold, match=r"^staff_tags_unlisted: none of the 2 matters"):
        people.staff_tags(PEOPLE, lambda path: {"id": "x"}, [M1, M2], tmp_path / "t.json")


def test_a_resume_reads_only_the_matters_not_yet_read(tmp_path):
    calls = []

    def get(path):
        calls.append(path)
        return {"tags": [staff_tag(STAFF_A)] if path.endswith(M2) else []}

    path = tmp_path / "t.json"
    assert people.staff_tags(PEOPLE, get, [M1, M2], path) == {M2: [STAFF_A]}
    assert people.staff_tags(PEOPLE, get, [M1, M2], path) == {M2: [STAFF_A]}
    assert len(calls) == 2
    assert people.staff_tags(PEOPLE, get, [M1, M2, "m3"], path) == {M2: [STAFF_A]}
    assert calls[2:] == ["/matters/m3"]


def test_a_scope_without_people_makes_no_matter_reads(tmp_path):
    seat = _tag_only_seat()
    r = _run(tmp_path, seat, {"attorney_staff_ids": [STAFF_A]})
    assert r.run().verdict == "delivered"
    assert seat.matter_reads == []
    assert people.staff_tags(SimpleNamespace(people=None), None, [M1], tmp_path / "t.json") == {}


# ---- the status list, end to end -----------------------------------------------------------------
def test_a_matter_i_am_only_tagged_on_is_in_scope_with_role_staff(tmp_path):
    seat = _tag_only_seat()
    r = _run(tmp_path, seat, {"people": {"names": [], "requester": ME}})
    v = r.run()
    assert v.verdict == "delivered", v.reason
    inv = json.loads((r.data / "inventory.json").read_text())
    assert sorted(m["id"] for m in inv["matters"]) == sorted([M1, M2])
    assert inv["people_scope"] == {"roles_only": 1, "with_staff_tags": 2}
    tab = review.read_sheets(r._out().read_bytes())[book.TAGGED]
    assert [(row[0], row[2]) for row in tab] == [("100001", "Responsible"), ("100002", "Staff")]
    assert sorted(seat.matter_reads) == sorted([M1, M2])
    kept = json.loads((r.data / people.TAGS_FILE).read_text())
    assert kept["tags"] == {M2: [STAFF_A]}


def test_without_the_tag_the_matter_is_not_in_scope(tmp_path):
    """The falsifier: the same seat with the tag removed lists only M1."""
    seat = _tag_only_seat()
    seat.tags[M2] = [custom_tag("Priority")]
    r = _run(tmp_path, seat, {"people": {"names": [], "requester": ME}})
    assert r.run().verdict == "delivered"
    assert [m["id"] for m in json.loads((r.data / "inventory.json").read_text())["matters"]] == [M1]


def test_a_tag_read_that_keeps_failing_holds_the_job_and_files_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(arrivals, "TAG_BACKOFF_SECONDS", 0.0)
    seat = _tag_only_seat()
    seat.tag_errors[M2] = [ApiError(502)] * arrivals.TAG_ATTEMPTS
    v = _run(tmp_path, seat, {"people": {"names": [], "requester": ME}}).run()
    assert v.verdict == "held" and v.reason.startswith("staff_tags_unlisted: "), v.reason
    assert seat.sent == []


# ---- the deadlines report ---------------------------------------------------------------------
def test_the_deadlines_people_scope_includes_a_tag_only_matter(tmp_path):
    from test_litigation_deadlines import WORLD, make_run

    world = {m: (dict(v[0]), *v[1:]) for m, v in WORLD.items()}
    world[M2][0]["originatingStaffId"] = None  # the list names originating staff (roles_unlisted passes)
    r, seat = make_run(tmp_path, world, scope={"people": {"names": ["Beta Example"], "requester": None}})
    seat.client.tags[M1] = [staff_tag(STAFF_B)]  # Alpha's matter; Beta is only tagged on it
    v = r.run()
    assert v.verdict == "delivered", v.reason
    listing = json.loads((r.data / "pull" / "listing.json").read_text())
    assert sorted(s["id"] for s in listing["stubs"]) == sorted([M1, M2])
    assert (r.data / "pull" / people.TAGS_FILE).is_file()
