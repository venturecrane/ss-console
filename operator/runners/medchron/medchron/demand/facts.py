"""The matter's structured fields the demand job reads before the documents: the
client's address (the privilege wall's key), the other side's insurer, the date
of loss, the responsible attorney (the default signer), and the Medicals tab's
provider rows and charges (the bill reconciliation). $0.

Read through the connector's own pure readers (``form_letter_facts``,
``medicals_layout``), never a second copy of the vendor's shapes. A read that
FAILED is recorded as an error, never as an empty value: "could not look" must
not read as "none" (the a-failed-read-must-never-look-empty lesson). The wall
fails closed on a missing client address (``pull.wall_reason``).

A seat without a connector client (the tests' fake) supplies
``matter_facts(matter_id)`` itself.
"""

from __future__ import annotations

import re
from typing import Any

SIDE_OTHER = "Defendants"


def _safe(errors: list[str], what: str, fn: Any, default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - recorded by name; the caller sees the error, not a blank
        errors.append(f"{what}: {type(exc).__name__}: {str(exc)[:160]}")
        return default


def _medicals(values: dict[str, Any]) -> list[dict[str, Any]]:
    from smokeball_connector.medicals_layout import invoice_lines, parse_charge, provider_rows

    out = []
    for idx, row in sorted(provider_rows(values).items()):
        name = str(row.get("Provider/DisplayName") or "").strip()
        charges = []
        for _n, line in sorted(invoice_lines(row).items()):
            amount = parse_charge(line.get("InitialInvoiceAmount"))
            if amount is not None:
                charges.append({"amount": str(amount), "start": line.get("ServiceStartDate")})
        out.append({"index": idx, "provider": name, "charges": charges})
    return out


_COUNSEL = re.compile(r"(?i)\b(attorney|counsel|lawyer|law firm)\b")


def defense_counsel(roles: list[dict[str, Any]]) -> list[str]:
    """The other side's counsel of record, by the role and relationship names
    the matter carries (a litigation demand goes to them, not to a carrier)."""
    out = []
    for role in roles:
        if role.get("isOtherSide") is not True:
            continue
        # A slot with no contact behind it is an empty field, not counsel of record.
        filled = [role] + [r for r in role.get("relationships") or [] if isinstance(r, dict)]
        names = [str(x.get("name") or "") for x in filled if x.get("contactId") or isinstance(x.get("contact"), dict)]
        out += [n for n in names if _COUNSEL.search(n)]
    return sorted(set(out))


def from_client(client: Any, matter_id: str) -> dict[str, Any]:
    from smokeball_connector import form_letter_facts as flf

    errors: list[str] = []
    matter = _safe(errors, "matter", lambda: client.get(f"/matters/{matter_id}"), {}) or {}
    layout = _safe(errors, "layouts", lambda: flf.matter_layout_values(client, matter_id), {})
    emails: list[str] = []
    for cid in [c for c in (matter.get("clientIds") or []) if isinstance(c, str)]:
        contact = _safe(errors, "client contact", lambda cid=cid: flf.fetch_contact(client, cid), {})
        e = flf.contact_email(contact)
        if e:
            emails.append(e.lower())
    parties = _safe(errors, "roles", lambda: flf.read_parties(client, matter_id), None)
    insurer = None
    if parties is not None:
        cid, _src = flf.related_contact(parties, layout, side=SIDE_OTHER, relationship="Insurer", client_side=False)
        insurer = flf.contact_name(_safe(errors, "insurer contact", lambda: flf.fetch_contact(client, cid), {}))
    signer = _safe(errors, "signer", lambda: flf.signer_facts(client, matter, flf.load_signers()), {})
    dol = flf.date_of_loss(layout)
    name = _safe(errors, "client contact", lambda: flf.client_name(client, matter), None)
    staff_id = matter.get("personResponsibleStaffId")
    staff = _safe(errors, "responsible attorney", lambda: client.get(f"/staff/{staff_id}"), {}) if staff_id else {}
    staff = staff if isinstance(staff, dict) else {}
    attorney = " ".join(p for p in (staff.get("firstName"), staff.get("lastName")) if isinstance(p, str) and p) or None
    return {
        "responsible_attorney": attorney,
        "defense_counsel": defense_counsel(parties.roles if parties is not None else []),
        "matter_number": str(matter.get("number") or "") or None,
        "client_name": name.value if name is not None else None,
        "client_emails": sorted(set(emails)),
        "insurer": insurer,
        "date_of_loss": dol.value,
        "signer": (signer.get("name").value if signer.get("name") else None),
        "medicals": _safe(errors, "medicals", lambda: _medicals(layout), []),
        "errors": errors,
    }


#: The reads whose failure HOLDS the job (review of #3074): the privilege wall
#: keys on the client's address, and the file names and the cross-check key on
#: the client's name and the matter's number. "Could not look" is not "none".
REQUIRED_READS = ("matter", "client contact")


def blocking_errors(facts: dict[str, Any]) -> list[str]:
    errs = [str(e) for e in facts.get("errors") or [] if str(e).split(":", 1)[0] in REQUIRED_READS]
    if not facts.get("client_name"):
        errs.append("client name: not on the matter's client contact")
    if not facts.get("matter_number"):
        errs.append("matter number: not read")
    return errs


def matter_number(seat: Any, matter_id: str) -> str | None:
    """The number of the matter with this id, read from the system of record.
    None when it cannot be read (the caller holds on None)."""
    own = getattr(seat, "matter_number", None)
    if callable(own):
        got = own(matter_id)
        return str(got) if got else None
    client = getattr(seat, "client", None)
    try:
        m = client.get(f"/matters/{matter_id}") if client is not None else None
    except Exception:  # noqa: BLE001 - an unreadable matter is None, and None holds the job
        return None
    return (str(m.get("number") or "") or None) if isinstance(m, dict) else None


def read(seat: Any, matter_id: str) -> dict[str, Any]:
    own = getattr(seat, "matter_facts", None)
    if callable(own):
        got = own(matter_id)
        return dict(got) if isinstance(got, dict) else {}
    client = getattr(seat, "client", None)
    if client is None:
        return {
            "matter_number": None,
            "responsible_attorney": None,
            "defense_counsel": [],
            "client_name": None,
            "client_emails": [],
            "insurer": None,
            "date_of_loss": None,
            "signer": None,
            "medicals": [],
            "errors": ["the seat backend has no connector client; matter fields not read"],
        }
    return from_client(client, matter_id)
