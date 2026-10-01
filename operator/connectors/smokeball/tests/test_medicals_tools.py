"""The Medicals tab write (``add_medicals_row``).

What these defend, each written so that removing the line it defends makes
it fail:

* the write is OPENED by the filed-document ledger and by nothing else: a
  file id this process did not file on that matter writes nothing;
* a provider already on the tab is reported and left alone;
* a provider the firm's contacts do not hold, or hold twice, creates nothing
  (never a contact, never a row);
* a matter with several Medicals tabs refuses to choose a claimant;
* the row is found where the TENANT put it, named as the tenant named it;
* a value that does not read back is reported, not retried, not undone;
* the note is composed from the facts and the ledger, says which pages, and
  says "read from a scan" only when the ledger says so;
* no argument carries free text the draft gate would have to scan.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from smokeball_connector import letter_pages as lp
from smokeball_connector import medicals_tools as mt

MATTER = "m-1"
TAB = "pi-1"
PI = {"id": TAB, "layoutDesign": {"id": "PersonalInjurySettlementDetailsItem"}, "parentIndex": 0}
OTHER = {"id": "ins-1", "layoutDesign": {"id": "6325a09c_4ae31510"}, "parentIndex": 0}
AMR = {"id": "c-amr", "company": {"name": "American Medical Response"}}
NORTHSIDE = {"id": "c-nor", "company": {"name": "Northside Imaging Center"}}
NORTHSIDE_TWO = {"id": "c-nor2", "company": {"name": "Northside Imaging Center Billing"}}

_KEY_INDEX = re.compile(r"^Providers\[(\d+)\]/")


class _Tenant:
    """A scripted tenant: a matter with layout items, a Medicals tab whose
    values are a dict, and a contact search. Records every request so a test
    asserts what was NOT written, not only the status returned."""

    def __init__(
        self,
        *,
        tabs: list[dict[str, Any]] | None = None,
        rows: dict[int, str] | None = None,
        contacts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tabs = tabs if tabs is not None else [OTHER, PI]
        self.values: dict[str, Any] = {}
        for index, name in (rows or {}).items():
            self.values[f"Providers[{index}]/Provider/DisplayName"] = name
            self.values[f"Providers[{index}]/Invoices[0]/InitialInvoiceAmount"] = "100.00"
        self.contacts = contacts if contacts is not None else [AMR]
        self.requests: list[tuple[str, str, Any]] = []
        self.link_lands_at: int | None = None  # None = the planned slot
        self.link_adds_row = True
        self.patch_sticks = True
        self.fail_layouts = False

    # -- reads
    def get(self, path: str, **params: Any) -> Any:
        self.requests.append(("GET", path, params))
        if path.endswith("/layouts"):
            if self.fail_layouts:
                raise RuntimeError("boom")
            return {"value": self.tabs}
        if path == "/contacts":
            return {"value": list(self.contacts)}
        if path.startswith(f"/matters/{MATTER}/layouts/"):
            return {"values": [{"key": k, "value": v} for k, v in self.values.items()]}
        raise AssertionError(f"unscripted GET {path}")

    # -- writes
    def request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        self.requests.append((method, path, json))
        if method == "POST" and path.endswith("/contacts"):
            if self.link_adds_row:
                planned = int(_KEY_INDEX.match(json["key"]).group(1))
                index = planned if self.link_lands_at is None else self.link_lands_at
                contact = next(c for c in self.contacts if c["id"] == json["contactId"])
                self.values[f"Providers[{index}]/Provider/DisplayName"] = contact["company"]["name"]
                self.values[f"Providers[{index}]/Provider/MatterEntityId"] = json["contactId"]
            return {}
        if method == "PATCH":
            if self.patch_sticks:
                for entry in json["values"]:
                    self.values[entry["key"]] = entry["value"]
            return {}
        raise AssertionError(f"unscripted {method} {path}")

    def writes(self) -> list[tuple[str, str, Any]]:
        return [r for r in self.requests if r[0] in ("POST", "PATCH")]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    lp.FILED_DOCS._docs.clear()
    monkeypatch.setattr(mt, "SLEEP", lambda _s: None)
    monkeypatch.setattr(mt, "_stamp", lambda: lambda text: f"[Operator] {text}")


@pytest.fixture
def tenant(monkeypatch: pytest.MonkeyPatch) -> _Tenant:
    t = _Tenant()
    monkeypatch.setattr(mt, "_client", lambda: t)
    return t


def _filed(file_id: str = "file-7", *, from_scan: bool = True, matter: str = MATTER) -> None:
    lp.FILED_DOCS.record(matter, file_id, "2026-10-01 AMR pp3-4.pdf", 3, 4, from_scan=from_scan)


def _add(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "matter_id": MATTER,
        "source_file_id": "file-7",
        "provider_name": "American Medical Response",
        "charge": "4345.16",
        "service_start": "2026-07-16",
        "service_end": "2026-07-16",
    }
    args.update(overrides)
    return mt.add_medicals_row(**args)


# ---- the signature --------------------------------------------------------


def test_no_argument_carries_free_text() -> None:
    params = set(inspect.signature(mt.add_medicals_row).parameters)
    assert params == {
        "matter_id",
        "source_file_id",
        "provider_name",
        "charge",
        "service_start",
        "service_end",
        "account_number",
        "claimant_index",
    }
    # The overlay's draft gate scans write arguments by these names; the
    # note is composed in the connector, so none may exist.
    assert not params & {"description", "subject", "title", "body", "note", "text", "content", "message"}


# ---- what opens the write ------------------------------------------------


def test_a_file_this_run_did_not_file_writes_nothing(tenant: _Tenant) -> None:
    out = _add()
    assert out["status"] == "refused"
    assert "not a document this run filed" in out["reason"]
    assert tenant.requests == [], "nothing may be read, let alone written, without the ledger entry"


def test_a_file_filed_on_another_matter_does_not_open_this_one(tenant: _Tenant) -> None:
    _filed(matter="m-2")
    out = _add()
    assert out["status"] == "refused"
    assert tenant.writes() == []


# ---- the happy path and the tenant's own naming --------------------------


def test_writes_one_row_from_a_filed_bill_and_reads_it_back(tenant: _Tenant) -> None:
    _filed(from_scan=True)
    out = _add(account_number="007055644-0001")
    assert out["status"] == "written", out
    link, patch = tenant.writes()
    assert link[0] == "POST" and link[1] == f"/matters/{MATTER}/layouts/{TAB}/contacts"
    assert link[2] == {"key": "Providers[0]/Provider/MatterEntityId", "contactId": "c-amr"}
    assert patch[0] == "PATCH" and patch[1] == f"/matters/{MATTER}/layouts/{TAB}"
    written = {v["key"]: v["value"] for v in patch[2]["values"]}
    assert written["Providers[0]/Invoices[0]/InitialInvoiceAmount"] == "4345.16"
    assert written["Providers[0]/Invoices[0]/ServiceStartDate"] == "2026-07-16"
    assert written["Providers[0]/Invoices[0]/ServiceEndDate"] == "2026-07-16"
    assert written["Providers[0]/AccountNumber"] == "007055644-0001"
    assert "InvoiceBalance" not in " ".join(written), "the balance is derived by the tenant, never written"
    note = written["Providers[0]/Note"]
    assert note.startswith("[Operator] ")
    assert "2026-10-01 AMR pp3-4.pdf" in note and "pages 3-4" in note
    assert "read from a scan" in note
    assert "4345.16" in note and "Check the figure against the bill" in note
    assert out["index"] == 0 and out["linked_as"] == "American Medical Response"
    assert out["from_scan"] is True


def test_a_bill_read_from_text_says_so_in_the_note(tenant: _Tenant) -> None:
    _filed(from_scan=False)
    out = _add()
    assert out["status"] == "written"
    assert "read from the document's text" in out["note"]
    assert "read from a scan" not in out["note"]


def test_the_row_is_found_where_the_tenant_put_it(tenant: _Tenant) -> None:
    """The tenant owns the slot: the link was asked for Providers[1] (one row
    exists) and landed at 3. The values go to 3, never to the planned 1."""
    _filed()
    tenant.values["Providers[0]/Provider/DisplayName"] = "Kaiser"
    tenant.link_lands_at = 3
    out = _add()
    assert out["status"] == "written"
    _, patch = tenant.writes()
    keys = {v["key"] for v in patch[2]["values"]}
    assert all(k.startswith("Providers[3]/") for k in keys), keys
    assert out["index"] == 3


# ---- every way to be unsure ------------------------------------------------


def test_a_provider_already_on_the_tab_is_reported_and_left_alone(tenant: _Tenant) -> None:
    _filed()
    tenant.values["Providers[0]/Provider/DisplayName"] = "AMERICAN MEDICAL RESPONSE."
    tenant.values["Providers[0]/Invoices[0]/InitialInvoiceAmount"] = "4345.16"
    out = _add()
    assert out["status"] == "already_present"
    assert out["existing"]["charge"] == "4345.16"
    assert out["bill"]["charge"] == "4345.16"
    assert tenant.writes() == []


@pytest.mark.parametrize("contacts", [[], [NORTHSIDE, NORTHSIDE_TWO]])
def test_no_contact_or_two_contacts_creates_nothing(tenant: _Tenant, contacts: list[dict[str, Any]]) -> None:
    _filed()
    tenant.contacts = contacts
    out = _add(provider_name="Northside Imaging Center")
    assert out["status"] == "needs_contact"
    assert [c["id"] for c in out["candidates"]] == [c["id"] for c in contacts]
    assert tenant.writes() == [], "no contact is ever created and no row is ever linked"


def test_several_claimants_refuse_to_choose(tenant: _Tenant) -> None:
    _filed()
    second = dict(PI, id="pi-2", parentIndex=1)
    tenant.tabs = [PI, second]
    out = _add()
    assert out["status"] == "refused" and "several Medicals tabs" in out["reason"]
    assert [t["claimant_index"] for t in out["tabs"]] == [0, 1]
    assert tenant.writes() == []
    out = _add(claimant_index=1)
    assert out["status"] == "written"
    link = tenant.writes()[0]
    assert link[1] == f"/matters/{MATTER}/layouts/pi-2/contacts"


def test_a_matter_with_no_medicals_tab_refuses(tenant: _Tenant) -> None:
    _filed()
    tenant.tabs = [OTHER]
    out = _add()
    assert out["status"] == "refused" and "no Medicals tab" in out["reason"]
    assert tenant.writes() == []


def test_a_layout_read_that_fails_is_a_failed_step(tenant: _Tenant) -> None:
    _filed()
    tenant.fail_layouts = True
    out = _add()
    assert out["status"] == "refused" and "could not be read" in out["reason"]


def test_a_value_that_does_not_stick_is_reported_not_retried(tenant: _Tenant) -> None:
    _filed()
    tenant.patch_sticks = False
    out = _add()
    assert out["status"] == "readback_mismatch"
    assert "Providers[0]/Invoices[0]/InitialInvoiceAmount" in out["mismatch"]
    patches = [r for r in tenant.writes() if r[0] == "PATCH"]
    assert len(patches) == 1, "a mismatch is reported once, never retried into place"


def test_a_link_that_never_appears_stops_before_the_values(tenant: _Tenant) -> None:
    _filed()
    tenant.link_adds_row = False
    out = _add()
    assert out["status"] == "link_not_visible"
    assert [r[0] for r in tenant.writes()] == ["POST"]


# ---- the facts -------------------------------------------------------------


@pytest.mark.parametrize("charge", ["", "0", "-5.00", "1,250.00", "1250.123", "abc", 1250.0, None])
def test_charge_must_be_a_positive_two_decimal_string(tenant: _Tenant, charge: Any) -> None:
    _filed()
    out = _add(charge=charge)
    assert out["status"] == "refused" and "charge" in out["reason"]
    assert tenant.requests == []


@pytest.mark.parametrize("bad", ["7/16/2026", "2026-7-16", "", None])
def test_dates_must_be_iso(tenant: _Tenant, bad: Any) -> None:
    _filed()
    out = _add(service_start=bad)
    assert out["status"] == "refused" and "service_start" in out["reason"]
    assert tenant.requests == []


def test_service_end_before_start_refuses(tenant: _Tenant) -> None:
    _filed()
    out = _add(service_start="2026-07-16", service_end="2026-07-15")
    assert out["status"] == "refused"
    assert tenant.requests == []


# ---- the helpers -----------------------------------------------------------


def test_name_normalization_treats_spelling_as_one_facility() -> None:
    rows = {0: {"Provider/DisplayName": "NORTHSIDE IMAGING CENTER."}, 1: {"Provider/DisplayName": "Kaiser"}}
    assert mt.indices_named(rows, "Northside Imaging Center") == [0]
    assert mt.indices_named(rows, "Northside Imaging Center, Valley Health") == [0]
    assert mt.indices_named(rows, "Imaging") == [], "a short fragment is not a facility"
    # A row the firm named with one short word is not claimed by a longer
    # name on a bill: the tab's name is the contact record's, so "Kaiser" is
    # a contact called Kaiser, and whether "Kaiser Permanente" is that contact
    # is the contact search's question (every token must be on the record),
    # which answers needs_contact and creates nothing.
    assert mt.indices_named(rows, "Kaiser Permanente") == []
    assert mt.indices_named(rows, "kaiser") == [1]


def test_layout_values_accepts_both_shapes() -> None:
    listed = {"values": [{"key": "a", "value": "1"}]}
    wrapped = {"value": [listed]}
    assert mt.layout_values(listed) == {"a": "1"}
    assert mt.layout_values(wrapped) == {"a": "1"}
    assert mt.layout_values({"values": {"a": "1"}}) == {"a": "1"}


def test_compare_reads_amounts_numerically_and_dates_by_day() -> None:
    want = {
        "x/Invoices[0]/InitialInvoiceAmount": "4345.16",
        "x/Invoices[0]/ServiceStartDate": "2026-07-16",
        "x/Note": "n",
    }
    got = {
        "x/Invoices[0]/InitialInvoiceAmount": 4345.16,
        "x/Invoices[0]/ServiceStartDate": "2026-07-16T00:00:00",
        "x/Note": "n ",
    }
    assert mt.compare(want, got) == {}
    assert "x/Note" in mt.compare(want, dict(got, **{"x/Note": "other"}))
