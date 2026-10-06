"""Records orders: the client, prepare (READ) and place (COMMITMENT).

Fixtures follow the vendor's OpenAPI document read 2026-10-05 (``Order API
1.0.0``): ``OrderRecordResponse`` is ``{order_record_id, message, side_notes?}``,
``get_locations`` rows carry ``id, value, custodiantype, street, city, state,
postalcode``, ``OrderRecordExternalResponse`` is ``{matter_id, locations:[{id,
request_id, name, status}]}``. No live call is made: the firm's token is not in
hand, so the vendor is an ``httpx.MockTransport``.

The invariant every test below shares: the client's SSN never appears in any
tool return or any raised message.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest
from smokeball_connector import records_vendor, records_order_tools
from smokeball_connector.records_vendor import NOT_CONNECTED, RecordsVendorClient
from smokeball_connector.records_orders import OrderNotPlaced, order_ref, prepare
from smokeball_connector.records_patient import OrderRefused
from smokeball_connector.records_place import place

MATTER = "8d7c2a4e-1f3b-4c5d-9e6f-0a1b2c3d4e5f"
CLIENT = "c0ffee00-0000-4000-8000-000000000001"
SSN = "123-45-6789"
SSN_DIGITS = "123456789"
TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def _authored_fee(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The seat authors the firm's standing fee; every test reads it from here."""
    path = tmp_path / "customer.yaml"
    path.write_text("records_orders:\n  pre_approved_custodian_fee: 100\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return path


OID = "11111111-2222-4333-8444-555555555555"
PDF = b"%PDF-1.7\n" + b"x" * 64
EMAIL = "paralegal@firm.example"


def _contact(**person_over: Any) -> dict[str, Any]:
    person = {
        "firstName": "Pat",
        "lastName": "Example",
        "identificationNumber": SSN_DIGITS,
        "birthDate": "1985-06-15T00:00:00",
        "residentialAddress": {
            "addressLine1": "1 Main St",
            "city": "Springfield",
            "state": "CA",
            "zipCode": "95811",
        },
        "cell": {"areaCode": "916", "number": "555-1212"},
        "email": "pat@example.com",
    }
    person.update(person_over)
    return {"id": CLIENT, "person": person}


class FakeSmokeball:
    def __init__(self, contact: dict[str, Any] | None = None, files: list[dict[str, Any]] | None = None) -> None:
        self.contact = contact if contact is not None else _contact()
        self.files = (
            files
            if files is not None
            else [
                {
                    "id": "f-hipaa",
                    "name": "HIPAA Authorization - signed",
                    "fileExtension": ".pdf",
                    "dateCreated": "2026-09-30",
                },
                {"id": "f-letter", "name": "1st party letter", "fileExtension": ".docx", "dateCreated": "2026-10-05"},
            ]
        )
        self.blob = PDF

    def get(self, path: str, **params: Any) -> Any:
        if path == f"/matters/{MATTER}":
            return {"id": MATTER, "number": "900101", "clientIds": [CLIENT]}
        if path == f"/contacts/{CLIENT}":
            return self.contact
        if path == f"/matters/{MATTER}/documents/files":
            return {"value": self.files if params.get("Offset", 0) == 0 else []}
        raise AssertionError(f"unexpected Smokeball read {path}")

    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        assert (matter_id, file_id) == (MATTER, "f-hipaa")
        return {"name": "HIPAA Authorization - signed"}, self.blob


LOCATIONS = [
    {
        "id": 552211,
        "value": "Example Community Health Center",
        "custodiantype": "Facility",
        "street": "1 Main St",
        "city": "Springfield",
        "state": "CA",
        "postalcode": "95811",
    },
]


class Vendor:
    """A vendor stand-in that records every request and answers from a script."""

    def __init__(self, fail: dict[str, tuple[int, dict[str, Any]]] | None = None, locations: Any = None) -> None:
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail = fail or {}
        self.locations = LOCATIONS if locations is None else locations

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, request.content))
        assert request.headers["authorization"] == "Bearer tok-test"
        step = self._step(request.method, path)
        if step in self.fail:
            status, body = self.fail[step]
            return httpx.Response(status, json=body)
        if step == "locations":
            return httpx.Response(200, json=self.locations)
        if step == "external":
            return httpx.Response(
                200,
                json={
                    "matter_id": MATTER,
                    "locations": [
                        {
                            "id": "aaaaaaaa-0000-4000-8000-000000000000",
                            "request_id": "123456",
                            "name": "Example Community Health Center",
                            "status": "Not Started",
                        }
                    ],
                },
            )
        code = 201 if step == "new" else 200
        return httpx.Response(code, json={"order_record_id": OID, "message": f"{step} ok"})

    @staticmethod
    def _step(method: str, path: str) -> str:
        if path.endswith("/get_locations"):
            return "locations"
        if path.startswith("/api/v1/orders/external:"):
            return "external"
        if path == "/api/v1/orders/_new":
            return "new"
        if method == "PATCH":
            return "patch"
        return path.rsplit("/", 1)[-1]

    def client(self) -> RecordsVendorClient:
        return RecordsVendorClient(
            "tok-test", "https://vendor.example", http=httpx.Client(transport=httpx.MockTransport(self.handler))
        )

    def steps(self) -> list[str]:
        return [self._step(m, p) for m, p, _ in self.calls]


def _request(**over: Any) -> dict[str, Any]:
    req = {
        "matter_id": MATTER,
        "facilities": [{"name": "Example Community Health Center"}],
        "order_by_email": EMAIL,
        "years": 5,
    }
    req.update(over)
    return req


def _no_ssn(value: Any) -> None:
    text = json.dumps(value, default=str)
    assert SSN not in text and SSN_DIGITS not in text and "1985-06-15" not in text


def _ready_order(vendor: Vendor | None = None, sb: FakeSmokeball | None = None) -> dict[str, Any]:
    out = prepare(sb or FakeSmokeball(), (vendor or Vendor()).client(), _request(), TODAY)
    assert out["status"] == "ready", out
    return out["order"]


# ---- prepare ----------------------------------------------------------------
def test_prepare_builds_the_order_and_orders_nothing() -> None:
    vendor = Vendor()
    out = prepare(FakeSmokeball(), vendor.client(), _request(), TODAY)
    _no_ssn(out)
    order = out["order"]
    assert order["ssn_last4"] == "6789" and order["client_name"] == "Pat Example"
    assert order["matter_number"] == "900101" and order["hipaa_file_id"] == "f-hipaa"
    assert order["hipaa_file_name"] == "HIPAA Authorization - signed.pdf"
    assert order["pre_approved_custodian_fee"] == 100.0 and order["order_certificate"] == "no_request"
    assert order["locations"] == [
        {
            "custodian_id": "552211",
            "custodian_name": "Example Community Health Center",
            "custodian_address": "1 Main St, Springfield, CA 95811",
            "record_types": ["Medical", "Billing"],
            "service_start": "2021-10-05",
            "service_end": "2026-10-05",
        }
    ]
    assert order["order_ref"] == order_ref(order)
    assert vendor.steps() == ["locations"]  # a directory search, no order call


def test_prepare_asks_when_the_directory_holds_several_matches() -> None:
    two = LOCATIONS + [{**LOCATIONS[0], "id": 552212, "street": "1 Other Rd"}]
    out = prepare(FakeSmokeball(), Vendor(locations=two).client(), _request(), TODAY)
    assert out["status"] == "needs_choice" and "order" not in out
    assert [c["custodian_id"] for c in out["facilities"][0]["candidates"]] == ["552211", "552212"]
    picked = _request(facilities=[{"name": "Example Community Health Center", "custodian_id": "552212"}])
    again = prepare(FakeSmokeball(), Vendor(locations=two).client(), picked, TODAY)
    assert again["order"]["locations"][0]["custodian_id"] == "552212"


def test_prepare_never_accepts_a_custodian_id_the_directory_did_not_offer() -> None:
    picked = _request(facilities=[{"name": "Example Community Health Center", "custodian_id": "999"}])
    out = prepare(FakeSmokeball(), Vendor().client(), picked, TODAY)
    assert out["status"] == "needs_choice"


def test_prepare_a_new_custodian_only_when_she_says_so() -> None:
    none = Vendor(locations=[])
    out = prepare(FakeSmokeball(), none.client(), _request(facilities=[{"name": "Example Chiropractic"}]), TODAY)
    assert out["status"] == "needs_choice" and out["facilities"][0]["candidates"] == []
    new = _request(facilities=[{"name": "Example Chiropractic", "new_custodian": True, "address": "1 Main St"}])
    order = prepare(FakeSmokeball(), none.client(), new, TODAY)["order"]
    assert order["locations"][0]["custodian_id"] is None
    vendor = Vendor(locations=[])
    place(FakeSmokeball(), vendor.client(), order)
    patched = json.loads(vendor.calls[1][2])["locations"][0]
    assert patched["custodian_name"] == "Example Chiropractic (1 Main St)" and "custodian_id" not in patched


def test_prepare_reports_missing_client_facts_by_name_only() -> None:
    sb = FakeSmokeball(contact=_contact(identificationNumber="", birthDate=None))
    out = prepare(sb, Vendor().client(), _request(), TODAY)
    assert out["status"] == "missing_client_facts"
    assert out["client"]["missing"] == ["Social Security number (9 digits)", "date of birth"]


def test_prepare_asks_which_authorization_when_there_are_several() -> None:
    files = [
        {"id": "a1", "name": "HIPAA 2024", "fileExtension": ".pdf", "dateCreated": "2024-01-01"},
        {"id": "a2", "name": "HIPAA 2026", "fileExtension": ".pdf", "dateCreated": "2026-01-01"},
    ]
    out = prepare(FakeSmokeball(files=files), Vendor().client(), _request(), TODAY)
    assert out["status"] == "needs_choice" and [c["file_id"] for c in out["hipaa_candidates"]] == ["a2", "a1"]


def test_prepare_refuses_a_matter_number_and_a_future_range() -> None:
    with pytest.raises(OrderRefused):
        prepare(FakeSmokeball(), Vendor().client(), _request(matter_id="900101"), TODAY)
    with pytest.raises(OrderRefused):
        prepare(FakeSmokeball(), Vendor().client(), _request(years=None, service_start="2027-01-01"), TODAY)


def test_the_fee_is_the_firms_authored_figure_and_is_returned_as_text(
    _authored_fee: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The administrator's read-back names the fee in dollars, and the outbound
    fabrication gate admits that figure only when a read this turn carried it
    as text. A bare 100.0 seeded nothing and the read-back was held."""
    out = prepare(FakeSmokeball(), Vendor().client(), _request(), TODAY)
    assert out["order"]["pre_approved_custodian_fee"] == 100.0
    assert out["pre_approved_custodian_fee_shown"] == "$100.00"
    out = prepare(FakeSmokeball(), Vendor().client(), _request(pre_approved_custodian_fee=150), TODAY)
    assert out["pre_approved_custodian_fee_shown"] == "$150.00", "her own figure wins over the standing one"
    _authored_fee.write_text("records_orders: {}\n", encoding="utf-8")
    with pytest.raises(OrderRefused, match="not authored"):
        prepare(FakeSmokeball(), Vendor().client(), _request(), TODAY)


# ---- place ------------------------------------------------------------------
def test_place_runs_new_patch_upload_validate_finish_in_order() -> None:
    order = _ready_order()
    vendor = Vendor()
    out = place(FakeSmokeball(), vendor.client(), order)
    _no_ssn(out)
    assert out["id"] == OID and out["status"] == "placed"
    assert vendor.steps() == ["new", "patch", "upload", "validate", "finish"]
    new_body = json.loads(vendor.calls[0][2])
    # The identifiers go to the vendor, and only there.
    assert new_body["patient"]["ssn"] == SSN and new_body["patient"]["date_birth"] == "1985-06-15"
    assert new_body["patient"]["hipaa_type"] == "upload"
    assert new_body["matter"]["matter_type"] == "smokeball" and new_body["matter"]["matter_id"] == MATTER
    assert new_body["matter"]["order_type"] == "authorization" and new_body["matter"]["use_yipaa_form"] is False
    assert json.loads(vendor.calls[1][2]) == {
        "locations": [
            {
                "record_types": ["Medical", "Billing"],
                "service_start": "2021-10-05",
                "service_end": "2026-10-05",
                "custodian_id": "552211",
            }
        ]
    }
    upload = vendor.calls[2][2]
    assert b"completed_hipaa_form" in upload and PDF in upload


def test_a_validate_failure_finishes_nothing_and_never_echoes_the_ssn() -> None:
    order = _ready_order()
    vendor = Vendor(fail={"validate": (422, {"message": f"patient.ssn {SSN} is invalid"})})
    with pytest.raises(OrderNotPlaced) as err:
        place(FakeSmokeball(), vendor.client(), order)
    assert "finish" not in vendor.steps()
    assert OID in str(err.value) and "HTTP 422" in str(err.value)
    _no_ssn(str(err.value))


def test_a_4xx_on_create_is_surfaced_and_nothing_follows() -> None:
    vendor = Vendor(fail={"new": (401, {"message": "Unauthenticated."})})
    with pytest.raises(OrderNotPlaced, match="HTTP 401"):
        place(FakeSmokeball(), vendor.client(), _ready_order())
    assert vendor.steps() == ["new"]


def test_place_refuses_an_order_changed_after_prepare() -> None:
    order = _ready_order()
    order["locations"][0]["custodian_id"] = "777"
    vendor = Vendor()
    with pytest.raises(OrderRefused, match="changed after it was prepared"):
        place(FakeSmokeball(), vendor.client(), order)
    assert vendor.calls == []


def test_place_refuses_when_the_client_no_longer_matches() -> None:
    order = _ready_order()
    sb = FakeSmokeball(contact=_contact(identificationNumber="987654321"))
    with pytest.raises(OrderRefused, match="no longer matches") as err:
        place(sb, Vendor().client(), order)
    _no_ssn(str(err.value))


def test_place_refuses_an_authorization_that_is_not_a_pdf() -> None:
    sb = FakeSmokeball()
    sb.blob = b"PK\x03\x04 a docx"
    vendor = Vendor()
    with pytest.raises(OrderRefused, match="not a PDF"):
        place(sb, vendor.client(), _ready_order())
    assert vendor.calls == []


# ---- the tools --------------------------------------------------------------
def test_every_tool_says_not_connected_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(records_vendor.TOKEN_ENV, raising=False)
    monkeypatch.setenv(records_vendor.URL_ENV, "https://vendor.example")
    assert (
        records_order_tools.prepare_records_order(MATTER, [{"name": "x"}], EMAIL, years=1)["message"] == NOT_CONNECTED
    )
    assert records_order_tools.records_orders_for_matter(MATTER)["status"] == "not_connected"
    with pytest.raises(records_vendor.RecordsVendorNotConnected, match="not connected"):
        records_order_tools.place_records_order({})


def test_the_tools_never_return_the_ssn(monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = Vendor()
    monkeypatch.setenv(records_vendor.TOKEN_ENV, "tok-test")
    monkeypatch.setattr(records_order_tools, "client_from_env", vendor.client)
    monkeypatch.setattr(records_order_tools, "_sb", FakeSmokeball)
    monkeypatch.setattr(records_order_tools, "_today", lambda: TODAY)
    prepared = records_order_tools.prepare_records_order(
        MATTER, [{"name": "Example Community Health Center"}], EMAIL, years=5
    )
    placed = records_order_tools.place_records_order(prepared["order"])
    read = records_order_tools.records_orders_for_matter(MATTER)
    for value in (prepared, placed, read):
        _no_ssn(value)
    assert read["locations"][0]["request_id"] == "123456"


def test_orders_for_matter_reads_a_404_as_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = Vendor(fail={"external": (404, {"message": "Not Found"})})
    monkeypatch.setattr(records_order_tools, "client_from_env", vendor.client)
    assert records_order_tools.records_orders_for_matter(MATTER)["status"] == "none"


def test_a_token_without_an_https_url_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(records_vendor.TOKEN_ENV, "tok-test")
    monkeypatch.delenv(records_vendor.URL_ENV, raising=False)
    assert records_vendor.client_from_env() is None
    monkeypatch.setenv(records_vendor.URL_ENV, "http://vendor.example")
    assert records_vendor.client_from_env() is None


def test_the_order_carries_the_seats_vendor_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(records_vendor.NAME_ENV, "  Example   Records  ")
    assert records_vendor.vendor_name() == "Example Records"
    monkeypatch.delenv(records_vendor.NAME_ENV)
    assert records_vendor.vendor_name() == "the records vendor"
    out = prepare(FakeSmokeball(), Vendor().client(), _request(vendor_name="Example Records"), TODAY)
    assert out["order"]["vendor_name"] == "Example Records"


def test_scrub_blanks_ssn_shapes() -> None:
    assert records_vendor.scrub(f"bad ssn {SSN} and {SSN_DIGITS}") == "bad ssn [redacted] and [redacted]"
