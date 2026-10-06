"""The requests the firm makes by EMAIL rather than on a form, drafted for the
person to send from their own mailbox. ``render_firm_form_letter`` routes the
names in ``EMAILS`` here; nothing is filed and nothing is sent.

Every value is read from the matter, as for the letters: a fact the record
does not hold prints as ``[Not in the file: what]``, is listed in
``unfilled``, and makes the draft ``incomplete`` rather than ready.
"""

from __future__ import annotations

from typing import Any

from . import form_letter_facts as facts
from .form_letter_facts import Fact
from .form_letters import FORMS, MARKER, FormSpec, _claim_number, _side_word

#: Requests the firm makes by EMAIL, not on a form: the person sends them from
#: their own mailbox, so they carry no signer (their mail signature applies).
#: The med pay ledger request is the firm's own email (2026-08-10, to the
#: client's carrier: subject "Claim <#> | Medical Payment Ledger", "I am
#: writing regarding our client ... please provide a Medical Payments ledger
#: ... for our records. Thank you for your assistance, and please let us know
#: if you need anything further."). Two parts are composed here and nowhere in
#: the firm's email: the greeting, and the ask sentence, which generalizes its
#: one situational sentence (an ambulance bill) from the email's own phrases.
#: "your insured" is dropped: a passenger or household claimant is not.
EMAILS: dict[str, dict[str, str]] = {
    "med_pay_ledger_email": {
        "subject": "Claim {claim_number} | Medical Payment Ledger",
        "body": (
            "Hello,\n\n"
            "I am writing regarding our client, {client_name}. Please provide a Medical Payments ledger "
            "reflecting all payments made under our client's Medical Payments coverage for our records.\n\n"
            "Thank you for your assistance, and please let us know if you need anything further.\n\n"
            "Kind regards,"
        ),
    },
}


def _carrier_email(client: Any, matter_id: str, spec: FormSpec) -> tuple[Fact, str]:
    """The side's carrier email: the insurer contact's, else the adjuster's,
    with the contact named so the person can judge where it goes; and that
    contact in plain words for the reply ("" when there is no email)."""
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    for relationship in ("Insurer", "Adjuster"):
        cid, src = facts.related_contact(
            parties, layout, side=spec.side, relationship=relationship, client_side=spec.client_side
        )
        contact = facts.fetch_contact(client, cid)
        email = facts.contact_email(contact) if contact else None
        if email:
            who = facts.contact_name(contact) or "unnamed"
            source = f"email from the {relationship.lower()} contact {who} ({src})"
            return Fact(email, source), f"the {relationship.lower()} contact {who}"
    return Fact(None, "", f"{_side_word(spec)} insurer email"), ""


def draft_firm_email(client: Any, matter_id: str, form: str) -> dict[str, Any]:
    """One of ``EMAILS`` for the person to send from their own mailbox. Nothing
    is filed or sent. ``drafted`` when every fact was in the file, else
    ``incomplete`` with the markers in place and listed in ``unfilled``."""
    words = EMAILS[form]
    spec = FORMS["med_pay_fax"]  # the client's own carrier, the med pay side
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    address, on = _carrier_email(client, matter_id, spec)
    found = {
        "carrier_email": address,
        "claim_number": _claim_number(layout, spec),
        "client_name": facts.client_name(client, matter),
    }
    values = {k: f.value if f.value is not None else MARKER.format(f.missing) for k, f in found.items()}
    unfilled = [MARKER.format(f.missing) for f in found.values() if f.value is None]
    subject = words["subject"].format(**values)
    body = words["body"].format(**values)
    return {
        "status": "incomplete" if unfilled else "drafted",
        "fileId": None,
        "matterId": matter_id,
        "email": {
            "to": values["carrier_email"],
            "subject": subject,
            "body": body,
        },
        "reply_block": reply_block(values["carrier_email"], subject, body, on, unfilled),
        "unfilled": unfilled,
        "facts_used": {k: f.source for k, f in found.items() if f.value is not None},
    }


def reply_block(to: str, subject: str, body: str, on: str, unfilled: list[str]) -> str:
    """The email as the reply carries it, built here so the reply cannot call
    an incomplete draft ready or run its lines together. The mail renderer
    joins consecutive lines into one paragraph, so every line of the email is
    its own paragraph, a blank line between. Live 2026-10-06: a block the
    model laid out arrived as one run-on paragraph, headed "Ready to send"
    with markers in it."""
    if unfilled:
        head = f"The med pay ledger email is waiting on the file: {', '.join(unfilled)}. As it stands:"
    else:
        head = f"Ready to send from your email (the address is on {on}):"
    paragraphs = [p.replace("\n", " ") for p in body.split("\n\n")]
    return "\n\n".join([head, f"To: {to}", f"Subject: {subject}", *paragraphs])


__all__ = ["EMAILS", "draft_firm_email", "reply_block"]
