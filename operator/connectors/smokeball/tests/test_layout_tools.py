"""The layout read (``get_matter_layouts``) and the Negotiation Details write
(``add_negotiation_rows``).

What these defend, each written so that removing the line it defends makes it
fail:

* a failed or unrecognised read is ``error``, never an empty list of tabs;
* the no-section call is an index without fields, a section call returns that
  tab's fields, and sensitive identifiers are masked;
* an EMPTY negotiation tab is named only from the firm's authored design;
* a new row goes after the last filled one, row 0 carries no index, and a
  ``row`` fills only that row's empty fields;
* nothing already entered is ever changed: a clash, a full tab, a value that
  appeared between the read and the write, all write nothing;
* the same figures already on a row are skipped; the same amount on another
  date is held for a person;
* the PATCH carries only the keys being filled, and a value that does not read
  back is reported.
"""

from __future__ import annotations

from typing import Any

import pytest
from smokeball_connector import layout_config as lc
from smokeball_connector import layout_sections as ls
from smokeball_connector import layout_tools as lt

MATTER = "m-1"
DESIGN = "f6719448-d924-4c59-9c1e-3bd2b76550ca"
B = "Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails"


def _item(item_id: str, design: str, parent_index: int | None = 0, desc: str | None = None) -> dict[str, Any]:
    return {
        "id": item_id,
        "layoutDesignId": design,
        "parentId": "Plaintiff",
        "parentIndex": parent_index,
        "description": desc,
    }


class _Tenant:
    def __init__(self, items: list[dict[str, Any]], values: dict[str, dict[str, Any]]) -> None:
        self.items = items
        self.values = values
        self.requests: list[tuple[str, str, Any]] = []
        self.list_response: Any = None
        self.fail_item: str | None = None
        self.patch_sticks = True
        self.patch_fails = False
        self.race: dict[str, Any] | None = None  # values that appear after the first item read
        self._reads: dict[str, int] = {}

    def get(self, path: str, **params: Any) -> Any:
        self.requests.append(("GET", path, params))
        if path == f"/matters/{MATTER}/layouts":
            return self.list_response if self.list_response is not None else {"value": self.items}
        item_id = path.rsplit("/", 1)[1]
        if item_id == self.fail_item:
            raise RuntimeError("boom")
        self._reads[item_id] = self._reads.get(item_id, 0) + 1
        if self.race and self._reads[item_id] == 2:
            self.values[item_id].update(self.race)
        vals = self.values.get(item_id, {})
        return {"id": item_id, "values": [{"key": k, "value": v} for k, v in vals.items()]}

    def request(self, method: str, path: str, *, json: Any = None, **_: Any) -> Any:
        self.requests.append((method, path, json))
        if method == "PATCH" and self.patch_fails:
            raise RuntimeError("vendor 400")
        if method == "PATCH" and self.patch_sticks:
            item_id = path.rsplit("/", 1)[1]
            for pair in json["values"]:
                value = pair["value"]
                # The vendor reads numbers back as floats ("50000.0").
                self.values.setdefault(item_id, {})[pair["key"]] = float(value) if isinstance(value, int) else value
        return {}

    def patches(self) -> list[Any]:
        return [r[2] for r in self.requests if r[0] == "PATCH"]


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(lt, "SLEEP", lambda _s: None)
    monkeypatch.setattr(lt, "VALUE_WAITS", (0, 0))
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(f"smokeball_layouts:\n  settlement_negotiations_design: {DESIGN}\n")
    monkeypatch.setenv(lc.CUSTOMER_YAML_ENV, str(cfg))


def _use(monkeypatch: pytest.MonkeyPatch, tenant: _Tenant) -> _Tenant:
    monkeypatch.setattr(lt, "_client", lambda: tenant)
    return tenant


def _filled_tenant() -> _Tenant:
    return _Tenant(
        [
            _item("ins", "6325a09c_aa", desc="STATEFARM | Claim"),
            _item("neg", f"{DESIGN}_404f"),
            _item("pi", "PersonalInjurySettlementDetailsItem"),
        ],
        {
            "ins": {
                "Matter/Plaintiffs/InsurancePolicy/Insurer": "StateFarm",
                "Matter/Plaintiffs/Person/SocialSecurityNumber": "123-45-6789",
            },
            "neg": {f"{B}/OfferAmount": 5000.0, f"{B}/OfferDate": "2026-08-01T00:00:00", f"{B}[1]/DemandAmount": 40000},
            # A live Medicals tab's first sorted key is a cost field, not a provider.
            "pi": {"FirmCosts/Total": 120, "Providers[0]/Provider/DisplayName": "AMR"},
        },
    )


# ---- read -----------------------------------------------------------------


def test_index_has_sections_and_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _filled_tenant())
    out = lt.get_matter_layouts(MATTER)
    assert out["status"] == "ok"
    sections = {e["item_id"]: e["section"] for e in out["items"]}
    assert sections == {"ins": "Insurance", "neg": "Negotiation Details", "pi": "Medicals"}
    assert all("fields" not in e for e in out["items"])
    assert "not unreadable" in out["note"]


def test_section_returns_parsed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _filled_tenant())
    out = lt.get_matter_layouts(MATTER, section="negotiation details")
    [entry] = out["items"]
    rows = entry["negotiation"]["rows"]
    assert rows[0]["row"] == 0 and rows[0]["offer_amount"] == 5000.0
    assert rows[1] == {"row": 1, "demand_amount": 40000}
    assert entry["negotiation"]["empty_rows"] == list(range(2, 10))


def test_sensitive_identifiers_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _filled_tenant())
    fields = lt.get_matter_layouts(MATTER, section="Insurance")["items"][0]["fields"]
    assert fields["Matter/Plaintiffs/Person/SocialSecurityNumber"] == ls.MASK
    assert fields["Matter/Plaintiffs/InsurancePolicy/Insurer"] == "StateFarm"
    raw = lt.get_matter_layouts(MATTER, section="Insurance", include_sensitive=True)["items"][0]["fields"]
    assert raw["Matter/Plaintiffs/Person/SocialSecurityNumber"] == "123-45-6789"


def test_empty_negotiation_tab_is_named_from_config_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    t = _use(monkeypatch, _Tenant([_item("neg", f"{DESIGN}_c8ff"), _item("x", "77f5d0ee")], {}))
    sections = {e["item_id"]: e["section"] for e in lt.get_matter_layouts(MATTER)["items"]}
    assert sections["neg"] == "Negotiation Details (no rows entered)"
    assert sections["x"] == "Not named (no fields entered)"
    empty = tmp_path / "none.yaml"
    empty.write_text("other: 1\n")
    monkeypatch.setenv(lc.CUSTOMER_YAML_ENV, str(empty))
    assert {e["section"] for e in lt.get_matter_layouts(MATTER)["items"]} == {"Not named (no fields entered)"}
    assert t.patches() == []


def test_a_failed_read_is_an_error_not_an_empty_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    t.list_response = {"unexpected": True}
    out = lt.get_matter_layouts(MATTER)
    assert out["status"] == "error" and "items" not in out


def test_one_unreadable_tab_is_named_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    t.fail_item = "ins"
    out = lt.get_matter_layouts(MATTER)
    assert out["unreadable_items"][0]["item_id"] == "ins"
    assert {e["item_id"] for e in out["items"]} == {"neg", "pi"}


# ---- write ----------------------------------------------------------------


def test_new_row_goes_after_last_filled_with_only_its_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "15000", "offer_date": "2026-09-01"}])
    assert out["status"] == "written"
    assert out["entries"][0]["row"] == 2
    [patch] = t.patches()
    assert {p["key"] for p in patch["values"]} == {f"{B}[2]/OfferAmount", f"{B}[2]/OfferDate"}


def test_row_zero_key_has_no_index(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _Tenant([_item("neg", f"{DESIGN}_c8ff")], {}))
    out = lt.add_negotiation_rows(MATTER, [{"demand_amount": "50000", "demand_date": "2026-07-01"}])
    assert out["status"] == "written" and out["entries"][0]["row"] == 0
    assert {p["key"] for p in t.patches()[0]["values"]} == {f"{B}/DemandAmount", f"{B}/DemandDate"}


def test_gap_rows_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Tenant([_item("neg", f"{DESIGN}_c8ff")], {"neg": {f"{B}/OfferAmount": 1, f"{B}[3]/OfferAmount": 2}})
    _use(monkeypatch, t)
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}])
    assert out["entries"][0]["row"] == 4 and out["gap_rows"] == [1, 2]


def test_row_fills_only_empty_fields_and_refuses_a_clash(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"row": 1, "demand_amount": "40000", "offer_amount": "20000"}])
    assert out["status"] == "written"
    assert {p["key"] for p in t.patches()[0]["values"]} == {f"{B}[1]/OfferAmount"}
    clash = lt.add_negotiation_rows(MATTER, [{"row": 1, "demand_amount": "45000"}])
    assert clash["status"] == "refused" and len(t.patches()) == 1


def test_same_figures_are_already_present_and_write_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "5000.00", "offer_date": "2026-08-01"}])
    assert out["status"] == "nothing_to_write"
    assert out["entries"][0] == {"entry": 0, "status": "already_present", "row": 0}
    assert t.patches() == []


def test_same_amount_other_date_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "5000", "offer_date": "2026-09-15"}])
    assert out["entries"][0]["status"] == "possible_duplicate" and t.patches() == []


def test_full_tab_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Tenant([_item("neg", f"{DESIGN}_c8ff")], {"neg": {f"{B}[9]/OfferAmount": 1}})
    _use(monkeypatch, t)
    assert lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}])["status"] == "refused"
    assert t.patches() == []


def test_unknown_tab_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    t = _use(monkeypatch, _Tenant([_item("x", "77f5d0ee_aa")], {}))
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}])
    assert out["status"] == "refused" and "not set up" in out["reason"] and t.patches() == []


def test_two_plaintiffs_need_an_index(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Tenant([_item("n0", f"{DESIGN}_c8ff", 0, "A"), _item("n1", f"{DESIGN}_c8ff", 1, "B")], {})
    _use(monkeypatch, t)
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}])
    assert out["status"] == "refused" and len(out["tabs"]) == 2 and t.patches() == []
    ok = lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}], plaintiff_index=1)
    assert ok["item_id"] == "n1" and ok["status"] == "written"


def test_a_value_entered_meanwhile_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    t.race = {f"{B}[2]/OfferAmount": 999}
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "15000"}])
    assert out["status"] == "refused" and t.patches() == []


def test_a_value_that_does_not_read_back_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    t.patch_sticks = False
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "15000"}])
    assert out["status"] == "readback_mismatch" and f"{B}[2]/OfferAmount" in out["mismatch"]


@pytest.mark.parametrize(
    "row",
    [
        {"offer_amount": 1500.5},
        {"offer_amount": "15,000"},
        {"offer_date": "09/01/2026"},
        {"note": "only a note"},
        {"offer_amount": "100", "colour": "red"},
        {"row": 12, "offer_amount": "100"},
    ],
)
def test_malformed_input_writes_nothing(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> None:
    t = _use(monkeypatch, _filled_tenant())
    assert lt.add_negotiation_rows(MATTER, [row])["status"] == "refused"
    assert t.patches() == [] and t.requests == []


def test_details_and_minimum_fill_only_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _filled_tenant()
    t.values["neg"][ls.DETAILS_KEY] = "existing summary"
    _use(monkeypatch, t)
    out = lt.add_negotiation_rows(MATTER, [], details="new summary", minimum_settlement="25000")
    assert out["status"] == "written" and out["skipped"] == ["details already entered; not changed"]
    assert {p["key"] for p in t.patches()[0]["values"]} == {ls.MINIMUM_KEY}


def test_amounts_go_as_numbers_and_minimum_reads_back_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "15000.50"}], minimum_settlement="50000")
    assert out["status"] == "written", out
    sent = {p["key"]: p["value"] for p in t.patches()[0]["values"]}
    assert sent[f"{B}[2]/OfferAmount"] == 15000.5 and sent[ls.MINIMUM_KEY] == 50000


def test_a_named_row_and_an_auto_row_never_share_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    out = lt.add_negotiation_rows(MATTER, [{"row": 2, "demand_amount": "30000"}, {"offer_amount": "12000"}])
    assert [e["row"] for e in out["entries"]] == [2, 3] and out["status"] == "written"
    assert {p["key"] for p in t.patches()[0]["values"]} == {f"{B}[2]/DemandAmount", f"{B}[3]/OfferAmount"}


def test_the_same_entry_twice_in_one_call_is_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    row = {"offer_amount": "12000", "offer_date": "2026-09-01"}
    out = lt.add_negotiation_rows(MATTER, [row, dict(row)])
    assert [e["status"] for e in out["entries"]] == ["written", "already_present"]
    assert len(t.patches()[0]["values"]) == 2


def test_an_impossible_date_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    assert lt.add_negotiation_rows(MATTER, [{"offer_amount": "1", "offer_date": "2026-13-45"}])["status"] == "refused"
    assert lt.add_negotiation_rows(MATTER, [], minimum_settlement="NaN")["status"] == "refused"
    assert t.patches() == []


def test_a_refused_patch_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _filled_tenant())
    t.patch_fails = True
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "12000"}])
    assert out["status"] == "refused" and "refused the write" in out["reason"]


def test_a_second_plaintiff_without_a_tab_still_needs_an_index(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Tenant([_item("n0", f"{DESIGN}_c8ff", 0, "A"), _item("h1", "71370204_d54b", 1, "Medi-Cal")], {"h1": {"x": 1}})
    _use(monkeypatch, t)
    out = lt.add_negotiation_rows(MATTER, [{"offer_amount": "3000"}])
    assert out["status"] == "refused" and "more than one plaintiff" in out["reason"] and t.patches() == []


# ---- the Operator's own summary (refresh_operator_details) -----------------
# The ONE change to an entered value: a summary the Operator wrote, rewritten
# from the tab after a run's write. A summary the firm wrote is never touched.

OURS = (
    "Entered 10/9/26 from the offer letters and emails saved in this file. "
    "Latest: our $1,000,000 demand of 7/28/26 has no response in the file."
)
NEW = (
    "Entered 10/9/26 from the offer letters and emails saved in this file. "
    "Latest: Defendants / carrier $956,000 (9/23/26), accepted 9/23/26."
)


def _summary_tenant(details: str) -> _Tenant:
    return _Tenant([_item("neg", f"{DESIGN}_404f")], {"neg": {f"{B}/DemandAmount": 1000000, f"{B}/Details": details}})


def test_an_operator_summary_is_rewritten_and_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _summary_tenant(OURS))
    out = lt.refresh_operator_details(MATTER, NEW, OURS)
    assert out["status"] == "written" and out["details"] == NEW
    assert t.patches() == [{"values": [{"key": f"{B}/Details", "value": NEW}]}]


@pytest.mark.parametrize(
    "firm",
    ["Chris: carrier at limits, client to decide by Friday.", "Entered by Christa, see the file.", ""],
)
def test_a_firm_written_summary_is_never_changed(monkeypatch: pytest.MonkeyPatch, firm: str) -> None:
    """FALSIFIER: drop the marker check and the firm's own words are overwritten."""
    t = _use(monkeypatch, _summary_tenant(firm))
    out = lt.refresh_operator_details(MATTER, NEW, firm)
    assert out["status"] == "refused" and t.patches() == []
    assert t.values["neg"][f"{B}/Details"] == firm


def test_a_summary_the_firm_replaced_after_the_read_is_not_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller read the Operator's summary; the firm has since replaced it."""
    t = _use(monkeypatch, _summary_tenant("Chris: settled, do not touch."))
    out = lt.refresh_operator_details(MATTER, NEW, OURS)
    assert out["status"] == "refused" and t.patches() == []


def test_a_summary_changed_between_the_check_and_the_write_is_not_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _summary_tenant(OURS))
    t.race = {f"{B}/Details": "Chris: settled, do not touch."}
    out = lt.refresh_operator_details(MATTER, NEW, OURS)
    assert out["status"] == "refused" and t.patches() == []


def test_a_new_summary_without_the_marker_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _summary_tenant(OURS))
    out = lt.refresh_operator_details(MATTER, "Latest: something.", OURS)
    assert out["status"] == "refused" and t.patches() == []


def test_a_summary_that_does_not_read_back_is_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _summary_tenant(OURS))
    t.patch_sticks = False
    out = lt.refresh_operator_details(MATTER, NEW, OURS)
    assert out["status"] == "readback_mismatch" and out["reads"] == OURS and out["wrote"] == NEW


def test_an_unchanged_summary_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _use(monkeypatch, _summary_tenant(OURS))
    assert lt.refresh_operator_details(MATTER, OURS, OURS)["status"] == "nothing_to_write"
    assert t.patches() == []


def test_the_summary_write_is_not_a_tool() -> None:
    """Runner-only: an agent can never call it."""
    registered: list[str] = []

    class _Server:
        def tool(self) -> Any:
            def deco(fn: Any) -> Any:
                registered.append(fn.__name__)
                return fn

            return deco

    lt.register(_Server())
    assert "refresh_operator_details" not in registered and "add_negotiation_rows" in registered
