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
    return {
        "client_emails": sorted(set(emails)),
        "insurer": insurer,
        "date_of_loss": dol.value,
        "signer": (signer.get("name").value if signer.get("name") else None),
        "medicals": _safe(errors, "medicals", lambda: _medicals(layout), []),
        "errors": errors,
    }


def read(seat: Any, matter_id: str) -> dict[str, Any]:
    own = getattr(seat, "matter_facts", None)
    if callable(own):
        got = own(matter_id)
        return dict(got) if isinstance(got, dict) else {}
    client = getattr(seat, "client", None)
    if client is None:
        return {
            "client_emails": [],
            "insurer": None,
            "date_of_loss": None,
            "signer": None,
            "medicals": [],
            "errors": ["the seat backend has no connector client; matter fields not read"],
        }
    return from_client(client, matter_id)
