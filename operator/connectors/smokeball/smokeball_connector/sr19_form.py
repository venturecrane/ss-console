"""``render_sr19``: the California DMV SR 19C (Financial Responsibility
Information Request), prefilled from the matter, filed for the firm's signer.

WHAT IT IS FOR. "Mail DMV SR19 form (UM Case)" is on every A&P auto file (189
done across the firm's matters). The firm's task form produces the state's
SR 19C, and the firm's own filled requests (2026-04-02, 2026-09-01) ask for
"Insurance Information from File" on the other driver: the requester is the
firm (its name and address), its interest "Driver/owner" with "Attorney for
involved party: Vehicle driver/owner", the client as the driver, and the other
driver as the subject. The request is certified under penalty of perjury by
whoever signs it.

WHAT IT NEVER FILLS. The requester's own name line (left blank above the
firm's name and address, so the person who signs writes their own: the form
can never name one person and be sworn by another), and the whole
certification (date, printed name, signature). Nothing is inferred: the other
driver's date of birth, license number and plate stay blank unless a document
cited through ``cited_facts`` shows them, and only the client's own license
number is accepted that way today.

WHAT IT RETURNS NEVER CARRIES A VALUE. Box names, never what was written.

Classified INTERNAL_WRITE. It mails nothing and pays no fee.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

from . import cited_facts
from . import form_letter_facts as facts
from .form_letters import _read_back
from .library import ResolvedTemplate, list_matter_files, load_library_config, name_matches, resolve_template
from .sr1_form import _address_parts, _birth_date, _other_driver, fill_acroform

DOCUMENT_CLASS = "sr19_form"
DEFAULT_TEMPLATE = "Form - DMV SR19.pdf"
FILE_NAME = "DMV SR19 - for signature.pdf"
LOCATION_KEY = "Matter/CaseDetails/AccidentDetails/AccidentLocation"
CITED_FIELDS = {"driver_license_number"}

#: The boxes the firm's own requests tick, by fully qualified field name
#: (mapped by page position on the state form, pinned by a test).
CHECKS = {
    "Insurance1": "/Yes",  # Section A: Insurance Information from File
    "Check Box1.0.0": "/Yes",  # Section B: involved as Driver/owner
    "Check Box1.3.0": "/Yes",  # Section B: Attorney for involved party, Vehicle driver/owner
    "Check Box5": "/Yes",  # Section D: subject is Driver of other vehicle
}
BOXES: dict[str, str] = {
    "Name/Address1": "the firm's name and address (requester)",
    "Date of Request1": "date of request",
    "AccidentvDate2": "accident date",
    "Location-City 1": "accident location",
    "Client1": "your client",
    "Driver1": "driver of the car your client was in",
    "DLNo 1": "her driver license number",
    "BDay 1": "her birth date",
    "Address1": "her address",
    "Subject of Inquiry": "the other driver's name",
    "Address2": "the other driver's address",
}
CLIENT_COMPLETES = (
    "the requester's own name, above the firm's",
    "the other driver's birth date, license number and plate",
    "the certification: date, printed name and signature",
    "the fee check to the state",
)


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(reason: str) -> dict[str, Any]:
    return {"status": "refused", "fileId": None, "reason": reason}


def load_firm(path: str | None = None) -> tuple[str | None, list[str]]:
    """``form_letters.firm``: the firm's name and mailing lines, as its
    letterhead prints them. Authored, never read off a letter at runtime."""
    block = facts._load_yaml(path).get(facts.CONFIG_BLOCK)
    firm = block.get("firm") if isinstance(block, dict) else None
    if not isinstance(firm, dict):
        return None, []
    name = firm.get("name") if isinstance(firm.get("name"), str) else None
    lines = [str(x).strip() for x in firm.get("address") or [] if str(x).strip()]
    return (name.strip() if name else None), lines


def _one_line(parts: dict[str, str]) -> str | None:
    if not parts:
        return None
    tail = " ".join(p for p in (parts.get("state"), parts.get("zip")) if p)
    return ", ".join(p for p in (parts.get("street"), parts.get("city"), tail) if p)


def gather(client: Any, matter_id: str, cited: Any, today: Any) -> tuple[dict[str, str], cited_facts.Confirmed]:
    """(box values, confirmed cited facts). A box with no value is absent."""
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    ids = [c for c in matter.get("clientIds") or [] if isinstance(c, str)]
    me = facts.fetch_contact(client, ids[0]) if ids else {}
    person = me.get("person") if isinstance(me.get("person"), dict) else None
    other = facts.fetch_contact(client, _other_driver(parties))
    values: dict[str, str] = {}

    def put(box: str, value: Any) -> None:
        if isinstance(value, facts.Fact):
            value = value.value
        if value not in (None, ""):
            values[box] = str(value)

    firm_name, firm_lines = load_firm()
    if firm_name:
        # The first line is left for the requester's own name.
        values["Name/Address1"] = "\n".join(["", firm_name, *firm_lines])
    put("Date of Request1", today.strftime("%m/%d/%Y"))
    put("AccidentvDate2", facts.date_of_loss(layout))
    put("Location-City 1", str(layout.get(LOCATION_KEY) or "").strip())
    if person:  # the driver is a person; an entity client leaves these for one
        name = facts.contact_name(me)
        put("Client1", name)
        put("Driver1", name)
        put("BDay 1", _birth_date(me))
        put("Address1", _one_line(_address_parts(me)))
    if other and isinstance(other.get("person"), dict):
        put("Subject of Inquiry", facts.contact_name(other))
        put("Address2", _one_line(_address_parts(other)))
    confirmed = cited_facts.confirm(client, matter_id, cited, set(CITED_FIELDS))
    if person:
        put("DLNo 1", confirmed.facts["driver_license_number"].value)
    return values, confirmed


def render_sr19(matter_id: str, cited: dict[str, Any] | None = None) -> Any:
    """Prefill the California DMV SR 19C (Financial Responsibility Information
    Request) asking DMV for the OTHER driver's insurance information, the way
    the firm's own requests read, and file it on the matter for the firm's
    signer. Never sign, date or certify it, and never write the requester's
    own name: whoever signs writes it.

    Use it only when the other driver's insurance is unknown (the task calls
    it a UM case). If the matter already names the other driver's insurer,
    do not call it: say so and ask.

    Filled from the matter: the firm's name and address (``form_letters.firm``),
    today as the request date, the accident date and location, the client as
    the driver with her birth date and address, the other driver's name and
    address. ``cited`` may carry ``driver_license_number`` read off her license
    with ``read_document``, accepted only beside its label. Ticks "Insurance
    Information from File", "Driver/owner", "Attorney for: Vehicle
    driver/owner" and "Driver of other vehicle".

    Files ``DMV SR19 - for signature.pdf`` at the matter's root and reads it
    back. Returns ``status``, ``fileId``, ``fileName``, ``filled`` (box names,
    NEVER values), ``left_for_signer``, ``cited_refused``,
    ``same_name_on_matter``. Mails nothing, pays no fee, completes no task."""
    matter = str(matter_id or "").strip()
    if not matter:
        return _refused("matter_id is required")
    client = _client()
    cfg = load_library_config()
    resolved = resolve_template(
        client, dataclasses.replace(cfg, templates={DOCUMENT_CLASS: DEFAULT_TEMPLATE, **cfg.templates}), DOCUMENT_CLASS
    )
    if not isinstance(resolved, ResolvedTemplate):
        return _refused(f"the SR19 form did not resolve ({resolved.reason}); nothing was filed")
    try:
        values, confirmed = gather(client, matter, cited, facts.seat_today())
    except Exception as exc:  # noqa: BLE001 - a record that could not be READ files nothing
        return _refused(f"the matter's record could not be read ({exc.__class__.__name__}); nothing was filed")
    try:
        data = fill_acroform(resolved.bytes, values, CHECKS)
    except Exception as exc:  # noqa: BLE001 - a form pypdf cannot fill is refused, never filed half-made
        return _refused(f"{resolved.name!r} could not be filled ({exc.__class__.__name__}); nothing was filed")
    same = [str(e.get("id")) for e in list_matter_files(client, matter) if name_matches(e, FILE_NAME)]
    result = client.add_file(matter, FILE_NAME, data)
    file_id = result.get("fileId") if isinstance(result, dict) else None
    seen = _read_back(client, matter, str(file_id)) if file_id else None
    return {
        "status": "filed" if seen else "filed_not_visible",
        "fileId": file_id,
        "fileName": FILE_NAME,
        "matterId": matter,
        "filled": sorted(BOXES[b] for b in values if b in BOXES),
        "left_for_signer": sorted(BOXES[b] for b in BOXES if b not in values) + list(CLIENT_COMPLETES),
        "cited_refused": {cited_facts.WORDS.get(k, k): why for k, why in confirmed.refused.items()},
        "same_name_on_matter": same,
        "template": {"name": resolved.name, "fileId": resolved.file_id},
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def register(server: Any) -> None:
    """Register the SR19 tool. Called once, from ``attachment_tools.register``."""
    server.tool()(render_sr19)


__all__ = ["BOXES", "CHECKS", "DEFAULT_TEMPLATE", "FILE_NAME", "gather", "load_firm", "register", "render_sr19"]
