"""The obvious directory listing, picked; the rest asked (2026-10-29).

Fixtures mirror, with invented names and addresses, the SHAPE of the vendor
directory's answer for a real three-facility request: an ambulance company
listed in many cities, a hospital listed five ways at one address beside its
emergency room, surgery center, pathology department and physicians plaza, and
a VA campus listed four ways. Before this, each facility came back as a "which
one?" question, and the requester's own answer naming the hospital and its
street was asked about again.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from smokeball_connector.facility_pick import narrow, places
from smokeball_connector.records_orders import prepare

from test_records_orders import _authored_fee, EMAIL, MATTER, SSN, SSN_DIGITS, FakeSmokeball  # noqa: F401 - imports the autouse fixture that authors the firm's custodian fee for every prepare here


def _row(id_: str, value: str, street: str = "", city: str = "", state: str = "CA", zip_: str = "") -> dict[str, Any]:
    return {"id": id_, "value": value, "street": street, "city": city, "state": state, "postalcode": zip_}


HOSPITAL = [
    _row("neo_7750", "Saint Example Medical Center - Emergency Room", "400 Oak Ave", "Riverbend", zip_="93608"),
    _row("neo_7752", "Saint Example Surgery Center", "460 Oak Ave", "Riverbend", zip_="93608"),
    _row(
        "neo_6551",
        "Pathology Department - Saint Example Medical Center - Riverbend",
        "400 Oak Ave",
        "Riverbend",
        zip_="93608",
    ),
    _row(
        "neo_4045", "Example Medical Group - Saint Example Physicians Plaza", "455 Oak Ave", "Riverbend", zip_="93608"
    ),
    _row("neo_5121", "Example Health - Saint Example Medical Center", "400 Oak Ave", "Riverbend", zip_="93608"),
    _row("058165a1", "Saint Example Medical Center", "", "Riverbend", zip_="93608"),
    _row("704c428f", "Example Saint Hospital", "", "Riverbend", zip_="93608"),
    _row("095091ea", "Saint Example Medical Center", "", "", zip_=""),
    _row("npi_1023", "SAINT EXAMPLE MEDICAL CENTER", "400 OAK AVE", "RIVERBEND"),
]
VA = [
    _row("neo_6528", "Lakeside VA Medical Center", "200 Hospital Way", "Northfield", zip_="93655"),
    _row("neo_5261", "Northfield VA", "200 Hospital Way", "Northfield", zip_="93655"),
    _row("417c93dc", "Northfield VA", "", "Northfield", zip_="93655"),
    _row("npi_1437", "VA NORTHFIELD", "200 HOSPITAL WAY", "NORTHFIELD"),
]
AMBULANCE = [
    _row("neo_9291", "Example Ambulance"),
    _row("neo_1088", "Example Ambulance - Hilltop County", "1 Vale Way", "Hilltop", zip_="95112"),
    _row("neo_2877", "Example Ambulance - Bayview", "2 Bay Ln", "Bayview", zip_="94520"),
    _row("neo_4060", "Example Ambulance - Harbor", "3 Pier Ct", "Harbor", "CT", "93558"),
    _row("neo_1586", "Example Ambulance - Port City", "4 Dock St.", "Port City", zip_="94107"),
    _row("neo_4219", "Example Ambulance - Lakeside", "5 Fee Dr", "Lakeside", zip_="93815"),
    _row("neo_1664", "Example Ambulance - TX", "6 Plain Ave", "Midland", "TX", "76018"),
]
DIRECTORY = {"Example Ambulance": AMBULANCE, "Saint Example": HOSPITAL, "VA Northfield": VA}
TODAY = date(2026, 10, 29)


class Directory:
    """The vendor's get_locations, answered per search term."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.searches: list[str] = []

    def get_locations(self, term: str, zip_code: str | None = None) -> list[dict[str, Any]]:
        self.searches.append(term)
        return list(self.rows.get(term, []))


def _cands(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from smokeball_connector.records_orders import _candidate

    return [c for c in (_candidate(r) for r in rows) if c is not None]


def _request(*names: str, **over: Any) -> dict[str, Any]:
    req = {
        "matter_id": MATTER,
        "facilities": [{"name": n} for n in names],
        "order_by_email": EMAIL,
        "service_start": "2021-10-29",
    }
    req.update(over)
    return req


# ---- one place listed several times ----------------------------------------
def test_one_hospital_listed_five_ways_is_one_place_and_its_departments_are_not_it() -> None:
    groups = places("Saint Example", _cands(HOSPITAL))
    assert len(groups) == 1
    assert groups[0][0]["custodian_id"] == "neo_5121"  # the vendor's curated entry represents it
    ids = {c["custodian_id"] for c in groups[0]}
    assert not ids & {"neo_7750", "neo_7752", "neo_6551", "neo_4045"}  # ER, surgery, pathology, physicians


def test_four_va_listings_at_one_address_need_no_question() -> None:
    out = narrow("VA Northfield", _cands(VA), [])
    assert out["pick"]["custodian_id"] == "neo_5261"
    assert out["pick"]["address"].startswith("200 Hospital Way")


def test_a_separately_named_group_at_the_hospitals_address_is_not_merged_into_it() -> None:
    group = _row("neo_9001", "Saint Example Hospitalist Group", "400 Oak Ave", "Riverbend", zip_="93608")
    found = _cands(HOSPITAL + [group])
    assert all("neo_9001" not in {c["custodian_id"] for c in g} for g in places("Saint Example", found))
    named = places("Saint Example Hospitalist Group", found)
    assert [g[0]["custodian_id"] for g in named] == ["neo_9001"]


# ---- near the order's other facilities ----------------------------------------
def _anchors() -> list[dict[str, Any]]:
    return [
        {"facility": "Saint Example", "address": "400 Oak Ave, Riverbend, CA 93608"},
        {"facility": "VA Northfield", "address": "200 Hospital Way, Northfield, CA 93655"},
    ]


def test_the_one_ambulance_listing_near_the_orders_other_facilities_is_the_pick() -> None:
    out = narrow("Example Ambulance", _cands(AMBULANCE), _anchors())
    assert out["pick"]["custodian_id"] == "neo_4219"
    assert "Saint Example" in out["why"] and "VA Northfield" in out["why"]


def test_an_ambulance_alone_with_nothing_to_place_it_is_a_question_not_a_guess() -> None:
    out = narrow("Example Ambulance", _cands(AMBULANCE), [])
    assert out["pick"] is None and len(out["shortlist"]) > 1


def test_two_ambulance_listings_near_the_order_is_a_question() -> None:
    ridge = _row("neo_7001", "Example Ambulance - Ridge", "7 Vernon St", "Ridge", zip_="93661")
    out = narrow("Example Ambulance", _cands(AMBULANCE + [ridge]), _anchors())
    assert out["pick"] is None
    assert {c["custodian_id"] for c in out["shortlist"][:2]} == {"neo_4219", "neo_7001"}


# ---- prepare, end to end on the three-facility shape ----------------------------
def test_prepare_three_facilities_is_ready_with_three_picks_and_orders_nothing() -> None:
    directory = Directory(DIRECTORY)
    out = prepare(FakeSmokeball(), directory, _request("Example Ambulance", "Saint Example", "VA Northfield"), TODAY)
    assert out["status"] == "ready", out
    assert [loc["custodian_id"] for loc in out["order"]["locations"]] == ["neo_4219", "neo_5121", "neo_5261"]
    assert [p["facility"] for p in out["picked"]] == ["Example Ambulance", "Saint Example", "VA Northfield"]
    assert out["picked_ids"] == {
        "Example Ambulance": "neo_4219",
        "Saint Example": "neo_5121",
        "VA Northfield": "neo_5261",
    }
    # each facility searched once: the second pass reuses the first answer
    assert sorted(directory.searches) == sorted(DIRECTORY)


def test_picked_lines_carry_no_directory_id_and_nothing_of_the_clients() -> None:
    out = prepare(
        FakeSmokeball(), Directory(DIRECTORY), _request("Example Ambulance", "Saint Example", "VA Northfield"), TODAY
    )
    text = json.dumps(out["picked"])
    assert "neo_" not in text and "npi_" not in text
    assert SSN not in text and SSN_DIGITS not in text
    # the fake client lives at 1 Main St, Springfield 95811: her home never anchors or explains a pick
    assert "Springfield" not in text and "95811" not in text and "Main St" not in text


def test_a_pick_she_read_is_the_pick_ordered_when_she_answers_another_question() -> None:
    unknown = {
        "Example Clinic": [
            _row("1", "Example Clinic", "1 A St", "Eureka", zip_="95501"),
            _row("2", "Example Clinic", "2 B St", "Reno", "NV", "89501"),
        ]
    }
    rows = {**DIRECTORY, **unknown}
    first = prepare(FakeSmokeball(), Directory(rows), _request("Saint Example", "Example Clinic"), TODAY)
    assert first["status"] == "needs_choice"
    assert first["picked_ids"] == {"Saint Example": "neo_5121"}
    assert [q["facility"] for q in first["facilities"]] == ["Example Clinic"]
    # she answers the clinic; the Operator passes the pick back as its custodian_id
    again = _request("Saint Example", "Example Clinic")
    again["facilities"] = [
        {"name": "Saint Example", "custodian_id": first["picked_ids"]["Saint Example"]},
        {"name": "Example Clinic", "custodian_id": "2"},
    ]
    second = prepare(FakeSmokeball(), Directory(rows), again, TODAY)
    assert second["status"] == "ready"
    assert [loc["custodian_id"] for loc in second["order"]["locations"]] == ["neo_5121", "2"]


def test_her_own_choice_always_beats_a_pick() -> None:
    req = _request("Saint Example")
    req["facilities"] = [{"name": "Saint Example", "custodian_id": "neo_7750"}]  # she wants the ER entry
    out = prepare(FakeSmokeball(), Directory(DIRECTORY), req, TODAY)
    assert out["status"] == "ready" and out["picked"] == []
    assert out["order"]["locations"][0]["custodian_id"] == "neo_7750"


def test_her_answer_naming_the_hospital_and_its_street_settles_it() -> None:
    """Her answer named the hospital and its street, and was asked about again
    because the ER, pathology and wound care entries share the address. The
    departments are not what she named."""
    req = _request("Saint Example Medical Center")
    req["facilities"] = [{"name": "Saint Example Medical Center", "zip": "93608"}]
    out = prepare(FakeSmokeball(), Directory({"Saint Example Medical Center": HOSPITAL}), req, TODAY)
    assert out["status"] == "ready", out
    assert out["order"]["locations"][0]["custodian_id"] == "neo_5121"
