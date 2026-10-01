"""The Medicals tab's shapes and the facts a row is built from: pure functions,
no client and no clock, split out of ``medicals_tools`` so that module keeps
the writes and stays under the module-size ceiling."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .matter_resolution import _contact_tokens, _name_tokens

_AMOUNT_RE = re.compile(r"^\d{1,9}(?:\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROV_NAME = re.compile(r"^Providers\[(\d+)\]/Provider/DisplayName$")
_PROV_KEY = re.compile(r"^Providers\[(\d+)\]/(.+)$")
_MAX_NAME = 120
_MAX_ACCOUNT = 60
_INVOICE_KEY = re.compile(r"^Invoices\[(\d+)\]/(.+)$")


def _items(resp: Any) -> list[Any]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ("value", "items", "data", "results"):
            if isinstance(resp.get(key), list):
                return resp[key]
    return []


def layout_values(item: Any) -> dict[str, Any]:
    """A layout item's values as ``{key: value}``. The vendor returns them as a
    list of ``{key, value}`` pairs, sometimes wrapped in a one-element
    ``value`` list; a dict is accepted too."""
    if (
        isinstance(item, dict)
        and isinstance(item.get("value"), list)
        and item["value"]
        and isinstance(item["value"][0], dict)
        and "values" in item["value"][0]
    ):
        item = item["value"][0]
    values = item.get("values") if isinstance(item, dict) else None
    if isinstance(values, dict):
        return dict(values)
    out: dict[str, Any] = {}
    for entry in values or []:
        if isinstance(entry, dict) and "key" in entry:
            out[entry["key"]] = entry.get("value")
    return out


def provider_rows(values: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """``{index: {field: value}}`` for every ``Providers[n]`` row on the tab."""
    rows: dict[int, dict[str, Any]] = {}
    for key, value in values.items():
        match = _PROV_KEY.match(key)
        if match:
            rows.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return rows


def normalize_name(name: Any) -> str:
    """One spelling for a provider name: casefolded, punctuation dropped,
    spaces collapsed. "Northside Imaging Center" and "NORTHSIDE IMAGING
    CENTER." are one facility."""
    if not isinstance(name, str):
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def _same_provider(a: str, b: str) -> bool:
    """Equal after normalization, or one contains the other when both are
    long enough for containment to mean something ("Northside Imaging
    Center" and "Northside Imaging Center, Valley Health")."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 8 and f" {shorter} " in f" {longer} "


def indices_named(rows: dict[int, dict[str, Any]], name: str) -> list[int]:
    return sorted(i for i, row in rows.items() if _same_provider(str(row.get("Provider/DisplayName") or ""), name))


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_charge(raw: Any) -> Decimal | None:
    """A positive amount with at most two decimals, as a STRING. A float is
    refused: "1250.0" is not the figure a bill prints."""
    if not isinstance(raw, str) or not _AMOUNT_RE.match(raw.strip()):
        return None
    try:
        amount = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _problem(
    *, matter_id: str, source_file_id: str, provider_name: str, charge: Any, service_start: str, service_end: str
) -> str | None:
    if not _clean(matter_id) or not _clean(source_file_id):
        return "matter_id and source_file_id are required"
    if not _clean(provider_name) or len(_clean(provider_name)) > _MAX_NAME:
        return f"provider_name must be the facility named on the bill, up to {_MAX_NAME} characters"
    if parse_charge(charge) is None:
        return 'charge must be the bill\'s figure as a string with at most two decimals, e.g. "1250.00"'
    for label, value in (("service_start", service_start), ("service_end", service_end)):
        if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
            return f"{label} must be a date as YYYY-MM-DD"
    if service_end.strip() < service_start.strip():
        return "service_end is before service_start"
    return None


def compose_note(
    *,
    provider_name: str,
    charge: Decimal,
    service_start: str,
    service_end: str,
    account_number: str,
    source: dict[str, Any],
    stamp: Callable[[str], str | None],
) -> str:
    """The row's note, from the facts and the ledger's record of the source.
    Composed here so there is no free-text argument, and stamped so a person
    reading the tab can tell the Operator's row from a colleague's."""
    pages = f"pages {source['first_page']}-{source['last_page']}"
    read = "read from a scan" if source.get("from_scan") else "read from the document's text"
    span = service_start if service_start == service_end else f"{service_start} to {service_end}"
    account = f" Account {account_number}." if account_number else ""
    text = (
        f"From {source['file_name']} ({pages} of the scanned mail), {read}: {provider_name} billed "
        f"{charge} for service {span}.{account} Check the figure against the bill before relying on it."
    )
    return stamp(text) or text


def _row_values(
    index: int,
    *,
    charge: Decimal,
    service_start: str,
    service_end: str,
    account_number: str,
    note: str,
    description: str,
) -> dict[str, str]:
    prefix = f"Providers[{index}]/"
    values = {
        prefix + "Invoices[0]/InitialInvoiceAmount": f"{charge:.2f}",
        prefix + "Invoices[0]/ServiceStartDate": service_start,
        prefix + "Invoices[0]/ServiceEndDate": service_end,
        prefix + "Invoices[0]/Description": description,
        prefix + "Note": note,
    }
    if account_number:
        values[prefix + "AccountNumber"] = account_number
    return values


def _num(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def compare(want: dict[str, str], got: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{key: {want, got}}`` for every written field that did not stick."""
    bad: dict[str, dict[str, Any]] = {}
    for key, wanted in want.items():
        actual = got.get(key)
        if key.endswith("Amount"):
            ok = _num(actual) is not None and _num(actual) == _num(wanted)
        elif key.endswith("Date"):
            ok = str(actual or "")[:10] == wanted
        else:
            ok = str(actual or "").strip() == wanted.strip()
        if not ok:
            bad[key] = {"want": wanted, "got": actual}
    return bad


def _contact_label(contact: dict[str, Any]) -> dict[str, Any]:
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    company = contact.get("company") if isinstance(contact.get("company"), dict) else {}
    name = (
        " ".join(p for p in (person.get("firstName"), person.get("lastName")) if p)
        or company.get("name")
        or contact.get("name")
        or "(unnamed)"
    )
    return {"id": contact.get("id"), "name": name}


def prefer_exact(candidates: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """The contacts whose own name IS the provider's, when any is.

    The contact search matches on tokens, so "Northside Imaging Center" also
    returns "Valley Northside Imaging Center" (a client tenant, 2026-10-01):
    every word is in it. Two records then read as "several", and a
    bill with an exact match in the firm's contacts was left unkeyed. An exact
    name (after the one normalization) narrows the list; with none exact, the
    list stands as it was, and several still means several."""
    want = normalize_name(name)
    exact = [c for c in candidates if normalize_name(_contact_label(c)["name"]) == want]
    return exact or candidates


def client_positions(clients: list[dict[str, Any]], patient_name: str) -> list[int]:
    """The positions, in the matter's client order, of the clients whose
    record carries every token of the bill's patient name ("QUILL, ROSA"
    reads as Rosa Quill). A tab's ``parentIndex`` is that position."""
    want = {t.lower() for t in _name_tokens(patient_name)}
    if not want:
        return []
    return [i for i, contact in enumerate(clients) if want <= _contact_tokens(contact)]


def invoice_lines(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """``{n: {field: value}}`` for every ``Invoices[n]`` line on one provider row."""
    lines: dict[int, dict[str, Any]] = {}
    for key, value in row.items():
        match = _INVOICE_KEY.match(key)
        if match:
            lines.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return lines


def same_bill_on_row(row: dict[str, Any], charge: Decimal, service_start: str) -> int | None:
    """The invoice line already carrying this bill (same amount AND same first
    date of service), or None. That is a repeat of a bill already keyed, and it
    is never keyed twice."""
    for n, line in sorted(invoice_lines(row).items()):
        if (
            _num(line.get("InitialInvoiceAmount")) == charge
            and str(line.get("ServiceStartDate") or "")[:10] == service_start
        ):
            return n
    return None


def invoice_line_values(
    index: int, line: int, *, charge: Decimal, service_start: str, service_end: str, description: str
) -> dict[str, str]:
    """One further bill on a provider row already on the tab: its own line."""
    prefix = f"Providers[{index}]/Invoices[{line}]/"
    return {
        prefix + "InitialInvoiceAmount": f"{charge:.2f}",
        prefix + "ServiceStartDate": service_start,
        prefix + "ServiceEndDate": service_end,
        prefix + "Description": description,
    }
