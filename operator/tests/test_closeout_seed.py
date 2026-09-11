"""The Settlement Closeout seed must stay faithful to the real wire shape (ss #2455).

The seed generates both what a person keys into the staging sandbox and the canned
layout payloads the offline tests run against. If those two drift, the read-back
reconciliation probe stops being able to say whether a mismatch was a misread or a
keying slip, which is the only reason the seed is single-sourced.

The key vocabulary pinned below was observed on real production layouts in a
read-only probe (vfy_01M0DXZ33QCTE7R58GF2WJ1ZB1), key names only. A key that is not
in that census must not appear in a fixture: an invented field teaches a skill to
gate on a branch that can never fire, and every assertion downstream then passes
vacuously.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

SEED_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures/law-firm/pi/lien-ledger-tracker/seed"
SEED_FILE = SEED_DIR / "closeout-seed.yaml"
WIRE_DIR = SEED_DIR / "wire"
RENDERER = SEED_DIR / "render_seed.py"

DESIGN_ID = "PersonalInjurySettlementDetailsItem"

# Observed on production, verbatim. Not a wish list.
PRODUCTION_PROVIDER_KEYS = {
    "Providers[n]/AccountNumber",
    "Providers[n]/DocumentRequests[]",
    "Providers[n]/FinalAmount",
    "Providers[n]/InvoiceAdjustmentsTotal",
    "Providers[n]/InvoiceAmountPaidTotal",
    "Providers[n]/InvoiceBalance",
    "Providers[n]/InvoiceInitialAmount",
    "Providers[n]/InvoiceTotalClaim",
    "Providers[n]/Invoices[n]/Description",
    "Providers[n]/Invoices[n]/InitialInvoiceAmount",
    "Providers[n]/Invoices[n]/InvoiceAdjustmentAmount",
    "Providers[n]/Invoices[n]/InvoiceBalance",
    "Providers[n]/Invoices[n]/Payors[]",
    "Providers[n]/Invoices[n]/ServiceEndDate",
    "Providers[n]/Invoices[n]/ServiceStartDate",
    "Providers[n]/LienAmount",
    "Providers[n]/LienAsserted",
    "Providers[n]/LienOrBalanceNote",
    "Providers[n]/Note",
    "Providers[n]/OverrideCalculations",
    "Providers[n]/Provider/DisplayName",
    "Providers[n]/Provider/MatterEntityId",
    "Providers[n]/ServiceEndDate",
    "Providers[n]/ServiceStartDate",
    "Providers[n]/SummaryTotalClaim",
}


def _seed() -> dict:
    return yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))


def _items(number: str) -> list[dict]:
    return json.loads((WIRE_DIR / f"{number}.json").read_text(encoding="utf-8"))


def _norm_keys(item: dict) -> set[str]:
    return {re.sub(r"\[\d+\]", "[n]", v["key"]) for v in item["values"]}


def _by_key(item: dict) -> dict[str, dict]:
    return {v["key"]: v for v in item["values"]}


def test_every_seeded_matter_has_a_wire_payload():
    for m in _seed()["matters"]:
        assert (WIRE_DIR / f"{m['number']}.json").exists(), (
            f"{m['seed_id']} has no rendered payload; run render_seed.py"
        )


def test_provider_key_vocabulary_matches_the_production_census():
    """No invented keys, and no silently dropped ones."""
    seen: set[str] = set()
    for m in _seed()["matters"]:
        for item in _items(m["number"]):
            seen |= {k for k in _norm_keys(item) if k.startswith("Providers[n]/")}
    invented = seen - PRODUCTION_PROVIDER_KEYS
    assert not invented, f"fixture invents keys never seen on the wire: {sorted(invented)}"
    missing = PRODUCTION_PROVIDER_KEYS - seen
    assert not missing, f"fixture no longer exercises observed keys: {sorted(missing)}"


def test_every_item_is_a_per_plaintiff_settlement_details_item():
    for m in _seed()["matters"]:
        for item in _items(m["number"]):
            assert item["layoutDesignId"] == DESIGN_ID
            assert item["parentId"] == "Plaintiff"
            assert isinstance(item["parentIndex"], int)


def test_multi_plaintiff_matter_keeps_its_items_separate():
    """The flattening defect: two plaintiffs must not collapse into one dict."""
    items = _items("2026-SC-203")
    assert len(items) == 2, "S-03 is the multi-plaintiff case and must render two items"
    assert {i["parentIndex"] for i in items} == {0, 1}
    names = [{v["value"] for v in i["values"] if v["key"].endswith("/Provider/DisplayName")} for i in items]
    assert names[0] and names[1], "each plaintiff carries its own providers"
    assert names[0].isdisjoint(names[1]), "the two plaintiffs' providers must not overlap here"


def test_the_empty_medicals_matter_carries_no_provider_rows():
    """S-02 is the 62% case: blank, and blank is not zero."""
    (item,) = _items("2026-SC-202")
    provider_keys = {k for k in _norm_keys(item) if k.startswith("Providers[n]/")}
    assert not provider_keys, "S-02 must have no provider detail at all"
    assert "Providers[]" in _by_key(item), "the empty collection marker is still present"


def test_unset_values_carry_no_value_member_at_all():
    """A layout value is absent, not empty-string, when unset - the parser depends on it."""
    (item,) = _items("2026-SC-201")
    unset = [v for v in item["values"] if "value" not in v]
    assert unset, "some keys must render as absent, mirroring the wire"
    assert all(set(v.keys()) == {"key"} for v in unset)


def test_the_cleared_matter_carries_real_zeros_not_blanks():
    """S-06 is the closure candidate: 0.00 present, not missing."""
    (item,) = _items("2026-SC-206")
    balances = [v for k, v in _by_key(item).items() if k.endswith("/InvoiceBalance") and "Invoices[" not in k]
    assert balances, "S-06 must carry provider balance rows"
    assert all("value" in v and float(v["value"]) == 0.0 for v in balances)


def test_the_shared_payer_shares_one_entity_id_and_the_typo_does_not():
    """The rollup groups on MatterEntityId; the typo is a separate contact by design."""
    seed = _seed()
    shared = seed["providers"]["valley_health"]["matter_entity_id"]
    typo = seed["providers"]["valley_health_typo"]["matter_entity_id"]
    assert shared != typo, "the misspelling is its own contact record, as at a real firm"

    def entity_ids(number: str) -> set[str]:
        ids: set[str] = set()
        for item in _items(number):
            ids |= {
                v["value"] for v in item["values"] if v["key"].endswith("/Provider/MatterEntityId") and "value" in v
            }
        return ids

    assert shared in entity_ids("2026-SC-204")
    assert shared in entity_ids("2026-SC-205"), "S-04 and S-05 must roll up as one provider"
    assert typo in entity_ids("2026-SC-201")
    assert typo not in entity_ids("2026-SC-204") | entity_ids("2026-SC-205")


def test_near_named_but_distinct_providers_stay_distinct():
    """Sierra Imaging and Open Sierra Imaging are two businesses, not one typo."""
    seed = _seed()
    a = seed["providers"]["sierra_imaging"]
    b = seed["providers"]["open_sierra_imaging"]
    assert a["matter_entity_id"] != b["matter_entity_id"]
    assert a["display_name"] in b["display_name"], "the pair must be near-named, or it does not test what it claims to"


def test_render_is_deterministic():
    """Re-rendering must not change a committed byte, or review cannot trust the diff."""
    before = {p.name: p.read_bytes() for p in sorted(WIRE_DIR.glob("*.json"))}
    sheet = (SEED_DIR / "KEYING-SHEET.md").read_bytes()
    subprocess.run([sys.executable, str(RENDERER)], check=True, capture_output=True)  # noqa: S603 - test re-runs the seed renderer under sys.executable with no arguments
    after = {p.name: p.read_bytes() for p in sorted(WIRE_DIR.glob("*.json"))}
    assert after == before, "rendered payloads drifted; commit the re-render"
    assert (SEED_DIR / "KEYING-SHEET.md").read_bytes() == sheet


def test_keying_sheet_warns_about_every_deliberate_trap():
    """A person keying this will 'fix' the typo unless the sheet tells them not to."""
    sheet = (SEED_DIR / "KEYING-SHEET.md").read_text(encoding="utf-8")
    assert "Valley Helath Plan" in sheet, "the misspelling must appear exactly as keyed"
    assert "KEY THIS MISSPELLING EXACTLY" in sheet
    assert "SAME contact record" in sheet, "the shared-payer instruction must be explicit"
    assert "DIFFERENT businesses" in sheet, "the must-not-merge pair must be called out"
    assert "—" not in sheet, "no em dashes in generated copy"
