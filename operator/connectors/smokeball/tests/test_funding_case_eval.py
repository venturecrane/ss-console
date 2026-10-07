"""The funding case evaluation prefill (2026-10-06).

What these defend, each written so removing the line it defends fails it:

* it fills what the record holds (attorney, firm, paralegal, the client's
  contact block, date of loss, both carriers, providers) and ticks "MVA" only
  for a motor vehicle matter type;
* a home phone goes only in the home box and a cell only in the cell box;
* it never writes the Social Security number (even with an identification
  number on the contact), a medical total, an opinion box or a signature;
* ``filled`` is read back off the output: a box the record had no value for is
  never reported filled;
* a library form missing any box this tool fills is refused and nothing is
  filed (a swapped or revised form);
* the result never carries a value.

The form here is SYNTHETIC: built in the test with the real form's box names
(checked against the firm's own copy before this shipped), plus decoy boxes
that must stay empty. The funder's form itself is not committed. All facts are
synthetic.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, StreamObject, TextStringObject
from smokeball_connector import form_letters as fl
from smokeball_connector import funding_case_eval as fce
from smokeball_connector.library import NotResolved, ResolvedTemplate

DECOYS = (
    "Social Security Number",
    "Amount requested",
    "Estimated Medical Expenses to date",
    "Most recent date of Medical Care",
    "Description of Accident",
    "Occupation",
)
DECOY_CHECKS = ("liability clear YES", "Client have health insurance YES")

MATTER, CLIENT, OTHER = "m-1", "c-1", "c-2"
INS_1P, INS_3P, ADJ_3P = "c-i1", "c-i3", "c-a3"
STAFF, ASSIST = "s-1", "s-2"


def _form(text_names: list[str], check_names: list[str]) -> bytes:
    """A one-page AcroForm with these text boxes and checkboxes (on state /Yes)."""
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    fields = ArrayObject()
    annots = ArrayObject()

    def add(field: DictionaryObject) -> None:
        field.update(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(
                    [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                ),
                NameObject("/F"): NumberObject(4),
            }
        )
        ref = writer._add_object(field)
        fields.append(ref)
        annots.append(ref)

    for name in text_names:
        add(DictionaryObject({NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(name)}))
    for name in check_names:
        on, off = writer._add_object(StreamObject()), writer._add_object(StreamObject())
        ap = DictionaryObject({NameObject("/N"): DictionaryObject({NameObject("/Yes"): on, NameObject("/Off"): off})})
        add(
            DictionaryObject(
                {
                    NameObject("/FT"): NameObject("/Btn"),
                    NameObject("/T"): TextStringObject(name),
                    NameObject("/V"): NameObject("/Off"),
                    NameObject("/AS"): NameObject("/Off"),
                    NameObject("/AP"): ap,
                }
            )
        )
    page[NameObject("/Annots")] = annots
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): fields})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


FORM = _form([*fce.BOXES, *DECOYS], [*fce.CHECKS, *DECOY_CHECKS])


class _Record:
    def __init__(self, *, matter_type: str = "Motor Vehicle Accident - Plaintiff", home: bool = False) -> None:
        self.matter_type = matter_type
        self.home = home
        self.uploads: list[tuple[str, str, bytes]] = []

    def get(self, path: str, **_params: Any) -> Any:
        if path == f"/matters/{MATTER}":
            return {
                "id": MATTER,
                "clientIds": [CLIENT],
                "matterTypeId": "mt-1",
                "personResponsibleStaffId": STAFF,
                "personAssistingStaffId": ASSIST,
            }
        if path == "/mattertypes/mt-1":
            return {"id": "mt-1", "name": self.matter_type}
        if path == f"/matters/{MATTER}/roles":
            return {
                "roles": [
                    {
                        "name": "Plaintiff",
                        "isClient": True,
                        "contactId": CLIENT,
                        "relationships": [{"id": "r-i1", "name": "Insurer", "contactId": INS_1P}],
                    },
                    {
                        "name": "Defendant",
                        "isOtherSide": True,
                        "contactId": OTHER,
                        "relationships": [
                            {"id": "r-i3", "name": "Insurer", "contactId": INS_3P},
                            {"id": "r-a3", "name": "Adjuster", "contactId": ADJ_3P},
                        ],
                    },
                ]
            }
        if path == f"/matters/{MATTER}/layouts":
            return {
                "value": [
                    {"id": "lay"},
                    {"id": "pi", "layoutDesign": {"id": "PersonalInjurySettlementDetailsItem-1"}, "parentIndex": 0},
                ]
            }
        if path == f"/matters/{MATTER}/layouts/lay":
            values = {
                "Matter/CaseDetails/AccidentDetails/AccidentDate": "2026-03-04",
                "Matter/Defendants/InsurancePolicy/Claims/Number": "CLM-3",
                "Matter/Plaintiffs/InsurancePolicy/Claims/Number": "CLM-1",
            }
            return {"values": [{"key": k, "value": v} for k, v in values.items()]}
        if path == f"/matters/{MATTER}/layouts/pi":
            values = {
                "Providers[0]/Provider/DisplayName": "Example Urgent Care",
                "Providers[1]/Provider/DisplayName": "Example Imaging",
                "Providers[1]/Invoices[0]/InitialInvoiceAmount": "900.00",
            }
            return {"values": [{"key": k, "value": v} for k, v in values.items()]}
        if path == f"/contacts/{CLIENT}":
            person: dict[str, Any] = {
                "firstName": "Dana",
                "lastName": "Example",
                "birthDate": "1990-02-03T00:00:00",
                "identificationNumber": "000-00-0000",
                "cell": {"areaCode": "916", "number": "555-0199"},
                "residentialAddress": {
                    "addressLine1": "1 Test Way",
                    "city": "Exampleton",
                    "state": "CA",
                    "zipCode": "95000",
                },
            }
            if self.home:
                person["phone"] = {"areaCode": "916", "number": "555-0100"}
            return {"id": CLIENT, "person": person}
        if path == f"/contacts/{OTHER}":
            return {"id": OTHER, "person": {"firstName": "Robin", "lastName": "Other"}}
        if path == f"/contacts/{INS_1P}":
            return {"id": INS_1P, "company": {"name": "Own Side Insurance"}}
        if path == f"/contacts/{INS_3P}":
            return {
                "id": INS_3P,
                "company": {
                    "name": "Other Side Mutual",
                    "phone": {"areaCode": "800", "number": "555-0300"},
                    "mailingAddress": {
                        "addressLine1": "PO Box 9",
                        "city": "Claimsville",
                        "state": "TX",
                        "zipCode": "75000",
                    },
                },
            }
        if path == f"/contacts/{ADJ_3P}":
            return {"id": ADJ_3P, "person": {"firstName": "Pat", "lastName": "Adjuster"}}
        if path == f"/staff/{STAFF}":
            return {"id": STAFF, "firstName": "Sam", "lastName": "Signer", "email": "sam@firm.example"}
        if path == f"/staff/{ASSIST}":
            return {"id": ASSIST, "firstName": "Alex", "lastName": "Barnes", "email": "alex@firm.example"}
        if path == f"/matters/{MATTER}/documents/files":
            return {"value": []}
        if path.startswith(f"/matters/{MATTER}/documents/files/"):
            return {"id": path.rsplit("/", 1)[1]}
        raise AssertionError(f"unscripted GET {path}")

    def add_file(self, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None) -> dict[str, Any]:
        self.uploads.append((matter_id, file_name, data))
        return {"fileId": "f-new"}


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text(
        "firm_identity:\n  name: 'Example Law'\n  street: '2 Court St'\n"
        "  city_state_zip: 'Exampleton, CA 95000'\n  phone: '(916) 555-0000'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return path


def _values(blob: bytes) -> dict[str, str]:
    return {k: str(v.get("/V") or "") for k, v in (PdfReader(io.BytesIO(blob)).get_fields() or {}).items()}


def _render(monkeypatch: pytest.MonkeyPatch, rec: _Record, form: bytes = FORM) -> Any:
    monkeypatch.setattr(fce, "_client", lambda: rec)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    monkeypatch.setattr(
        fce,
        "resolve_template",
        lambda *_a, **_k: ResolvedTemplate(
            bytes=form, name="Form - Funding Case Eval", file_id="t", matter_id="lib", folder_id="fold"
        ),
    )
    return fce.render_funding_case_eval(MATTER)


def test_fills_what_the_record_holds(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    out = _render(monkeypatch, rec)
    assert out["status"] == "filed"
    v = _values(rec.uploads[0][2])
    assert v["Attorney Name"] == "Sam Signer" and v["Attorney Email"] == "sam@firm.example"
    assert v["Firm"] == "Example Law" and v["Attorney Phone"] == "(916) 555-0000"
    assert v["Paralegal Name"] == "Alex Barnes"
    assert v["Client Name"] == "Dana Example" and v["Client City, State, Zip"] == "Exampleton, CA 95000"
    assert v["Client's Date of Birth"] == "02/03/1990" and v["Date of Loss"]
    assert v["Liability Carrier"] == "Other Side Mutual" and v["Liability Claim #"] == "CLM-3"
    assert v["Liability Adjuster"] == "Pat Adjuster" and v["Liability Phone #"] == "(800) 555-0300"
    assert v["Liability Address"] == "PO Box 9" and v["Liability City, State, Zip"] == "Claimsville, TX 75000"
    assert v["First Party Insurance Carrier"] == "Own Side Insurance" and v["First Party Insurance Claim #"] == "CLM-1"
    assert v["Provider/Facility 1"] == "Example Urgent Care" and v["Provider/Facility 2"] == "Example Imaging"
    assert v["MVA"] == "/Yes"


def test_phones_go_only_in_their_own_box(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    _render(monkeypatch, rec)
    v = _values(rec.uploads[0][2])
    assert v["Client Cell/Pager"] == "(916) 555-0199" and v["Client Phone (home)"] == ""
    rec2 = _Record(home=True)
    _render(monkeypatch, rec2)
    assert _values(rec2.uploads[0][2])["Client Phone (home)"] == "(916) 555-0100"


def test_never_ssn_totals_opinions_or_narrative(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    _render(monkeypatch, rec)
    v = _values(rec.uploads[0][2])
    for box in DECOYS:
        assert v[box] == "", box
    for box in DECOY_CHECKS:
        assert v[box] in ("", "/Off"), box
    assert "000-00-0000" not in json.dumps(v)


def test_mva_only_for_a_motor_vehicle_matter(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record(matter_type="Premises Liability - Plaintiff")
    out = _render(monkeypatch, rec)
    assert _values(rec.uploads[0][2])["MVA"] in ("", "/Off")
    assert "case type: MVA" not in out["filled"]


def test_filled_is_read_back_and_carries_no_value(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    out = _render(monkeypatch, rec)
    assert "client home phone" not in out["filled"] and "client home phone" in out["not_in_file"]
    assert "client cell phone" in out["filled"] and "case type: MVA" in out["filled"]
    blob = json.dumps(out)
    for secret in ("Dana", "555-0199", "1990", "CLM-3", "Other Side Mutual", "sam@firm.example"):
        assert secret not in blob, secret


def test_a_form_missing_a_box_is_refused_and_nothing_filed(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    short = _form([b for b in fce.BOXES if b != "Liability Claim #"], list(fce.CHECKS))
    out = _render(monkeypatch, rec, short)
    assert out["status"] == "refused" and "does not match" in out["reason"]
    assert rec.uploads == []


def test_an_unresolved_form_files_nothing(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    monkeypatch.setattr(fce, "_client", lambda: rec)
    monkeypatch.setattr(fce, "resolve_template", lambda *_a, **_k: NotResolved("not in the library"))
    out = fce.render_funding_case_eval(MATTER)
    assert out["status"] == "refused" and rec.uploads == []


def test_seat_today_is_the_request_date(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Record()
    monkeypatch.setattr(fce.facts, "seat_today", lambda *_a: date(2026, 10, 6))
    _render(monkeypatch, rec)
    assert _values(rec.uploads[0][2])["Date of Request"] == "10/06/2026"
