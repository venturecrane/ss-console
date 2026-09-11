"""Render the Settlement Closeout seed (ss #2455) into its two artifacts.

    python3 operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/render_seed.py

Writes, beside this file:

  KEYING-SHEET.md    what a person enters into the Smokeball staging sandbox.
                     ``layouts/write`` is not a granted scope
                     (vfy_01M0DVZGZVTHN6G911NZFP136A), so there is no API path.
  wire/<number>.json a canned ``GET /matters/{id}/layouts`` payload per matter,
                     for the offline tests.

Both come from ``closeout-seed.yaml`` so the sandbox and the fixtures cannot
drift, which is what lets the read-back reconciliation probe say whether a
mismatch was a misread or a keying slip.

Every key name emitted was observed on real production layouts
(vfy_01M0DXZ33QCTE7R58GF2WJ1ZB1). Do not add a key this repo has not seen on the
wire: an invented field teaches a skill to gate on a branch that can never fire.
"""

from __future__ import annotations

import json
import pathlib
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - the repo ships PyYAML in the operator venv
    sys.stderr.write("PyYAML is required: python3 -m pip install pyyaml\n")
    raise SystemExit(2)

HERE = pathlib.Path(__file__).resolve().parent
SEED = HERE / "closeout-seed.yaml"
DESIGN_ID = "PersonalInjurySettlementDetailsItem"

# A layout value carries no "value" member at all when unset - it is not an empty
# string. The parser has to treat those as absent, so the fixtures reproduce it.
UNSET = object()


def _v(key: str, value=UNSET) -> dict:
    return {"key": key} if value is UNSET else {"key": key, "value": str(value)}


def _money(x) -> str:
    return f"{float(x):.2f}"


def _provider_values(idx: int, prov: dict, ref: dict) -> list[dict]:
    """One provider's flat key/value rows, in the observed key vocabulary."""
    p = f"Providers[{idx}]"
    initial = float(prov.get("invoice_initial", 0) or 0)
    balance = float(prov.get("invoice_balance", 0) or 0)
    paid = float(prov.get("invoice_amount_paid", 0) or 0)
    rows = [
        _v(f"{p}/Provider/DisplayName", ref["display_name"]),
        _v(f"{p}/Provider/MatterEntityId", ref["matter_entity_id"]),
        _v(f"{p}/InvoiceInitialAmount", _money(initial)),
        _v(f"{p}/InvoiceBalance", _money(balance)),
        _v(f"{p}/InvoiceAmountPaidTotal", _money(paid)),
        _v(f"{p}/InvoiceAdjustmentsTotal", _money(0)),
        _v(f"{p}/InvoiceTotalClaim", _money(initial)),
        _v(f"{p}/SummaryTotalClaim", _money(initial)),
        _v(f"{p}/LienAsserted", "true" if prov.get("lien_asserted") else "false"),
        _v(f"{p}/OverrideCalculations", "false"),
        _v(f"{p}/AccountNumber"),
        _v(f"{p}/Note"),
        _v(f"{p}/LienOrBalanceNote"),
        _v(f"{p}/DocumentRequests[]"),
    ]
    if prov.get("lien_amount") is not None:
        rows.append(_v(f"{p}/LienAmount", _money(prov["lien_amount"])))
    else:
        rows.append(_v(f"{p}/LienAmount", _money(0)))
    if prov.get("final_amount") is not None:
        rows.append(_v(f"{p}/FinalAmount", _money(prov["final_amount"])))
    else:
        rows.append(_v(f"{p}/FinalAmount", _money(0)))
    for date_key, seed_key in (("ServiceStartDate", "service_start"), ("ServiceEndDate", "service_end")):
        val = prov.get(seed_key)
        rows.append(_v(f"{p}/{date_key}", val) if val else _v(f"{p}/{date_key}"))
    # the nested invoice level, observed populated on production
    inv = f"{p}/Invoices[0]"
    rows += [
        _v(f"{inv}/InitialInvoiceAmount", _money(initial)),
        _v(f"{inv}/InvoiceBalance", _money(balance)),
        _v(f"{inv}/InvoiceAdjustmentAmount", _money(0)),
        _v(f"{inv}/Description"),
        _v(f"{inv}/Payors[]"),
    ]
    for date_key, seed_key in (("ServiceStartDate", "service_start"), ("ServiceEndDate", "service_end")):
        val = prov.get(seed_key)
        rows.append(_v(f"{inv}/{date_key}", val) if val else _v(f"{inv}/{date_key}"))
    return rows


def _item(matter: dict, plaintiff: dict, providers: dict) -> dict:
    """One per-plaintiff PersonalInjurySettlementDetailsItem."""
    provs = plaintiff.get("providers") or []
    values: list[dict] = [_v("Providers[]"), _v("OtherLiensAndBalances[]")]
    initial_total = balance_total = lien_total = final_total = 0.0
    for idx, prov in enumerate(provs):
        ref = providers[prov["ref"]]
        values += _provider_values(idx, prov, ref)
        initial_total += float(prov.get("invoice_initial", 0) or 0)
        balance_total += float(prov.get("invoice_balance", 0) or 0)
        lien_total += float(prov.get("lien_amount", 0) or 0)
        final_total += float(prov.get("final_amount", 0) or 0)

    values += [
        _v("InvoiceInitialTotal", _money(initial_total)),
        _v("InvoiceBalanceTotal", _money(balance_total)),
        _v("LiensAndBalancesTotalClaimSum", _money(initial_total)),
        _v("LiensAndBalancesLienAmountSum", _money(lien_total)),
        _v("LiensAndBalancesFinalAmountSum", _money(final_total)),
        _v("OtherLiensAndBalancesSummaryTotalClaimSum", _money(0)),
        _v("OtherLiensAndBalancesLienAmountSum", _money(0)),
        _v("OtherLiensAndBalancesFinalAmountSum", _money(0)),
        _v("SettlementAmount", _money(plaintiff.get("settlement_amount", 0) or 0)),
        _v("FirmFee", _money(plaintiff.get("firm_fee", 0) or 0)),
        _v("FirmCosts", _money(plaintiff.get("firm_costs", 0) or 0)),
        _v("FirmFeePercentage", "33.33"),
        _v("FirmFeeType"),
        _v("SettlementStatement/MatterId"),
        _v("SettlementStatement/MatterLineItems[]"),
        _v("SettlementStatement/ProjectedSettlementAmount", _money(plaintiff.get("settlement_amount", 0) or 0)),
        _v("SettlementStatement/ProjectedSettlementDate"),
        _v("SettlementStatement/SettlementDate"),
    ]
    idx = plaintiff["index"]
    item_id = f"seed-{matter['number'].lower()}-p{idx}"
    return {
        "id": item_id,
        "itemId": item_id,
        "parentId": "Plaintiff",
        "index": idx,
        "parentIndex": idx,
        "layoutDesign": {"id": DESIGN_ID, "rel": "Layouts"},
        "layoutDesignId": DESIGN_ID,
        "values": values,
    }


def _keying_sheet(seed: dict) -> str:
    providers = seed["providers"]
    out = [
        "# Settlement Closeout - sandbox keying sheet (ss #2455)",
        "",
        "Generated from `closeout-seed.yaml`. Do not edit this file: edit the seed",
        "and re-run `render_seed.py`, or the sandbox and the tests will disagree.",
        "",
        f"Tenant: **{seed['tenant']}** (staging). Seat: `{seed['seat']}`.",
        "",
        "These are permanent fixture matters in a tenant SMD controls, not probe",
        "artifacts in a client tenant, so no probe stamp or teardown applies.",
        "",
        "## The matters already exist. Only the provider rows need you.",
        "",
        "`seed_closeout.py` created all six matters and their clients by script, at",
        "the right status and dates, and `verify_closeout.py` checked each field",
        "against this same source. **Do not create them again.**",
        "",
        "What no script can do is the **Medicals & Settlement Details** tab. Those are",
        "layout values, and `layouts/write` is in no app's grant: not the seat's",
        "connector, and not the much broader staging seeder that holds `firm/write`",
        "and `invoices/write`. Two independent identities lacking it is why this part",
        "is by hand.",
        "",
        "## What to do",
        "",
        "Open each matter below by its number, go to **Medicals & Settlement Details**,",
        "and add one provider row per line in its table. Everything above the table on",
        "each matter is shown only so you can confirm you have the right file open.",
        "",
        "Leave every field marked `(blank)` actually empty rather than zero: blank and",
        "zero mean different things to the register, and the reconciliation probe",
        "checks which one is there.",
        "",
        "When you are done, tell the session and it runs the read-back probe, which",
        "reports per-field differences so a mismatch says whether the tool misread or",
        "a value slipped during entry.",
        "",
    ]
    for m in seed["matters"]:
        out += _keying_matter(m, providers)
    out += [
        "## Not keyed by hand, on purpose",
        "",
        "These shapes are exercised by offline fixtures generated from the same seed",
        "file, because none of them needs a live tenant and each is a data-entry slip",
        "waiting to happen:",
        "",
    ]
    for f in seed.get("fixture_only", []):
        note = " ".join(str(f["note"]).split())
        out.append(f"- **{f['id']}** - {note}")
    out.append("")
    return "\n".join(out)


def _keying_matter(m: dict, providers: dict) -> list[str]:
    closed = m.get("closed") or "(blank - leave the closed date empty)"
    rows = [
        f"## {m['seed_id']} - `{m['number']}`",
        "",
        f"_{m['shape']}._",
        "",
        "Already created. Confirm you have the right file open:",
        "",
        f"- Title: {m['title']}",
        f"- Status: **{m['status']}**",
        f"- Client(s): {', '.join(m['clients'])}",
        f"- Opened: {m['opened']}   Closed: {closed}",
        "",
    ]
    if m.get("keying_note"):
        rows += [f"> **Read before keying.** {' '.join(str(m['keying_note']).split())}", ""]
    for pl in m["plaintiffs"]:
        provs = pl.get("providers") or []
        label = f"Plaintiff {pl['index'] + 1}" if len(m["plaintiffs"]) > 1 else "Settlement details"
        rows.append(f"**{label}**")
        rows.append("")
        if not provs:
            rows += ["- No provider rows at all. Leave the Medicals section untouched.", ""]
            continue
        rows.append("| Provider | Invoice initial | Invoice balance | Lien asserted | Service start | Service end |")
        rows.append("| --- | --- | --- | --- | --- | --- |")
        notes: list[str] = []
        for prov in provs:
            ref = providers[prov["ref"]]
            rows.append(
                "| {name} | {ini} | {bal} | {lien} | {s} | {e} |".format(
                    name=ref["display_name"],
                    ini=_money(prov.get("invoice_initial", 0) or 0),
                    bal=_money(prov.get("invoice_balance", 0) or 0),
                    lien="yes" if prov.get("lien_asserted") else "no",
                    s=prov.get("service_start") or "(blank)",
                    e=prov.get("service_end") or "(blank)",
                )
            )
            if prov.get("note"):
                notes.append(f"> **{ref['display_name']}:** {' '.join(str(prov['note']).split())}")
        rows.append("")
        if notes:
            rows += notes + [""]
        if pl.get("settlement_amount"):
            rows.append(
                f"Settlement amount: {_money(pl['settlement_amount'])}   Firm fee: {_money(pl.get('firm_fee', 0) or 0)}"
            )
            rows.append("")
    return rows


def main() -> int:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    wire = HERE / "wire"
    wire.mkdir(exist_ok=True)
    for m in seed["matters"]:
        items = [_item(m, pl, seed["providers"]) for pl in m["plaintiffs"]]
        path = wire / f"{m['number']}.json"
        path.write_text(json.dumps(items, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(HERE)}  ({len(items)} layout item(s))")
    sheet = HERE / "KEYING-SHEET.md"
    sheet.write_text(_keying_sheet(seed), encoding="utf-8")
    print(f"wrote {sheet.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
