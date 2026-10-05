"""``add_medicals_provider``: a treating facility on the Medicals tab, no bill.

WHAT IT IS FOR. ``add_medicals_row`` keys a provider from a bill the run
filed. Staff also hand the Operator a client's providers with no bill at all:
Christa (A&P, 2026-10-05) listed one client's prior facilities and the clinic
treating her now, asking that they be on the Medicals tab before records are
ordered. That row is a link to the facility's contact record plus a line of
description ("Prior", "Current - need 5 years of records"); no charge, no dates
of service, no money field of any kind exists on this tool.

WHAT IT WILL NOT DO, each one writing nothing:

* A facility already on the tab (by the tab's own display names, the one
  normalization ``add_medicals_row`` uses) is ``already_present``.
* The firm's contacts holding several records with exactly the facility's
  name, or none with exactly its name but some that could be it, is
  ``needs_contact``: the candidates are listed for the sender to choose from
  ("is Northgate downtown/midtown one facility or two?"). A near match is never
  linked on a guess and never shadowed by a new contact, unless the caller
  passes ``create_new`` after the sender says it is a different facility.
* A matter with several Medicals tabs refuses without ``claimant_index``.

When the contacts hold no candidate at all, the facility is added as a company
named exactly as given; an address given with it is written to that new
contact (never to an existing one). Writes answer 202; the row is found on
read-back as the index that APPEARED, and its description is read back.
"""

from __future__ import annotations

import re
from typing import Any

from .matter_resolution import contacts_by_name
from .medicals_layout import (
    _clean,
    _contact_label,
    compare,
    indices_named,
    layout_values,
    normalize_name,
    provider_rows,
)
from .medicals_tools import LINK_WAITS, VALUE_WAITS, _poll, _refused, _select_tab

_MAX_NAME = 120
_MAX_TEXT = 200
#: "100 Main St, Springfield, CA 90000": the one address shape split into fields.
#: Anything else is written to the first line verbatim, never re-arranged.
_ADDRESS = re.compile(r"^(?P<line1>[^,]+),\s*(?P<city>[^,]+),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def _client() -> Any:
    from . import server

    return server._get_client()


def _address_body(address: str) -> dict[str, str]:
    match = _ADDRESS.match(address)
    if not match:
        return {"addressLine1": address}
    return {
        "addressLine1": match["line1"].strip(),
        "city": match["city"].strip(),
        "state": match["state"].upper(),
        "zipCode": match["zip"],
    }


def _create_company(client: Any, name: str, address: str) -> str | None:
    company: dict[str, Any] = {"name": name}
    if address:
        company["businessAddress"] = _address_body(address)
    try:
        made = client.request("POST", "/contacts", json={"company": company})
    except Exception:  # noqa: BLE001 - the vendor write raises a wide family; a failed create links nothing
        return None
    contact_id = made.get("id") if isinstance(made, dict) else None
    return contact_id if isinstance(contact_id, str) and contact_id else None


def _problem(matter_id: str, provider_name: str, address: str | None, note: str | None) -> str | None:
    if not _clean(matter_id):
        return "matter_id is required"
    if not _clean(provider_name) or len(_clean(provider_name)) > _MAX_NAME:
        return f"provider_name must be the facility's name, up to {_MAX_NAME} characters"
    for label, value in (("address", address), ("note", note)):
        if value is not None and (not isinstance(value, str) or len(value.strip()) > _MAX_TEXT):
            return f"{label} must be text up to {_MAX_TEXT} characters"
    return None


def _choose_contact(client: Any, name: str, create_new: bool) -> dict[str, Any]:
    """``{"id": ...}`` for the one exact match, ``{"create": True}`` when a new
    company is the answer, or a ``needs_contact`` / refusal dict."""
    try:
        found = contacts_by_name(client, name)
    except Exception as exc:  # noqa: BLE001 - SearchFailed and friends: a failed search never reads as "no such contact"
        return _refused(f"the firm's contacts could not be searched ({exc.__class__.__name__})")
    exact = [c for c in found if normalize_name(_contact_label(c)["name"]) == normalize_name(name)]
    if len(exact) == 1:
        return {"id": str(exact[0].get("id"))}
    if len(exact) > 1 or (found and not create_new):
        return {
            "status": "needs_contact",
            "created": False,
            "provider": name,
            "candidates": [_contact_label(c) for c in (exact or found)],
            "reason": (
                "the firm's contacts hold several records with exactly this name"
                if exact
                else "no contact has exactly this name, but these could be the same facility"
            ),
        }
    return {"create": True}


def add_medicals_provider(
    matter_id: str,
    provider_name: str,
    address: str | None = None,
    note: str | None = None,
    claimant_index: int | None = None,
    create_new: bool = False,
) -> Any:
    """Put ONE treating facility on a matter's Medicals tab with no bill: the
    facility's contact linked as a provider row, and ``note`` (e.g. "Prior",
    "Current - need 5 years of records") as the row's description. Use it when
    a person lists a client's providers. A bill is ``add_medicals_row``'s job.

    ``provider_name`` is the facility as the person wrote it. ONE facility per
    call: if the person's wording could be two facilities ("Northgate downtown
    midtown"), ask them before calling. ``address`` is used only when a new
    contact is created. ``claimant_index`` is needed only on a matter with
    several Medicals tabs, after the sender says which claimant.

    Returns ``status``: ``written`` (``row`` index, ``linked_as``, ``contactId``,
    ``created`` true when the contact was added); ``already_present`` (the
    facility is on the tab; ``row`` and ``linked_as``; nothing changed);
    ``needs_contact`` (``candidates`` from the firm's contacts; nothing written:
    ask the sender which one, then call again with that exact name, or with
    ``create_new`` true if it is a different facility); ``link_not_visible`` or
    ``readback_mismatch`` (reported as is, never retried); or ``refused``
    (``reason``; nothing written)."""
    problem = _problem(matter_id, provider_name, address, note)
    if problem:
        return _refused(problem)
    matter, name = _clean(matter_id), _clean(provider_name)
    client = _client()
    tab = _select_tab(client, matter, claimant_index, "")
    if "status" in tab:
        return tab
    path = f"/matters/{matter}/layouts/{tab['id']}"
    try:
        rows = provider_rows(layout_values(client.get(path)))
    except Exception as exc:  # noqa: BLE001 - a failed read refuses, never "no rows"
        return _refused(f"the Medicals tab could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    present = indices_named(rows, name)
    if present:
        row = rows[present[0]]
        return {
            "status": "already_present",
            "created": False,
            "row": present[0],
            "linked_as": row.get("Provider/DisplayName"),
        }
    chosen = _choose_contact(client, name, create_new)
    if "status" in chosen:
        return chosen
    created = "create" in chosen
    contact_id = _create_company(client, name, _clean(address)) if created else chosen["id"]
    if not contact_id:
        return _refused(f"{name} is not in the firm's contacts and could not be added; nothing was written")
    out = _link_and_describe(client, path, rows, contact_id, _clean(note))
    return {**out, "contactId": contact_id, "created": created, "matter_id": matter}


def _link_and_describe(
    client: Any, path: str, rows: dict[int, dict[str, Any]], contact_id: str, note: str
) -> dict[str, Any]:
    before = set(rows)
    planned = (max(rows) + 1) if rows else 0
    try:
        client.request(
            "POST",
            f"{path}/contacts",
            json={"key": f"Providers[{planned}]/Provider/MatterEntityId", "contactId": contact_id},
        )
    except Exception as exc:  # noqa: BLE001 - a failed link is reported with nothing written after it
        return _refused(f"the facility could not be linked to the tab ({exc.__class__.__name__}: {str(exc)[:200]})")
    values = _poll(client, path, LINK_WAITS, lambda current: bool(set(provider_rows(current)) - before))
    fresh = sorted(set(provider_rows(values)) - before)
    if not fresh:
        return {"status": "link_not_visible", "planned_row": planned}
    row = fresh[0]
    linked_as = provider_rows(values)[row].get("Provider/DisplayName")
    if not note:
        return {"status": "written", "row": row, "linked_as": linked_as}
    want = {f"Providers[{row}]/Invoices[0]/Description": note}
    try:
        client.request("PATCH", path, json={"values": [{"key": k, "value": v} for k, v in want.items()]})
    except Exception as exc:  # noqa: BLE001 - the row exists; say so rather than hide it
        failed = {"_write": {"want": note, "got": f"{exc.__class__.__name__}: {str(exc)[:200]}"}}
        return {"status": "readback_mismatch", "row": row, "linked_as": linked_as, "mismatch": failed}
    values = _poll(client, path, VALUE_WAITS, lambda current: not compare(want, current))
    mismatch = compare(want, values)
    out = {"status": "written" if not mismatch else "readback_mismatch", "row": row, "linked_as": linked_as}
    if mismatch:
        out["mismatch"] = mismatch
    return out


def register(server: Any) -> None:
    """Register the provider write. Called once, from ``attachment_tools.register``."""
    server.tool()(add_medicals_provider)


__all__ = ["add_medicals_provider", "register"]
