"""The Medicals tab write: one provider row, from one bill this run filed.

WHAT IT IS FOR. A firm's daily post carries medical bills among the letters:
an ambulance statement, an itemized hospital bill, a chiropractor's ledger.
Combined post intake files each on its matter; what the firm then does by hand
is key the facility and the charge onto the matter's Medicals tab, Smokeball's
``PersonalInjurySettlementDetailsItem`` layout, one ``Providers[n]`` row per
treating facility. Demands and settlement statements are built from that tab,
so a row that is wrong is a demand that is wrong, and a bill that never
reaches it is a special the firm never claims.

WHAT OPENS THE WRITE. Not a resolution token: the letter's token was spent by
filing it. A row is opened by ``letter_pages.FILED_DOCS`` holding the
``source_file_id`` the caller names, ON the matter the caller names, filed by
this process from a page range. The row is tied to the bill that was filed,
where it was filed, by the run that filed it; a file id the ledger does not
hold for that matter refuses, so no row can be written on a matter from a
bill the run never put there, and no row can be written from nothing.

THE FIGURE IS THE BILL'S, AND THE ROW SAYS WHERE IT CAME FROM. The charge
arrives as a string with at most two decimals and is written exactly; this
module never totals, rounds or converts. The row's note is COMPOSED HERE from
the facts passed and the ledger's record of the source (file name, pages,
whether those pages were transcribed from paper), so there is no free-text
argument to carry anything else, and a person reading the tab can see that the
Operator wrote the row, from which document, and that a figure read from a
scan is to be checked against the paper before it is relied on. The provider's
``DisplayName`` is the contact record's, set by the tenant at the link; the
bill's spelling is only the search.

EVERY WAY TO BE UNSURE WRITES NOTHING.

* A provider already on the tab (matched by normalized name against the
  tab's own ``DisplayName`` values) is ``already_present``: the existing row
  is reported with its charge and nothing is changed. The firm's row is the
  firm's; a second bill from the same facility is a person's reconciliation.
* A provider the firm's contacts do not hold, or hold more than once, is
  ``needs_contact``: the candidates are named and nothing is created. A row
  is a link to a contact record, and this tool never creates a contact.
* A matter with several Medicals tabs (one per claimant) refuses unless the
  caller names ``claimant_index``, and lists the tabs so the sender can be
  asked which claimant the bill is for. Choosing is the firm's act.
* A write that does not read back as written is reported with the field
  that differs, never retried into place and never undone.

HARD-WON VENDOR RULES, carried from the file-review tooling that filled the
Medicals tab on the firm's files on 2026-09-22 and 09-29:

* A contact link makes an EMPTY row; the money is a separate PATCH.
* The tenant owns the slot a link lands in and names the row after ITS
  contact record, which can differ from the bill's spelling, so the new row
  is found on read-back as the index that APPEARED, never as the planned one.
* Layout writes answer 202; the read-back polls.
* ``InvoiceBalance`` is derived (equal to the charge until a payment is
  recorded), so it is neither written nor compared here.
* Values come back as strings ("4345.16"); amounts compare numerically.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .letter_pages import FILED_DOCS
from .matter_resolution import contacts_by_name

PI_DESIGN = "PersonalInjurySettlementDetailsItem"

#: Read-back waits after the link and after the values PATCH, in seconds.
#: Module-level so a test can shorten them; the vendor applies layout writes
#: asynchronously and the first read after a write is usually stale.
LINK_WAITS: tuple[float, ...] = (2, 3, 5, 8, 12)
VALUE_WAITS: tuple[float, ...] = (2, 3, 5, 8, 12)
SLEEP: Callable[[float], None] = time.sleep

_AMOUNT_RE = re.compile(r"^\d{1,9}(?:\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROV_NAME = re.compile(r"^Providers\[(\d+)\]/Provider/DisplayName$")
_PROV_KEY = re.compile(r"^Providers\[(\d+)\]/(.+)$")
_MAX_NAME = 120
_MAX_ACCOUNT = 60


def _client() -> Any:
    from . import server

    return server._get_client()


def _stamp() -> Callable[[str], str | None]:
    from . import server

    return server._stamp


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "refused", "created": False, "reason": reason}
    out.update(extra)
    return out


# ---- Reading the tab ------------------------------------------------------


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


def _pi_items(client: Any, matter_id: str) -> list[dict[str, Any]]:
    """The Medicals tabs on a matter: one per claimant, each with its
    ``parentIndex`` (the claimant's position among the matter's clients)."""
    out = []
    for item in _items(client.get(f"/matters/{matter_id}/layouts")):
        if not isinstance(item, dict):
            continue
        design = item.get("layoutDesign") if isinstance(item.get("layoutDesign"), dict) else {}
        design_id = str(design.get("id") or item.get("layoutDesignId") or "")
        if PI_DESIGN in design_id and isinstance(item.get("id"), str):
            out.append({"id": item["id"], "parentIndex": item.get("parentIndex")})
    return out


def _poll(client: Any, path: str, waits: tuple[float, ...], until: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for wait in waits:
        SLEEP(wait)
        values = layout_values(client.get(path))
        if until(values):
            break
    return values


# ---- The facts ------------------------------------------------------------


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


# ---- The write ------------------------------------------------------------


def add_medicals_row(
    matter_id: str,
    source_file_id: str,
    provider_name: str,
    charge: str,
    service_start: str,
    service_end: str,
    account_number: str = "",
    claimant_index: int | None = None,
) -> Any:
    """Put ONE provider row on a matter's Medicals tab from a medical bill
    this run filed on that matter. Classified INTERNAL_WRITE: a write into the
    firm's own record that bills nobody and sends nothing.

    ``source_file_id`` is the ``fileId`` ``file_attachment_pages_to_matter``
    returned for the bill, on this same ``matter_id``, in this run. Any other
    file id is refused and nothing is written: a row is tied to the bill that
    was filed, where it was filed. There is no argument that asserts a matter.

    Pass what the bill prints and nothing else: ``provider_name`` (the
    facility as the bill names it), ``charge`` (this bill's total charges as
    a string with at most two decimals, e.g. "4345.16"; never a figure you
    added up, rounded or converted), ``service_start`` and ``service_end``
    (YYYY-MM-DD; the same date for one visit), and ``account_number`` when the
    bill prints one. A bill that prints no total, or no dates of service, has
    no row: say so in the reply and put nothing here.

    ``claimant_index`` is needed only when the matter has several Medicals
    tabs (one per claimant); the refusal lists them so the sender can be
    asked which claimant the bill is for. Never pick one.

    Returns ``status``: ``written`` (the row exists and read back as written;
    ``index``, ``linked_as`` and ``note`` say what); ``readback_mismatch`` (the
    row exists but a field did not read back as written; ``mismatch`` names it,
    nothing was retried or undone); ``already_present`` (that provider is
    already on the tab; ``existing`` gives its row and NOTHING was changed);
    ``needs_contact`` (the firm's contacts hold no record for that provider, or
    more than one; ``candidates`` lists what was found and NOTHING was
    created); ``link_not_visible`` (the contact link was accepted but no row
    appeared in time; nothing further was written); or ``refused`` (``reason``
    says why and NOTHING was written)."""
    problem = _problem(
        matter_id=matter_id,
        source_file_id=source_file_id,
        provider_name=provider_name,
        charge=charge,
        service_start=service_start,
        service_end=service_end,
    )
    if problem:
        return _refused(problem)
    matter = _clean(matter_id)
    source = FILED_DOCS.lookup(matter, _clean(source_file_id))
    if source is None:
        return _refused(
            "source_file_id is not a document this run filed on that matter; "
            "file the bill's pages with file_attachment_pages_to_matter first and pass the fileId it returned"
        )
    amount = parse_charge(charge)
    assert amount is not None  # _problem checked it
    bill = _Bill(
        matter=matter,
        provider=_clean(provider_name),
        amount=amount,
        start=service_start.strip(),
        end=service_end.strip(),
        account=_clean(account_number)[:_MAX_ACCOUNT],
        source=source,
    )
    client = _client()
    tab = _select_tab(client, matter, claimant_index)
    if "status" in tab:
        return tab
    return _write_row(client, bill, tab["id"])


@dataclass(frozen=True)
class _Bill:
    """The facts one call carries, validated, plus the ledger's source record."""

    matter: str
    provider: str
    amount: Decimal
    start: str
    end: str
    account: str
    source: dict[str, Any]


def _select_tab(client: Any, matter: str, claimant_index: int | None) -> dict[str, Any]:
    """The Medicals tab to write, as ``{"id": ...}``, or a refusal dict."""
    try:
        tabs = _pi_items(client, matter)
    except Exception as exc:  # noqa: BLE001 - a failed read is a failed step, never "no tab"
        return _refused(f"the matter's layouts could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    if not tabs:
        return _refused("the matter has no Medicals tab (no personal-injury settlement details layout)")
    listed = [{"claimant_index": t["parentIndex"], "item_id": t["id"]} for t in tabs]
    if len(tabs) > 1 and claimant_index is None:
        return _refused(
            "the matter has several Medicals tabs, one per claimant; pass claimant_index for the claimant "
            "this bill is for, after the sender says which",
            tabs=listed,
        )
    if len(tabs) == 1 and claimant_index is None:
        return {"id": tabs[0]["id"]}
    chosen = [t for t in tabs if t["parentIndex"] == claimant_index]
    if len(chosen) != 1:
        return _refused(f"no Medicals tab has claimant_index {claimant_index!r}", tabs=listed)
    return {"id": chosen[0]["id"]}


def _write_row(client: Any, bill: _Bill, item_id: str) -> dict[str, Any]:
    """Read the tab, stop if the provider is on it, find its contact, then link
    and fill the row. Every early return writes nothing."""
    path = f"/matters/{bill.matter}/layouts/{item_id}"
    try:
        values = layout_values(client.get(path))
    except Exception as exc:  # noqa: BLE001 - the vendor read raises a wide family; a failed read refuses, never "no rows"
        return _refused(f"the Medicals tab could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    rows = provider_rows(values)
    present = indices_named(rows, bill.provider)
    if present:
        row = rows[present[0]]
        return {
            "status": "already_present",
            "created": False,
            "matter_id": bill.matter,
            "item_id": item_id,
            "index": present[0],
            "existing": {
                "provider": row.get("Provider/DisplayName"),
                "charge": row.get("Invoices[0]/InitialInvoiceAmount"),
                "service_start": row.get("Invoices[0]/ServiceStartDate") or row.get("ServiceStartDate"),
                "service_end": row.get("Invoices[0]/ServiceEndDate") or row.get("ServiceEndDate"),
            },
            "bill": {"charge": f"{bill.amount:.2f}", "service_start": bill.start, "service_end": bill.end},
        }
    try:
        candidates = contacts_by_name(client, bill.provider)
    except Exception as exc:  # noqa: BLE001 - SearchFailed and friends: a failed search is reported as one
        return _refused(f"the firm's contacts could not be searched ({exc.__class__.__name__})")
    if len(candidates) != 1:
        return {
            "status": "needs_contact",
            "created": False,
            "matter_id": bill.matter,
            "item_id": item_id,
            "provider": bill.provider,
            "candidates": [_contact_label(c) for c in candidates],
            "reason": (
                "the firm's contacts hold no record for this provider"
                if not candidates
                else "the firm's contacts hold several records that could be this provider"
            ),
        }
    return _link_and_fill(client, bill, item_id, path, rows, str(candidates[0].get("id")))


def _link_and_fill(
    client: Any, bill: _Bill, item_id: str, path: str, rows: dict[int, dict[str, Any]], contact_id: str
) -> dict[str, Any]:
    """The two writes: link the contact (an empty row appears where the tenant
    puts it), then PATCH that row's values and read them back."""
    before = set(rows)
    planned = (max(rows) + 1) if rows else 0
    try:
        client.request(
            "POST",
            f"{path}/contacts",
            json={"key": f"Providers[{planned}]/Provider/MatterEntityId", "contactId": contact_id},
        )
    except Exception as exc:  # noqa: BLE001 - the link POST raises a wide family; a failed link is reported with nothing written after it
        return _refused(f"the provider could not be linked to the tab ({exc.__class__.__name__}: {str(exc)[:200]})")
    values = _poll(client, path, LINK_WAITS, lambda current: bool(set(provider_rows(current)) - before))
    fresh = sorted(set(provider_rows(values)) - before)
    base = {"created": True, "matter_id": bill.matter, "item_id": item_id}
    if not fresh:
        return {"status": "link_not_visible", **base, "planned_index": planned, "contact_id": contact_id}
    index = fresh[0]
    linked_as = provider_rows(values)[index].get("Provider/DisplayName")
    note = compose_note(
        provider_name=bill.provider,
        charge=bill.amount,
        service_start=bill.start,
        service_end=bill.end,
        account_number=bill.account,
        source=bill.source,
        stamp=_stamp(),
    )
    want = _row_values(
        index,
        charge=bill.amount,
        service_start=bill.start,
        service_end=bill.end,
        account_number=bill.account,
        note=note,
        description=f"{bill.provider}, {bill.source['file_name']}"[:200],
    )
    try:
        client.request("PATCH", path, json={"values": [{"key": k, "value": v} for k, v in want.items()]})
    except Exception as exc:  # noqa: BLE001 - the empty row exists; say so rather than hide it
        failed = {"_write": {"want": "the row's values", "got": f"{exc.__class__.__name__}: {str(exc)[:200]}"}}
        return {"status": "readback_mismatch", **base, "index": index, "linked_as": linked_as, "mismatch": failed}
    values = _poll(client, path, VALUE_WAITS, lambda current: not compare(want, current))
    mismatch = compare(want, values)
    out = {
        "status": "written" if not mismatch else "readback_mismatch",
        **base,
        "index": index,
        "linked_as": linked_as,
        "charge": f"{bill.amount:.2f}",
        "service_start": bill.start,
        "service_end": bill.end,
        "note": note,
        "from_scan": bool(bill.source.get("from_scan")),
        "source_file": bill.source["file_name"],
        "extra_rows": fresh[1:],
    }
    if mismatch:
        out["mismatch"] = mismatch
    return out


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


def register(server: Any) -> None:
    """Register the Medicals write onto the connector's server. Called once,
    from ``attachment_tools.register``."""
    server.tool()(add_medicals_row)


__all__ = [
    "LINK_WAITS",
    "PI_DESIGN",
    "VALUE_WAITS",
    "add_medicals_row",
    "compare",
    "compose_note",
    "indices_named",
    "layout_values",
    "normalize_name",
    "parse_charge",
    "provider_rows",
    "register",
]
