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

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from .letter_pages import FILED_DOCS
from .matter_resolution import contacts_by_name
from .medicals_layout import (
    _MAX_ACCOUNT,
    _clean,
    _contact_label,
    _items,
    _problem,
    _row_values,
    client_positions,
    compare,
    compose_note,
    indices_named,
    invoice_line_values,
    invoice_lines,
    layout_values,
    normalize_name,
    parse_charge,
    prefer_exact,
    provider_rows,
    same_bill_on_row,
)

PI_DESIGN = "PersonalInjurySettlementDetailsItem"

#: Read-back waits after the link and after the values PATCH, in seconds.
#: Module-level so a test can shorten them; the vendor applies layout writes
#: asynchronously and the first read after a write is usually stale.
LINK_WAITS: tuple[float, ...] = (2, 3, 5, 8, 12)
VALUE_WAITS: tuple[float, ...] = (2, 3, 5, 8, 12)
SLEEP: Callable[[float], None] = time.sleep


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
    patient_name: str = "",
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

    ``patient_name`` is the patient as the bill prints them ("QUILL, ROSA").
    Always pass it: on a matter with several Medicals tabs (one per claimant)
    it chooses the claimant's own tab, matched against the matter's clients.
    If no client or several match, the call refuses and lists the tabs, and
    the sender is asked which claimant it is; then pass ``claimant_index``.
    Never pick one yourself.

    A provider already on the tab gets this bill as its own invoice line on
    that row, unless a line there already has the same amount and the same
    first date of service, which is the same bill keyed before.

    Returns ``status``: ``written`` (the row exists and read back as written;
    ``index``, ``linked_as`` and ``note`` say what); ``invoice_added`` (the
    provider was on the tab, and this bill is its own new invoice line,
    ``line``, read back); ``readback_mismatch`` (the
    row exists but a field did not read back as written; ``mismatch`` names it,
    nothing was retried or undone); ``already_present`` (this same bill is
    already on the provider's row; ``existing`` gives it and NOTHING was changed);
    ``needs_contact`` (the firm's contacts hold several records that could be
    that provider; ``candidates`` lists them and NOTHING was created). A
    provider the contacts do not hold at all is added as a company named as
    the bill prints it, and the result carries ``contact_created: true``; ``link_not_visible`` (the contact link was accepted but no row
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
    tab = _select_tab(client, matter, claimant_index, _clean(patient_name))
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


def _select_tab(client: Any, matter: str, claimant_index: int | None, patient_name: str) -> dict[str, Any]:
    """The Medicals tab to write, as ``{"id": ...}``, or a refusal dict."""
    try:
        tabs = _pi_items(client, matter)
    except Exception as exc:  # noqa: BLE001 - a failed read is a failed step, never "no tab"
        return _refused(f"the matter's layouts could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    if not tabs:
        return _refused("the matter has no Medicals tab (no personal-injury settlement details layout)")
    listed = [{"claimant_index": t["parentIndex"], "item_id": t["id"]} for t in tabs]
    if len(tabs) > 1 and claimant_index is None and patient_name:
        claimant_index = _claimant_of(client, matter, patient_name)
    if len(tabs) > 1 and claimant_index is None:
        return _refused(
            "the matter has several Medicals tabs, one per claimant, and the bill's patient did not match "
            "exactly one client; pass claimant_index for the claimant this bill is for, after the sender says which",
            tabs=listed,
        )
    if len(tabs) == 1 and claimant_index is None:
        return {"id": tabs[0]["id"]}
    chosen = [t for t in tabs if t["parentIndex"] == claimant_index]
    if len(chosen) != 1:
        return _refused(f"no Medicals tab has claimant_index {claimant_index!r}", tabs=listed)
    return {"id": chosen[0]["id"]}


def _claimant_of(client: Any, matter: str, patient_name: str) -> int | None:
    """The bill's patient's position among the matter's clients, which is the
    ``parentIndex`` of their Medicals tab (a client tenant 2026-10-01: two
    plaintiffs, the bill named the second, whose tab is position 1). None unless exactly one matches,
    or when the record cannot be read: then the sender is asked."""
    try:
        record = client.get(f"/matters/{matter}")
        ids = [cid for cid in (record.get("clientIds") or []) if isinstance(cid, str)]
        clients = [client.get(f"/contacts/{cid}") for cid in ids]
    except Exception:  # noqa: BLE001 - an unread record decides nothing; the refusal asks the sender instead
        return None
    hits = client_positions([c if isinstance(c, dict) else {} for c in clients], patient_name)
    return hits[0] if len(hits) == 1 else None


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
        return _on_existing_row(client, bill, item_id, path, present[0], rows[present[0]])
    try:
        candidates = prefer_exact(contacts_by_name(client, bill.provider), bill.provider)
    except Exception as exc:  # noqa: BLE001 - SearchFailed and friends: a failed search is reported as one
        return _refused(f"the firm's contacts could not be searched ({exc.__class__.__name__})")
    if len(candidates) > 1:
        return {
            "status": "needs_contact",
            "created": False,
            "matter_id": bill.matter,
            "item_id": item_id,
            "provider": bill.provider,
            "candidates": [_contact_label(c) for c in candidates],
            "reason": "the firm's contacts hold several records that could be this provider",
        }
    if candidates:
        return _link_and_fill(client, bill, item_id, path, rows, str(candidates[0].get("id")))
    contact_id = _create_provider_contact(client, bill.provider)
    if contact_id is None:
        return _refused(f"{bill.provider} is not in the firm's contacts and could not be added; nothing was written")
    return {**_link_and_fill(client, bill, item_id, path, rows, contact_id), "contact_created": True}


def _create_provider_contact(client: Any, provider: str) -> str | None:
    """Add a provider the firm's contacts do not hold, as a company named exactly
    as the bill prints it, and return its id. Reached ONLY when the broad token
    search returned nothing at all: one or more candidates never creates (a
    client tenant, 2026-10-01: two providers on that day's bills were in no
    contact record, and their Medicals rows waited on a person to add them)."""
    try:
        made = client.request("POST", "/contacts", json={"company": {"name": provider}})
    except Exception:  # noqa: BLE001 - the vendor write raises a wide family; a failed create writes no row
        return None
    contact_id = made.get("id") if isinstance(made, dict) else None
    return contact_id if isinstance(contact_id, str) and contact_id else None


def _on_existing_row(
    client: Any, bill: _Bill, item_id: str, path: str, index: int, row: dict[str, Any]
) -> dict[str, Any]:
    """The provider is on the tab. The same bill already keyed there is left
    alone; a further bill from that provider is its own invoice line, read back.
    (A client tenant, 2026-10-01: one imaging center billed two studies on one
    day, another three; one row each, a line per bill.)"""
    base = {"matter_id": bill.matter, "item_id": item_id, "index": index}
    same = same_bill_on_row(row, bill.amount, bill.start)
    if same is not None:
        line = invoice_lines(row)[same]
        existing = {
            "provider": row.get("Provider/DisplayName"),
            "line": same,
            "charge": line.get("InitialInvoiceAmount"),
            "service_start": line.get("ServiceStartDate"),
            "service_end": line.get("ServiceEndDate"),
        }
        bill_facts = {"charge": f"{bill.amount:.2f}", "service_start": bill.start, "service_end": bill.end}
        return {"status": "already_present", "created": False, **base, "existing": existing, "bill": bill_facts}
    line = 1 + max(invoice_lines(row) or [-1])
    read = "read from a scan" if bill.source.get("from_scan") else "read from the document's text"
    description = f"{bill.source['file_name']}, pages {bill.source['first_page']}-{bill.source['last_page']}, {read}"
    stamped = _stamp()(description) or description
    want = invoice_line_values(
        index, line, charge=bill.amount, service_start=bill.start, service_end=bill.end, description=stamped[:200]
    )
    try:
        client.request("PATCH", path, json={"values": [{"key": k, "value": v} for k, v in want.items()]})
    except Exception as exc:  # noqa: BLE001 - the vendor write raises a wide family; refused, nothing claimed written
        return _refused(f"the invoice line could not be written ({exc.__class__.__name__}: {str(exc)[:200]})")
    values = _poll(client, path, VALUE_WAITS, lambda current: not compare(want, current))
    mismatch = compare(want, values)
    out = {
        "status": "invoice_added" if not mismatch else "readback_mismatch",
        "created": True,
        **base,
        "line": line,
        "linked_as": row.get("Provider/DisplayName"),
        "charge": f"{bill.amount:.2f}",
        "service_start": bill.start,
        "service_end": bill.end,
        "from_scan": bool(bill.source.get("from_scan")),
        "source_file": bill.source["file_name"],
    }
    if mismatch:
        out["mismatch"] = mismatch
    return out


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
    "prefer_exact",
    "provider_rows",
    "register",
]
