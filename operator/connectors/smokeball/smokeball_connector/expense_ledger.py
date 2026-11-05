"""A matter's expense ledger, read whole, and the duplicate test against it.

Two facts about Smokeball's expense surface drive this module, both proven live
on the pilot's staging tenant (vfy_01M2TB0V9G0WX1CKTJK9Q57DCV):

1. **Deletion is soft.** ``DELETE /matters/{id}/expenses/{id}`` sets
   ``isDeleted: true`` and the row is STILL returned by the list. A reader that
   counts every row counts entries the firm removed, so ``get_expenses`` drops
   them by default and says how many it dropped (``deletedExcluded``). A filter
   that hides rows silently is a suppression channel; one that counts them is a
   view.
2. **A page is not the ledger.** The listing carries no total and no "more"
   flag, so a full page is byte-identical to a truncated one (the same rule
   ``listing.py`` states for files and contacts). The duplicate check therefore
   pages until the vendor returns a short page, and a listing it cannot finish
   is a REFUSAL, never "no duplicate found". "I could not check" and "there is
   nothing there" are different facts, and only one makes it safe to write.

Money is ``Decimal`` throughout. A JSON number is read through ``str`` so the
figure compared is the figure the tenant returned, never a binary float.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

#: The page size the duplicate check asks for. The loop never trusts a page of
#: this size to be the end of the ledger.
PAGE_SIZE = 500

#: A ledger longer than this many pages is not read to the end, and the write
#: it was guarding is refused. 40 pages is 20,000 entries on one matter.
MAX_PAGES = 40

#: Company-form words dropped from the END of a vendor name before matching, so
#: "Acme Records, Inc." and "Acme Records" are one vendor.
_VENDOR_SUFFIXES = frozenset(
    {"inc", "llc", "llp", "ltd", "co", "corp", "corporation", "company", "pc", "pllc", "lp", "pa"}
)

#: Fields a duplicate report carries back per matching row. Nothing else from
#: the ledger is echoed, so a report cannot become a second copy of the ledger.
_REPORT_FIELDS = ("id", "subject", "expenseDate", "finalized")


class ExpenseListingIncomplete(RuntimeError):
    """The ledger could not be read to its end, so a duplicate cannot be ruled out."""


def ledger_rows(resp: Any) -> list[dict[str, Any]]:
    """The expense rows in a response: the HATEOAS ``{"value": [...]}`` envelope
    or a bare list. An unrecognized shape yields nothing."""
    if isinstance(resp, dict):
        items = resp.get("value")
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    if isinstance(resp, list):
        return [i for i in resp if isinstance(i, dict)]
    return []


def is_deleted(row: dict[str, Any]) -> bool:
    return row.get("isDeleted") is True


def drop_deleted(resp: Any) -> Any:
    """Remove soft-deleted rows from a listing and count them.

    The count lands on the envelope as ``deletedExcluded``. A bare-list response
    has nowhere to carry a count, so it is filtered and returned as a list: the
    shape the caller asked for is kept either way."""
    if isinstance(resp, dict) and isinstance(resp.get("value"), list):
        kept = [r for r in resp["value"] if not (isinstance(r, dict) and is_deleted(r))]
        resp["deletedExcluded"] = len(resp["value"]) - len(kept)
        resp["value"] = kept
        return resp
    if isinstance(resp, list):
        return [r for r in resp if not (isinstance(r, dict) and is_deleted(r))]
    return resp


def read_whole_ledger(client: Any, matter_id: str) -> list[dict[str, Any]]:
    """Every live (not soft-deleted) expense row on the matter.

    Pages by the number of rows actually returned, so a vendor that caps a page
    below ``PAGE_SIZE`` is still read forward rather than mistaken for the end.
    Raises :class:`ExpenseListingIncomplete` when the ledger runs past
    ``MAX_PAGES`` or when a page repeats rows already seen (a vendor ignoring
    ``Offset`` would otherwise loop forever or read one page as the ledger).
    Transport and API errors propagate; the caller refuses on them."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    for _ in range(MAX_PAGES):
        page = ledger_rows(client.get(f"/matters/{matter_id}/expenses", Limit=PAGE_SIZE, Offset=offset))
        ids = {str(r.get("id")) for r in page if r.get("id") is not None}
        if page and ids and ids <= seen:
            raise ExpenseListingIncomplete("the expense listing repeated a page; its end could not be established")
        seen |= ids
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return [r for r in rows if not is_deleted(r)]
        offset += len(page)
    raise ExpenseListingIncomplete(f"the expense listing ran past {MAX_PAGES * PAGE_SIZE} entries")


def money(value: Any) -> Decimal | None:
    """A tenant-returned amount as ``Decimal``, or None when it is not a number.

    A JSON float goes through ``str`` so ``12.3`` is ``Decimal("12.3")``, never
    the binary expansion. Booleans are refused (``True`` is an int in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def row_amount(row: dict[str, Any]) -> Decimal | None:
    """The row's total: ``price`` x ``quantity`` (the two fields the live POST
    takes), else a flat ``amount`` if the vendor reports one."""
    price, quantity = money(row.get("price")), money(row.get("quantity"))
    if price is not None and quantity is not None:
        return price * quantity
    return money(row.get("amount"))


def row_date(row: dict[str, Any]) -> str:
    """The row's expense date as ``YYYY-MM-DD`` (the vendor may append a time)."""
    raw = row.get("expenseDate")
    return raw[:10] if isinstance(raw, str) else ""


#: Dates are stripped from a row's text before an invoice number is looked for
#: in it: a bare invoice number like "2026" or "0901" would otherwise match the
#: date every staged entry's description carries, and flag every invoice as a
#: duplicate of every other.
_DATE_SHAPES = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def _row_text(row: dict[str, Any]) -> str:
    parts = [row.get("subject"), row.get("description")]
    return " ".join(p for p in parts if isinstance(p, str)).lower()


def _runs(text: str) -> list[str]:
    """The text's runs of letters and digits, lowercased."""
    return re.findall(r"[a-z0-9]+", text.lower())


def contains_run_sequence(text: str, target: str) -> bool:
    """Do consecutive letter/digit runs of ``text`` concatenate to exactly
    ``target`` (itself letters and digits only)?

    So "INV-2026-001", "INV 2026 001" and "inv2026001" all contain the invoice
    number "inv2026001", while "10012" never contains "1001": a match must start
    and end on a run boundary, so it cannot sit inside a longer number."""
    if not target:
        return False
    runs = _runs(text)
    for i in range(len(runs)):
        joined = ""
        for run in runs[i:]:
            joined += run
            if joined == target:
                return True
            if not target.startswith(joined):
                break
    return False


def invoice_number_key(invoice_number: str) -> str:
    """The invoice number with everything but letters and digits removed."""
    return "".join(_runs(invoice_number))


def vendor_key(vendor: str) -> str:
    """The vendor's name, company-form suffixes dropped from the end, squashed
    to letters and digits: "Acme Records, Inc." is "acmerecords"."""
    words = _runs(vendor)
    while len(words) > 1 and words[-1] in _VENDOR_SUFFIXES:
        words.pop()
    return "".join(words)


def _report(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in _REPORT_FIELDS if k in row}
    amount = row_amount(row)
    out["amount"] = str(amount) if amount is not None else None
    return out


def classify_against_ledger(
    rows: list[dict[str, Any]],
    *,
    vendor: str,
    invoice_number: str,
    invoice_date: str,
    amount: Decimal,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Is this invoice already on the ledger?

    Returns ``("duplicate", rows)`` for an exact match, ``("possible_duplicate",
    rows)`` for a near one, and ``(None, [])`` when the ledger shows neither.

    * **Exact**: the invoice number appears in a row's subject or description,
      or the same vendor, amount AND date appear on one row.
    * **Near**: the same vendor and amount on a different date, or the same
      vendor and date with a different amount. Either is how a re-issued or
      corrected invoice looks, and deciding which it is belongs to a person.

    Both outcomes create nothing. The asymmetry is deliberate: a flagged
    invoice costs the office manager one look, and a double entry costs a
    client a double charge."""
    number_key = invoice_number_key(invoice_number)
    vendor_name = vendor_key(vendor)
    exact: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    for row in rows:
        text = _row_text(row)
        same_number = contains_run_sequence(_DATE_SHAPES.sub(" ", text), number_key)
        same_vendor = contains_run_sequence(text, vendor_name)
        same_amount = row_amount(row) == amount
        same_date = row_date(row) == invoice_date
        if same_number or (same_vendor and same_amount and same_date):
            exact.append(_report(row))
        elif same_vendor and (same_amount or same_date):
            near.append(_report(row))
    if exact:
        return "duplicate", exact
    if near:
        return "possible_duplicate", near
    return None, []
