"""Records orders built from a matter's filed gap audit (gap_orders.py, gap_locate.py).

Synthetic throughout: a gap audit docx in the demand job's rendered shape, a
fake Smokeball that serves it and the documents its rows cite, and a vendor
directory stand-in. Every name, address, id and number here is invented: none
is a real matter, provider, NPI (each fails the NPI check digit) or client."""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import pytest
from smokeball_connector import gap_locate, gap_orders
from smokeball_connector.records_orders import _CUSTODIAN_ID, service_range
from smokeball_connector.records_patient import OrderRefused

MATTER = "8d7c2a4e-1f3b-4c5d-9e6f-0a1b2c3d4e5f"
CLIENT = "c0ffee00-0000-4000-8000-000000000001"
TODAY = date(2026, 10, 7)
HEAD = [
    "Item",
    "Provider",
    "What's missing",
    "Where the file points to it",
    "Basis",
    "Suggested request type",
    "Priority",
]


def R(item, provider, missing, where, request, priority="Strengthens demand"):
    return [str(item), provider, missing, where, "Referenced in record", request, priority]


ROWS = {
    "A. Referral and order trail": [
        R(
            1,
            "Example Imaging Center (ordered by Dr. A)",
            "Missing: MRI report and films for 3/10/2026",
            "Example Imaging bill, p.1",
            "Radiology report, images and bill request",
            "Blocks demand",
        ),
        R(
            2,
            "Physical therapy (provider not named)",
            "Missing: PT notes",
            "ER record, p.2",
            "Ask client which PT clinic",
        ),
    ],
    "B. Provider-by-provider completeness": [
        R(
            3,
            "Northfield Spine Clinic (Dr. B)",
            "Missing prior records, same spine: injections 12/2/2023",
            "Northfield notes, p.6",
            "Prior records request, 5-year look-back",
            "Blocks demand",
        ),
        R(
            4,
            "Medicare",
            "Missing: conditional payment letter dated 1/2/2010",
            "CMS letter, p.1",
            "Conditional payment letter request",
        ),
        R(
            5,
            "Example Imaging Center",
            "Missing: lien and balance",
            "Example Imaging bill, p.1",
            "Lien and balance request",
            "Housekeeping",
        ),
        R(
            6,
            "Northfield Spine Clinic",
            "Visit 2/20/2026 billed with no itemization",
            "Northfield notes, p.1",
            "Itemized billing ledger with CPT detail",
        ),
        R(
            7,
            "Westside Medical Group",
            "Missing: records from 2/12/2026 (client DOB 3/1/2015 on the chart)",
            "Westside note, p.1",
            "Records and itemized billing request",
        ),
        R(8, "Unlisted Clinic", "Missing: records from 2/14/2026", "ER record, p.3", "Records request"),
        R(9, "Faraway Clinic", "Missing: records from 2/16/2026", "Faraway note, p.1", "Records request"),
        R(10, "Example Imaging Center", "Missing: billing", "Example Imaging bill, p.1", "Med pay ledger request"),
        R(11, "Hilltop Counseling Center", "Missing: session notes from 3/1/2026", "ER record, p.4", "Records request"),
        R(
            12,
            "Northfield Spine Clinic labs",
            "Missing: lab results 3/3/2026",
            "Northfield notes, p.7",
            "Lab results and bill request",
        ),
        R(13, "Example Clinic", "Missing: records", "ER record, p.5", "Records request; confirm the balance first"),
    ],
    "C. Billing-to-record reconciliation": [
        R(
            14,
            "Example Imaging Center",
            "Tab shows a charge with no date",
            "preflight (Medicals tab vs bills)",
            "Update Medicals tab",
            "Housekeeping",
        ),
    ],
    "D. Treatment timeline": [
        R(
            15,
            "Example Imaging Center",
            "3/10/2026 to 5/6/2026, 57 days",
            "Example Imaging bill, p.1",
            "None (timeline fact)",
            "Housekeeping",
        ),
    ],
}
LETTERHEADS = {
    "f-img": (
        "Example Imaging bill",
        "Example Imaging Center\n5 Elm St, Springfield, CA 95811\nPatient: Pat Example, 1 Main St, Hometown, CA 95001",
    ),
    "f-north": (
        "Northfield notes",
        "Northfield Spine Clinic\n9 Pine Rd, Shelbyville, CA 95822\nPatient address: Hometown, CA 95001",
    ),
    "f-west": (
        "Westside note",
        "Westside Medical Group\n200 Oak Ave, Springfield, CA 95811   (555) 010-0100\nPatient: Hometown, CA 95001\nHometown, CA 95001",
    ),
    "f-far": ("Faraway note", "Faraway Clinic\n77 Lake Rd, Lakeside, CA 95777"),
}


def _docx(paragraphs: list[str], tables: dict[str, list[list[str]]] | None = None) -> bytes:
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    for section, rows in (tables or {}).items():
        d.add_heading(section, 2)
        t = d.add_table(rows=1, cols=7)
        for i, h in enumerate(HEAD):
            t.rows[0].cells[i].text = h
        for r in rows:
            cells = t.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = v
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def gap_docx(rows: dict[str, list[list[str]]] | None = None) -> bytes:
    return _docx(["Records and Billing Gap Audit"], rows or ROWS)


class FakeSmokeball:
    def __init__(self, ssn: str = "123456789", rows: dict[str, list[list[str]]] | None = None) -> None:
        self.ssn, self.rows = ssn, rows
        self.downloads: list[str] = []
        self.files = [
            {
                "id": "f-gap",
                "name": "Gap Audit - 900101 - 10-07-26",
                "fileExtension": ".docx",
                "dateCreated": "2026-10-07T09:00",
            },
            {
                "id": "f-old",
                "name": "Gap Audit - 900101 - 10-01-26",
                "fileExtension": ".docx",
                "dateCreated": "2026-10-01T09:00",
            },
        ] + [
            {"id": k, "name": v[0], "fileExtension": ".docx", "dateCreated": "2026-03-01"}
            for k, v in LETTERHEADS.items()
        ]

    def get(self, path: str, **params: Any) -> Any:
        if path == f"/matters/{MATTER}/documents/files":
            return {"value": self.files if params.get("Offset", 0) == 0 else []}
        if path == f"/matters/{MATTER}":
            return {"id": MATTER, "number": "900101", "clientIds": [CLIENT]}
        if path == f"/contacts/{CLIENT}":
            return {
                "id": CLIENT,
                "person": {
                    "firstName": "Pat",
                    "lastName": "Example",
                    "identificationNumber": self.ssn,
                    "birthDate": "1985-06-15T00:00:00",
                    "residentialAddress": {
                        "addressLine1": "1 Main St",
                        "city": "Hometown",
                        "state": "CA",
                        "zipCode": "95001",
                    },
                    "email": "pat@example.com",
                },
            }
        raise LookupError(path)  # the Medicals tab is not served: no hints, never a crash

    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        self.downloads.append(file_id)
        if file_id == "f-gap":
            return {}, gap_docx(self.rows)
        return {}, _docx(LETTERHEADS[file_id][1].split("\n"))


def loc(i: str, name: str, street: str, city: str, zip_: str) -> dict[str, Any]:
    return {"id": i, "value": name, "street": street, "city": city, "state": "CA", "postalcode": zip_}


BOOK = {
    "Example Imaging Center": [loc("neo_101", "Example Imaging Center", "5 Elm St", "Springfield", "95811")],
    "Northfield Spine Clinic": [loc("npi_1000000001", "Northfield Spine Clinic", "9 Pine Rd", "Shelbyville", "95822")],
    "Westside Medical Group": [
        loc("neo_301", "Westside Medical Group", "200 Oak Ave", "Springfield", "95811"),
        loc("neo_302", "Westside Medical Group", "8 Bay Rd", "Capital City", "95899"),
        loc("neo_303", "Westside Medical Group - Pediatrics", "200 Oak Ave", "Springfield", "95811"),
        loc("neo_304", "Westside Medical Group", "1 Home Rd", "Hometown", "95001"),
    ],
    # one hit, and it is NOT where the file places the clinic (Lakeside)
    "Faraway Clinic": [loc("neo_401", "Faraway Clinic", "3 Far Rd", "Elsewhere", "95999")],
}


class Directory:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    def get_locations(self, term: str, zip_code: str | None = None) -> list[dict[str, Any]]:
        self.asked.append((term, zip_code))
        return [r for r in BOOK.get(term, []) if not zip_code or r["postalcode"] == zip_code]


@pytest.fixture(autouse=True)
def _seat(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("records_orders:\n  pre_approved_custodian_fee: 100\n  authorization: e_auth\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _build(sb: FakeSmokeball | None = None, d: Directory | None = None) -> dict[str, Any]:
    return gap_orders.build(
        sb or FakeSmokeball(), d or Directory(), MATTER, "paralegal@firm.example", TODAY, "the vendor"
    )


def _why() -> dict[str, str | None]:
    return {r["item"]: gap_orders.classify(r) for r in gap_orders.audit_rows(gap_docx())}


# ---- which rows are orderable -------------------------------------------------------------
def test_only_orderable_rows_addressed_to_a_medical_provider_are_kept() -> None:
    why = _why()
    assert [k for k, v in why.items() if v is None] == ["1", "3", "6", "7", "8", "9", "12"]
    assert "does not name" in why["2"]
    assert "payer" in why["4"]
    assert "lien" in why["5"]
    assert "timeline" in why["15"]


def test_a_payer_named_only_in_the_request_is_not_an_order() -> None:
    assert "payer" in _why()["10"]  # "Med pay ledger" passed before on the word "ledger"


@pytest.mark.parametrize(
    "provider",
    [
        "Health Net of Example",
        "Example Medicaid Plan",
        "MediCal",
        "Medi-Cal (DHCS)",
        "Example workers' comp carrier",
        "Example Claims Administrator",
        "Example Indemnity Co",
    ],
)
def test_payers_programs_and_carriers_are_not_custodians(provider: str) -> None:
    row = {"section": "B.", "provider": provider, "missing": "records", "where": "x", "request": "Records request"}
    assert "payer" in gap_orders.classify(row)


def test_medical_is_a_provider_word_not_medi_cal() -> None:
    row = {
        "section": "B.",
        "provider": "Example Medical Group",
        "missing": "records",
        "where": "x",
        "request": "Records request",
    }
    assert gap_orders.classify(row) is None


def test_a_confirmation_anywhere_in_the_request_holds_the_row() -> None:
    assert "confirmation" in _why()["13"]  # "Records request; confirm the balance first"


def test_counseling_is_excluded_on_purpose_with_its_reason() -> None:
    assert "own authorization" in _why()["11"]


# ---- dates ---------------------------------------------------------------------------------------
def _kept() -> list[dict[str, str]]:
    return [r for r in gap_orders.audit_rows(gap_docx()) if gap_orders.classify(r) is None]


def test_the_first_date_comes_from_orderable_rows_never_a_payer_row_or_a_birth_date() -> None:
    # the Medicare row's 1/2/2010 and the chart's DOB 3/1/2015 are not treatment
    assert gap_orders.first_date(_kept(), TODAY) == date(2026, 2, 12)
    assert gap_orders._dates("client DOB 3/1/2015, seen 2/12/2026", TODAY) == [date(2026, 2, 12)]
    assert gap_orders._dates("intake set for 10/10/26", TODAY) == []  # never a future date


def test_rows_group_by_custodian_with_types_and_dates() -> None:
    kept = _kept()
    groups = {g["name"]: g for g in gap_orders.group(kept, TODAY, gap_orders.first_date(kept, TODAY))}
    north = groups["Northfield Spine Clinic"]
    assert north["items"] == [3, 6] and set(north["types"]) == {"Medical", "Billing"}
    assert north["service_start"] == "2021-02-12"  # 5 years before the first treatment date
    imaging = groups["Example Imaging Center"]
    assert set(imaging["types"]) == {"Radiology Record", "Radiology Image", "Billing"}
    assert imaging["service_start"] == "2026-03-10" and imaging["service_end"] == "2026-10-07"


def test_a_long_look_back_is_held_to_max_years() -> None:
    row = {
        "item": "1",
        "section": "B.",
        "provider": "Example Clinic",
        "missing": "prior records from 2/1/2026",
        "where": "x",
        "request": "Prior records, 25-year look-back",
        "priority": "Blocks demand",
    }
    (g,) = gap_orders.group([row], TODAY, date(2026, 2, 1))
    assert g["service_start"] == "2006-10-07" and "held to 20 years" in g["basis"]


def test_service_range_clamps_any_start_to_max_years() -> None:
    assert service_range({"service_start": "1990-01-01"}, {}, TODAY) == ("2006-10-07", "2026-10-07")
    with pytest.raises(OrderRefused):
        service_range({"service_start": "1990-01-01", "service_end": "1995-01-01"}, {}, TODAY)


# ---- matching ----------------------------------------------------------------------------------
def test_every_distinctive_word_must_be_in_the_candidates_name() -> None:
    assert gap_locate.names_match("Saint Example Hospital", "Saint Example Hospital - Main")
    assert not gap_locate.names_match("Saint Example Hospital", "Saint Other Hospital")  # first word alone
    assert not gap_locate.names_match("Hometown Family Healthcare", "Oakdale Healthcare Center")


def test_a_department_inside_counts_only_after_a_facility_word_never_a_street() -> None:
    inside = {"name": "Example Radiology Group", "address": "6501 Main Ave Saint Example Hospital, Town, CA 95811"}
    street = {"name": "Example Imaging", "address": "12 Saint Example St, Town, CA 95811"}
    assert gap_locate.department_inside("Saint Example Medical Center", inside)
    assert not gap_locate.department_inside("Saint Example Medical Center", street)


def test_the_medicals_hint_needs_every_distinctive_word() -> None:
    contacts = [{"name": "Saint Other Hospital", "street": "1 A St", "city": "Town", "zip": "95811"}]
    assert gap_orders._hint("Saint Example Hospital", contacts) is None  # "hospital" alone is not a match
    assert gap_orders._hint("Saint Other", contacts) == contacts[0]


def test_directory_duplicates_at_one_address_are_one_place() -> None:
    a = {"custodian_id": "npi_1000000000", "name": "Example Lab LLC", "address": "4 Way, Town, CA 95811"}
    b = {"custodian_id": "neo_2", "name": "Example Lab", "address": "4 Way, Town, CA 95811"}
    assert gap_locate.same_place([a, b]) == [b]


def test_the_vendors_custodian_id_shapes_are_accepted_and_nothing_else() -> None:
    for good in ("552211", "neo_77", "npi_1000000001", "00000000-0000-4000-8000-000000000001"):
        assert _CUSTODIAN_ID.fullmatch(good)
    for bad in ("", "../x", "neo 1", "a" * 80, "neo_1\n"):
        assert not _CUSTODIAN_ID.fullmatch(bad)


# ---- the build ------------------------------------------------------------------------------------
def test_the_file_settles_locations_and_the_order_is_prepared() -> None:
    sb = FakeSmokeball()
    out = _build(sb)
    order = out["orders"][0]["order"]
    # Westside: four directory entries; the cited note's letterhead (200 Oak Ave,
    # 95811) settles it, the Pediatrics department is not the custodian, and
    # the client's own Hometown address on the same note is never evidence
    assert [x["custodian_id"] for x in order["locations"]] == ["neo_101", "npi_1000000001", "neo_301"]
    assert {"f-img", "f-north", "f-west"} <= set(sb.downloads)
    assert order["authorization"] == "e_auth"


def test_one_directory_hit_where_the_file_does_not_place_the_provider_is_not_matched() -> None:
    out = _build()
    faraway = next(p for p in out["providers"] if p["name"] == "Faraway Clinic")
    assert faraway["outcome"] == "ambiguous"
    assert all(loc["custodian_id"] != "neo_401" for loc in out["orders"][0]["order"]["locations"])


def test_two_audit_providers_on_one_location_are_ordered_once_and_said_so() -> None:
    # a second audit provider name for the same clinic resolves to the same entry
    rows = {k: [r for r in v if r[0] != "12"] for k, v in ROWS.items()}
    rows["B. Provider-by-provider completeness"].append(
        R(
            16,
            "Northfield Spine",
            "Missing: notes 3/3/2026",
            "Northfield notes, p.7",
            "Records request",
        )
    )
    BOOK["Northfield Spine"] = BOOK["Northfield Spine Clinic"]
    try:
        out = _build(FakeSmokeball(rows=rows))
    finally:
        BOOK.pop("Northfield Spine")
    ids = [x["custodian_id"] for x in out["orders"][0]["order"]["locations"]]
    assert ids.count("npi_1000000001") == 1
    assert out["merged"] == [
        "Northfield Spine Clinic and Northfield Spine are one location at the vendor "
        "(Northfield Spine Clinic); ordered once"
    ]


def test_one_matter_gets_one_order_so_one_act_line_and_one_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gap_orders, "MAX_LOCATIONS", 2)
    out = _build()
    assert len(out["orders"]) == 1
    ids = [x["custodian_id"] for x in out["orders"][0]["order"]["locations"]]
    assert ids == ["neo_101", "npi_1000000001"]  # the rows that block the demand go first
    assert out["after_this_order"] == ["Westside Medical Group"]


def test_what_the_file_cannot_settle_is_one_question_never_a_list() -> None:
    out = _build()
    q = out["question"]
    assert q.startswith("One thing before these are ordered") and "Unlisted Clinic" in q and "Faraway Clinic" in q


def test_a_missing_client_fact_still_shows_what_would_be_ordered() -> None:
    out = _build(FakeSmokeball(ssn=""))
    assert out["orders"][0]["status"] == "missing_client_facts"
    assert {w["custodian"] for w in out["would_order"]} == {
        "Example Imaging Center",
        "Northfield Spine Clinic",
        "Westside Medical Group",
    }


def test_two_providers_on_one_location_merge_types_and_range() -> None:
    f = [
        {
            "custodian_id": "neo_1",
            "record_types": ["Medical"],
            "service_start": "2026-01-01",
            "service_end": "2026-10-07",
            "providers": ["A"],
            "rank": 1,
        },
        {
            "custodian_id": "neo_1",
            "record_types": ["Billing"],
            "service_start": "2021-01-01",
            "service_end": "2026-10-07",
            "providers": ["B"],
            "rank": 0,
        },
    ]
    (m,) = gap_orders.merge_same_custodian(f)
    assert m["record_types"] == ["Medical", "Billing"] and m["service_start"] == "2021-01-01"
    assert m["providers"] == ["A", "B"] and m["rank"] == 0


def test_no_filed_gap_audit_is_said_plainly() -> None:
    sb = FakeSmokeball()
    sb.files = [f for f in sb.files if not f["name"].startswith("Gap Audit")]
    assert _build(sb)["status"] == "refused"


def test_a_lab_matches_only_a_candidate_whose_name_says_lab() -> None:
    assert not gap_locate.lab_ok("Example Medical Group labs", "Example Medical Group")
    assert gap_locate.lab_ok("Example Medical Group labs", "Example Medical Group Laboratory")
    assert gap_locate.lab_ok("Example Pathology", "Example Pathology Associates")
    assert gap_locate.lab_ok("Example Medical Group", "Example Medical Group")


def test_the_labs_row_is_asked_about_when_the_directory_has_only_the_clinic() -> None:
    out = _build()
    labs = next(p for p in out["providers"] if p["name"] == "Northfield Spine Clinic labs")
    assert labs["outcome"] != "matched" and "Northfield Spine Clinic labs" in out["question"]
    assert out["merged"] == []  # the clinic's own entry is never taken for its lab
