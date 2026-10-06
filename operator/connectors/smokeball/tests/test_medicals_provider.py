"""A treating facility on the Medicals tab with no bill (``add_medicals_provider``).

What these defend, each written so that removing the line it defends fails it:

* a facility the contacts hold once, by exactly its name, is linked, and the
  row's description is the note, read back;
* a facility the contacts do not hold at all is added as a company (with the
  address given) and linked;
* several exact records, or near matches, write nothing and list candidates;
  ``create_new`` is the sender's answer that a near match is not this facility;
* a facility already on the tab is reported and left alone;
* no money field is ever written.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from smokeball_connector import medicals_provider as mp
from smokeball_connector import medicals_tools as mt

MATTER = "m-1"
TAB = "pi-1"
PI = {"id": TAB, "layoutDesign": {"id": "PersonalInjurySettlementDetailsItem"}, "parentIndex": 0}
_KEY_INDEX = re.compile(r"^Providers\[(\d+)\]/")


class _Tenant:
    def __init__(self, contacts: list[dict[str, Any]] | None = None, rows: dict[int, str] | None = None) -> None:
        self.contacts = list(contacts or [])
        self.values: dict[str, Any] = {}
        for index, name in (rows or {}).items():
            self.values[f"Providers[{index}]/Provider/DisplayName"] = name
        self.requests: list[tuple[str, str, Any]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.requests.append(("GET", path, params))
        if path == f"/matters/{MATTER}/layouts":
            return {"value": [PI]}
        if path == f"/matters/{MATTER}/layouts/{TAB}":
            return {"values": [{"key": k, "value": v} for k, v in self.values.items()]}
        if path == "/contacts":
            return {"value": list(self.contacts)}
        raise AssertionError(f"unscripted GET {path}")

    def request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        self.requests.append((method, path, json))
        if method == "POST" and path == "/contacts":
            made = {"id": f"c-new-{len(self.contacts)}", "company": dict(json["company"])}
            self.contacts.append(made)
            return {"id": made["id"]}
        if method == "POST" and path.endswith("/contacts"):
            index = int(_KEY_INDEX.match(json["key"]).group(1))
            contact = next(c for c in self.contacts if c["id"] == json["contactId"])
            self.values[f"Providers[{index}]/Provider/DisplayName"] = contact["company"]["name"]
            return {}
        if method == "PATCH":
            for entry in json["values"]:
                self.values[entry["key"]] = entry["value"]
            return {}
        raise AssertionError(f"unscripted {method} {path}")

    def writes(self) -> list[tuple[str, str, Any]]:
        return [r for r in self.requests if r[0] in ("POST", "PATCH")]


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt, "SLEEP", lambda _s: None)


def _use(monkeypatch: pytest.MonkeyPatch, tenant: _Tenant) -> _Tenant:
    monkeypatch.setattr(mp, "_client", lambda: tenant)
    return tenant


RIVERSIDE = {"id": "c-river", "company": {"name": "Riverside Community Health Center"}}


def test_no_money_argument_exists() -> None:
    params = set(inspect.signature(mp.add_medicals_provider).parameters)
    assert not params & {"charge", "amount", "service_start", "service_end", "account_number"}


def test_links_the_one_exact_contact_and_writes_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([RIVERSIDE], rows={0: "Kaiser"}))
    out = mp.add_medicals_provider(
        MATTER, "Riverside Community Health Center", note="Current - need 5 years of records"
    )
    assert out["status"] == "written"
    assert (out["row"], out["contactId"], out["created"]) == (1, "c-river", False)
    assert tenant.values["Providers[1]/Invoices[0]/Description"] == "Current - need 5 years of records"
    patched = [k["key"] for m, _p, j in tenant.writes() if m == "PATCH" for k in j["values"]]
    assert patched == ["Providers[1]/Invoices[0]/Description"], "nothing but the description is written"
    assert not any(m == "POST" and p == "/contacts" for m, p, _j in tenant.writes())


def test_adds_an_unknown_facility_as_a_company_with_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([]))
    out = mp.add_medicals_provider(
        MATTER, "Clearview Imaging", address="100 Main St, Springfield, CA 90000", note="Prior"
    )
    assert out["status"] == "written" and out["created"] is True
    made = next(j for m, p, j in tenant.writes() if m == "POST" and p == "/contacts")
    assert made == {
        "company": {
            "name": "Clearview Imaging",
            "businessAddress": {
                "addressLine1": "100 Main St",
                "city": "Springfield",
                "state": "CA",
                "zipCode": "90000",
            },
        }
    }
    assert tenant.values["Providers[0]/Invoices[0]/Description"] == "Prior"


def test_an_address_of_another_shape_is_kept_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([]))
    mp.add_medicals_provider(MATTER, "Clearview Imaging", address="Suite 4, the old mill")
    made = next(j for m, p, j in tenant.writes() if m == "POST" and p == "/contacts")
    assert made["company"]["businessAddress"] == {"addressLine1": "Suite 4, the old mill"}


def test_two_exact_records_write_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    twin = {"id": "c-river2", "company": {"name": "Riverside Community Health Center"}}
    tenant = _use(monkeypatch, _Tenant([RIVERSIDE, twin]))
    out = mp.add_medicals_provider(MATTER, "Riverside Community Health Center")
    assert out["status"] == "needs_contact"
    assert {c["id"] for c in out["candidates"]} == {"c-river", "c-river2"}
    assert tenant.writes() == []


def test_a_near_match_asks_and_create_new_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    northgate = {"id": "c-sut", "company": {"name": "Northgate Medical Center Springfield"}}
    tenant = _use(monkeypatch, _Tenant([northgate]))
    out = mp.add_medicals_provider(MATTER, "Northgate Springfield")
    assert out["status"] == "needs_contact"
    assert out["candidates"] == [{"id": "c-sut", "name": "Northgate Medical Center Springfield"}]
    assert tenant.writes() == []
    again = mp.add_medicals_provider(MATTER, "Northgate Springfield", create_new=True)
    assert again["status"] == "written" and again["created"] is True


def test_a_facility_already_on_the_tab_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([RIVERSIDE], rows={0: "RIVERSIDE COMMUNITY HEALTH CENTER."}))
    out = mp.add_medicals_provider(MATTER, "Riverside Community Health Center", note="Current")
    assert out["status"] == "already_present" and out["row"] == 0
    assert tenant.writes() == []


NORTHSIDE = {"id": "c-nor", "company": {"name": "Northside Imaging Center"}}
VALLEY = {"id": "c-val", "company": {"name": "Valley Northside Imaging Center"}}


def test_a_row_that_only_resembles_the_facility_is_not_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review probe (2026-10-06): only the Valley row is on the tab. It is
    not Northside, so "already present" would be false and nothing would be
    written; a person is asked, and ``create_new`` is their answer."""
    tenant = _use(monkeypatch, _Tenant([VALLEY, NORTHSIDE], rows={0: "Valley Northside Imaging Center"}))
    out = mp.add_medicals_provider(MATTER, "Northside Imaging Center")
    assert out["status"] == "needs_contact", out
    assert out["candidates"] == [{"row": 0, "name": "Valley Northside Imaging Center"}]
    assert tenant.writes() == []
    again = mp.add_medicals_provider(MATTER, "Northside Imaging Center", create_new=True)
    assert again["status"] == "written", again
    assert (again["row"], again["contactId"], again["created"]) == (1, "c-nor", False)


def test_the_exact_row_wins_over_one_that_contains_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(
        monkeypatch,
        _Tenant([VALLEY, NORTHSIDE], rows={0: "Valley Northside Imaging Center", 1: "Northside Imaging Center"}),
    )
    out = mp.add_medicals_provider(MATTER, "northside imaging center.")
    assert out["status"] == "already_present" and out["row"] == 1
    assert tenant.writes() == []


def test_without_a_note_only_the_link_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([RIVERSIDE]))
    out = mp.add_medicals_provider(MATTER, "Riverside Community Health Center")
    assert out["status"] == "written"
    assert [m for m, _p, _j in tenant.writes()] == ["POST"]


def test_bad_arguments_refuse_before_any_read(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _use(monkeypatch, _Tenant([RIVERSIDE]))
    assert mp.add_medicals_provider(MATTER, "  ")["status"] == "refused"
    assert mp.add_medicals_provider(MATTER, "X", note="n" * 201)["status"] == "refused"
    assert tenant.requests == []
