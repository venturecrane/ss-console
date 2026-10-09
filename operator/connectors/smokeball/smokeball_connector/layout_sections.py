"""The shapes behind the layout tools: pure functions, no client and no clock.

A Smokeball matter's custom tabs ("layouts") come back as items carrying a
design id and a flat list of ``{key, value}`` pairs, and ONLY the filled ones:
an empty field is absent, never null. Everything here works from that, so a
missing key always reads as "not entered", never as "could not be read".

The Negotiation Details tab is a per-plaintiff item with ten fixed rows::

    Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails/DemandAmount      row 0
    Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails[3]/OfferDate      row 3
    Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails/Details          the summary
    Matter/SettlementNegotiations/SettlementNegotiationsDetails/MinimumSettlement           the minimum

Row 0 carries no index. A row may hold a demand, an offer, or a demand and the
offer that answered it."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

NEG_SECTION = "Negotiation Details"
NEG_MARK = "/SettlementNegotiations/"
_ROW_BASE = "Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails"
DETAILS_KEY = f"{_ROW_BASE}/Details"
MINIMUM_KEY = "Matter/SettlementNegotiations/SettlementNegotiationsDetails/MinimumSettlement"
ROWS = 10
#: Row field name in the tool's arguments -> the vendor's field name.
ROW_FIELDS = {
    "demand_amount": "DemandAmount",
    "demand_date": "DemandDate",
    "offer_amount": "OfferAmount",
    "offer_date": "OfferDate",
    "note": "Note",
}
AMOUNT_FIELDS = ("demand_amount", "offer_amount")
DATE_FIELDS = ("demand_date", "offer_date")
_ROW_KEY = re.compile(r"^Matter/Plaintiffs/SettlementNegotiations/SettlementNegotiationsDetails(?:\[(\d)\])?/(\w+)$")
_AMOUNT = re.compile(r"^\d{1,9}(?:\.\d{1,2})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_NOTE = 1000

#: Identifiers a reply never needs to repeat. Masked unless asked for by name.
_SENSITIVE = re.compile(
    r"(SocialSecurity|SSN|TaxFileNumber|TaxId|TaxNumber|DateOfBirth|BirthDate|DOB|"
    r"DriversLicen[cs]e|DriverLicen[cs]e|BankAccount|CreditCard|CardNumber)",
    re.IGNORECASE,
)
MASK = "[masked: sensitive identifier]"

_SECTION_NAMES = {
    "SettlementNegotiations": NEG_SECTION,
    "InsurancePolicy": "Insurance",
    "HealthInsurerPersonalInjury": "Health insurer",
    "StandardCaseDetails": "Case details",
}


def design_base(item: dict[str, Any]) -> str:
    """The design guid without its matter-type suffix (``<base>_<type>``)."""
    design = item.get("layoutDesign") if isinstance(item.get("layoutDesign"), dict) else {}
    raw = str(design.get("id") or item.get("layoutDesignId") or "")
    return raw.split("_", 1)[0].lower()


def _humanize(word: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word).strip().capitalize()


def section_of(keys: list[str], base: str, negotiation_design: str | None) -> str:
    """A plain label for an item, from its keys; an empty item is labeled only
    when it is the firm's authored negotiation design."""
    if not keys:
        if negotiation_design and base == negotiation_design:
            return f"{NEG_SECTION} (no rows entered)"
        return "Not named (no fields entered)"
    if any(NEG_MARK in k for k in keys):
        return NEG_SECTION
    first = keys[0]
    if first.startswith("Providers["):
        return "Medicals"
    if "Witness" in first:
        return "Witnesses"
    parts = [p for p in first.split("/") if p and p not in ("Matter", "Plaintiffs", "Defendants", "CaseDetails")]
    if not parts:
        return "Other"
    head = re.sub(r"\[\d*\]$", "", parts[0])
    return _SECTION_NAMES.get(head, _humanize(head))


def mask(values: dict[str, Any], include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(values)
    return {k: (MASK if _SENSITIVE.search(k) and v not in (None, "") else v) for k, v in values.items()}


def row_key(row: int, field: str) -> str:
    """The vendor key for a row's field; row 0 has no index."""
    return f"{_ROW_BASE}{'' if row == 0 else f'[{row}]'}/{ROW_FIELDS[field]}"


def filled(value: Any) -> bool:
    return value not in (None, "")


def negotiation_rows(values: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """``{row: {field: value}}`` for every filled field of the ten rows."""
    vendor_to_arg = {v: k for k, v in ROW_FIELDS.items()}
    rows: dict[int, dict[str, Any]] = {}
    for key, value in values.items():
        match = _ROW_KEY.match(key)
        if not match or not filled(value) or match.group(2) not in vendor_to_arg:
            continue
        rows.setdefault(int(match.group(1) or 0), {})[vendor_to_arg[match.group(2)]] = value
    return rows


def negotiation_view(values: dict[str, Any]) -> dict[str, Any]:
    rows = negotiation_rows(values)
    return {
        "rows": [{"row": n, **rows[n]} for n in sorted(rows)],
        "details": values.get(DETAILS_KEY),
        "minimum_settlement": values.get(MINIMUM_KEY),
        "empty_rows": [n for n in range(ROWS) if n not in rows],
    }


def norm_amount(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw).strip()).normalize() if filled(raw) else None
    except InvalidOperation:
        return None


def norm_date(raw: Any) -> str | None:
    return str(raw).strip()[:10] if filled(raw) else None


def norm(field: str, raw: Any) -> Any:
    if field in AMOUNT_FIELDS:
        return norm_amount(raw)
    if field in DATE_FIELDS:
        return norm_date(raw)
    return str(raw).strip() if filled(raw) else None


def parse_row(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one row argument. Returns ``(row, None)`` or ``(None, problem)``.
    An amount is a string or integer with at most two decimals (a float is
    refused: "1250.0" is not the figure a letter prints); a date is
    YYYY-MM-DD."""
    if not isinstance(raw, dict):
        return None, "each row must be an object"
    unknown = set(raw) - set(ROW_FIELDS) - {"row"}
    if unknown:
        return None, f"unknown row fields: {sorted(unknown)}"
    out: dict[str, Any] = {}
    for field in AMOUNT_FIELDS:
        value = raw.get(field)
        if value is None or value == "":
            continue
        text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
        if not isinstance(text, str) or not _AMOUNT.match(text.strip()) or Decimal(text.strip()) <= 0:
            return None, f'{field} must be the figure as written, e.g. "25000" or "25000.00"'
        out[field] = text.strip()
    for field in DATE_FIELDS:
        value = raw.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not _DATE.match(value.strip()):
            return None, f"{field} must be YYYY-MM-DD"
        out[field] = value.strip()
    note = raw.get("note")
    if note not in (None, ""):
        if not isinstance(note, str) or len(note.strip()) > MAX_NOTE:
            return None, f"note must be text up to {MAX_NOTE} characters"
        out["note"] = note.strip()
    if "row" in raw and raw["row"] is not None:
        if not isinstance(raw["row"], int) or isinstance(raw["row"], bool) or not 0 <= raw["row"] < ROWS:
            return None, f"row must be a number from 0 to {ROWS - 1}"
        out["row"] = raw["row"]
    if not any(f in out for f in (*AMOUNT_FIELDS, *DATE_FIELDS)) and "row" not in out:
        return None, "a new row needs a demand or an offer (amount or date); a note alone needs a row number"
    return out, None


def match_existing(row: dict[str, Any], rows: dict[int, dict[str, Any]]) -> tuple[str, int] | None:
    """``("already_present", n)`` when every supplied amount and date equals
    row n's; ``("possible_duplicate", n)`` when an amount equals row n's but its
    date differs. Notes never decide either."""
    supplied = [f for f in (*AMOUNT_FIELDS, *DATE_FIELDS) if f in row]
    if not supplied:
        return None
    for n, existing in sorted(rows.items()):
        if all(norm(f, row[f]) == norm(f, existing.get(f)) for f in supplied):
            return "already_present", n
    for n, existing in sorted(rows.items()):
        for amount, date in (("demand_amount", "demand_date"), ("offer_amount", "offer_date")):
            if amount in row and norm(amount, row[amount]) == norm(amount, existing.get(amount)):
                if norm(date, row.get(date)) != norm(date, existing.get(date)):
                    return "possible_duplicate", n
    return None


__all__ = [
    "AMOUNT_FIELDS",
    "DATE_FIELDS",
    "DETAILS_KEY",
    "MASK",
    "MINIMUM_KEY",
    "NEG_MARK",
    "NEG_SECTION",
    "ROWS",
    "ROW_FIELDS",
    "design_base",
    "filled",
    "mask",
    "match_existing",
    "negotiation_rows",
    "negotiation_view",
    "norm",
    "parse_row",
    "row_key",
    "section_of",
]
