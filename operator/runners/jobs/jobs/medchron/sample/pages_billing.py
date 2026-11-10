"""Billing page templates: claim forms, itemized statements, EOBs, lien
itemizations. Every page also returns its billing TRANSCRIPTION (the shape
``stages/billing.py`` records for a page it read), so the answer key states
exactly what is printed, and every printed total is the sum of the printed
lines, computed here in cents, never typed."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .model import Page, us_date
from .pages import Ctx

ROWS_PER_PAGE = 18


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def cents(x: Any) -> int:
    return round(float(x) * 100)


def _chunk(cx: Ctx, doc_type: str, rows: list[list[Any]], *, provider: str = "") -> dict[str, Any]:
    dates = sorted(str(r[0]) for r in rows if r and r[0])
    return {
        "doc_type": doc_type,
        "provider": provider or cx.provider.bill_name,
        "patient": cx.patient["name"],
        "date_first": us_date(dates[0]) if dates else None,
        "date_last": us_date(dates[-1]) if dates else None,
        "printed_totals": [],
        "printed_payments": [],
        "line_items": [],
        "notes": "",
    }


def _header(cx: Ctx, form: str) -> list[str]:
    p = cx.patient
    return [
        form,
        "",
        f"Patient: {p['name']}    DOB: {p['dob']}    Sex: {p.get('sex', '')}",
        f"Billing provider: {cx.provider.bill_name}, {cx.provider.address}",
        "",
    ]


def cms1500(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """One claim form per claim group, up to six service lines (box 24)."""
    pages = []
    icd = ", ".join(str(x) for x in p.get("icd") or [])
    for group in p.get("claims") or []:
        lines = _header(cx, "HEALTH INSURANCE CLAIM FORM (CMS-1500)")
        lines += [
            f"21. Diagnosis (ICD-10): {icd}",
            "",
            "24A. Date of service   24D. CPT/HCPCS   Description              24F. Charges",
        ]
        chunk = _chunk(cx, "MEDICAL_BILL", group)
        total = 0
        for d, code, desc, amt in group:
            c = cents(amt)
            total += c
            lines.append(f"{us_date(str(d)):<23}{str(code):<17}{str(desc)[:24]:<25}{money(c)}")
            chunk["line_items"].append(
                {"date": us_date(str(d)), "description": f"{code} {desc}", "charge": money(c), "page": 0}
            )
        lines += ["", f"28. Total charge: {money(total)}", "31. Signature of physician or supplier: on file"]
        chunk["printed_totals"].append({"label": "28. Total Charge", "amount": money(total), "page": 0})
        pages.append(Page(cx.doc_id, "cms1500", "", lines, bill=chunk))
    return pages


def ub04(p: dict[str, Any], cx: Ctx) -> list[Page]:
    rows = [list(r) for r in p.get("lines") or []]
    lines = _header(cx, "UB-04 UNIFORM BILL")
    lines += [f"6. Statement covers period: {us_date(str(rows[0][0]))} to {us_date(str(rows[-1][0]))}", ""]
    lines += ["42. Rev   44. HCPCS   43. Description                  45. Date     47. Total charges"]
    chunk = _chunk(cx, "MEDICAL_BILL", rows)
    total = 0
    for d, rev, code, desc, amt in rows:
        c = cents(amt)
        total += c
        lines.append(f"{str(rev):<10}{str(code):<12}{str(desc)[:32]:<33}{us_date(str(d)):<13}{money(c)}")
        chunk["line_items"].append(
            {"date": us_date(str(d)), "description": f"{rev} {desc}", "charge": money(c), "page": 0}
        )
    lines += ["", f"0001  TOTAL CHARGES  {money(total)}"]
    chunk["printed_totals"].append({"label": "Total Charges", "amount": money(total), "page": 0})
    return [Page(cx.doc_id, "ub04", "", lines, bill=chunk)]


def statement(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """An itemized account ledger: the provider's own account-wide total, the
    figure the billing chart prefers over a sum of claim forms."""
    rows = [list(r) for r in p.get("lines") or []]
    pays = [list(r) for r in p.get("payments") or []]
    total = sum(cents(r[3]) for r in rows)
    paid = sum(cents(r[2]) for r in pays)
    pages = []
    for k in range(0, len(rows), ROWS_PER_PAGE):
        part = rows[k : k + ROWS_PER_PAGE]
        lines = _header(cx, "ITEMIZED STATEMENT OF ACCOUNT")
        lines += ["Date of service   Code     Description                         Charge"]
        chunk = _chunk(cx, "LEDGER", part)
        for d, code, desc, amt in part:
            lines.append(f"{us_date(str(d)):<18}{str(code):<9}{str(desc)[:35]:<36}{money(cents(amt))}")
            chunk["line_items"].append(
                {"date": us_date(str(d)), "description": f"{code} {desc}", "charge": money(cents(amt)), "page": 0}
            )
        if k + ROWS_PER_PAGE >= len(rows):
            lines += ["", f"Total Charges: {money(total)}"]
            chunk["printed_totals"].append({"label": "Total Charges", "amount": money(total), "page": 0})
            for d, label, amt in pays:
                lines.append(f"{us_date(str(d))}  {label}  ({money(cents(amt))})")
                chunk["printed_payments"].append(
                    {"label": str(label), "amount": money(cents(amt)), "date": us_date(str(d)), "page": 0}
                )
            lines.append(f"Balance Due: {money(total - paid)}")
        pages.append(Page(cx.doc_id, "statement", "", lines, bill=chunk))
    return pages


def eob(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """A payer's explanation of benefits: what was billed, allowed and paid.
    Printed by the payer, so its transcription is not a provider bill."""
    pages = []
    for group in p.get("claims") or []:
        lines = [
            "EXPLANATION OF BENEFITS",
            "THIS IS NOT A BILL",
            "",
            f"Member: {cx.patient['name']}    Payer: {p['payer']}",
            f"Provider: {cx.provider.bill_name}",
            "",
            "Service date   Service                    Billed       Allowed      Paid         You owe",
        ]
        chunk = _chunk(cx, "OTHER", [], provider=str(p["payer"]))
        for d, desc, billed, allowed, paid in group:
            owe = cents(allowed) - cents(paid)
            lines.append(
                f"{us_date(str(d)):<15}{str(desc)[:26]:<27}{money(cents(billed)):<13}"
                f"{money(cents(allowed)):<13}{money(cents(paid)):<13}{money(owe)}"
            )
            chunk["printed_payments"].append(
                {"label": f"Plan paid {desc}", "amount": money(cents(paid)), "date": us_date(str(d)), "page": 0}
            )
        pages.append(Page(cx.doc_id, "eob", "", lines, bill=chunk))
    return pages


def split_total(total: int, n: int, cx: Ctx) -> list[int]:
    """``n`` seeded positive amounts in cents that sum to ``total`` exactly."""
    weights = [cx.rng.uniform(0.4, 1.6) for _ in range(n)]
    s = sum(weights)
    parts = [max(1, int(total * w / s)) for w in weights]
    parts[-1] += total - sum(parts)
    return parts


def _spread(p: dict[str, Any], cx: Ctx) -> list[list[Any]]:
    provs = [str(x) for x in p["providers"]]
    day, end = date.fromisoformat(str(p["start"])), date.fromisoformat(str(p["end"]))
    step = max(1, (end - day).days // max(1, int(p["n"])))
    amounts = split_total(cents(p["total"]), int(p["n"]), cx)
    return [
        [min(day + timedelta(days=i * step), end).isoformat(), provs[i % len(provs)], a] for i, a in enumerate(amounts)
    ]


def lien_itemization(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """A lienholder's itemization of what it paid, totalling the lien amount
    already asserted on the matter."""
    rows = [[str(d), str(pv), cents(a)] for d, pv, a in p["rows"]] if p.get("rows") else _spread(p, cx)
    amounts = [r[2] for r in rows]
    pages = []
    for k in range(0, len(rows), ROWS_PER_PAGE):
        lines = [str(p["heading"]), "", f"Beneficiary: {cx.patient['name']}", ""]
        lines += ["Service date   Provider                                   Amount paid"]
        lines += [f"{us_date(d):<15}{pv[:42]:<43}{money(a)}" for d, pv, a in rows[k : k + ROWS_PER_PAGE]]
        chunk = _chunk(cx, "LIEN_SUBROGATION", rows[k : k + ROWS_PER_PAGE], provider=str(p["lienholder"]))
        if k + ROWS_PER_PAGE >= len(rows):
            lines += ["", f"Total lien amount: {money(sum(amounts))}", str(p.get("note", ""))]
            chunk["printed_totals"].append({"label": "Total lien amount", "amount": money(sum(amounts)), "page": 0})
        pages.append(Page(cx.doc_id, "lien_itemization", "", lines, bill=chunk))
    return pages


TEMPLATES = {
    "cms1500": cms1500,
    "ub04": ub04,
    "statement": statement,
    "eob": eob,
    "lien_itemization": lien_itemization,
}
