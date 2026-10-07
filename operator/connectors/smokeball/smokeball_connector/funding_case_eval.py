"""``render_funding_case_eval``: a litigation-funding company's case evaluation
form (cash advance application), prefilled from the matter, filed for the firm
to finish.

WHAT IT IS FOR. A firm that asks a funder to advance money against a case sends
the funder's own intake form, and most of it repeats what the file already
holds: the attorney and firm, the client's contact block, the date of loss, the
carriers on each side with their claim numbers and adjusters, and who she has
seen. A&P asked the Operator to fill that part "with the data in the file so
far" (2026-10-06). The firm's copy of the form sits in its Document Library
(``funding_case_eval`` class, ``Form - Funding Case Eval.pdf``), resolved like
every firm form; a form that does not resolve refuses and files nothing.

WHAT IT NEVER FILLS. Everything that is the attorney's judgment or the
client's own account: the amount and kind of funding requested, whether
liability is clear, comparative negligence, causation, what the carriers have
accepted or paid, policy limits, prior accidents, pre-existing conditions,
prior counsel, borrowing, felony history, employment, the accident narrative,
litigation status, and both signatures. Never the Social Security number, even
when the client's contact record carries an identification number. Never a
medical total or a "most recent care" date: bills keyed so far would understate
the case to a funder that sizes its advance from that number.

FILLED MEANS LANDED. ``filled`` is read back off the output PDF, never taken
from what was attempted; a box name the firm's form does not carry refuses the
whole fill (the form was swapped or revised), so the reply can never name a box
as filled that prints blank.

WHAT IT RETURNS NEVER CARRIES A VALUE. Box names, never what was written.

Classified INTERNAL_WRITE. It sends, signs and submits nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
from typing import Any

from . import form_letter_facts as facts
from .form_letters import _read_back
from .letterhead import load_firm_identity
from .library import ResolvedTemplate, list_matter_files, load_library_config, name_matches, resolve_template
from .medicals_layout import layout_values, provider_rows
from .medicals_tools import _pi_items
from .sr1_form import _address_parts, _birth_date, fill_acroform

DOCUMENT_CLASS = "funding_case_eval"
DEFAULT_TEMPLATE = "Form - Funding Case Eval.pdf"
FILE_NAME = "Funding Case Evaluation - prefilled.pdf"
CLAIM_KEY = "Matter/{side}/InsurancePolicy/Claims/Number"
#: A matter type whose name reads as a motor vehicle case ticks "MVA".
MVA_WORDS = ("motor vehicle", "auto")

#: Text box -> what the reply calls it. Every box this tool may fill.
BOXES: dict[str, str] = {
    "Date of Request": "date of request",
    "Attorney Name": "attorney name",
    "Attorney Email": "attorney email",
    "Firm": "firm",
    "Attorney Street Address": "firm street address",
    "Attorney City, State, Zip": "firm city, state and zip",
    "Attorney Phone": "firm phone",
    "Paralegal Name": "paralegal name",
    "Paralegal Email": "paralegal email",
    "Client Name": "client name",
    "Client Street Address": "client street address",
    "Client City, State, Zip": "client city, state and zip",
    "Client Phone (home)": "client home phone",
    "Client Cell/Pager": "client cell phone",
    "Client's Date of Birth": "client date of birth",
    "Date of Loss": "date of loss",
    "Liability Carrier": "other side's carrier",
    "Liability Claim #": "other side's claim number",
    "Liability Adjuster": "other side's adjuster",
    "Liability Phone #": "other side's adjuster phone",
    "Liability Address": "other side's carrier address",
    "Liability City, State, Zip": "other side's carrier city, state and zip",
    "First Party Insurance Carrier": "client's own carrier",
    "First Party Insurance Claim #": "client's own claim number",
    "First Party Insurance Adjuster": "client's own adjuster",
    "First Party Insurance Phone #": "client's own adjuster phone",
    "First Party Insurance Address": "client's own carrier address",
    "First Party Insurance City, State, Zip": "client's own carrier city, state and zip",
    "Provider/Facility 1": "provider 1",
    "Provider/Facility 2": "provider 2",
    "Provider/Facility 3": "provider 3",
    "Provider/Facility 4": "provider 4",
}
#: Checkbox -> what the reply calls it, ticked "/Yes".
CHECKS: dict[str, str] = {"MVA": "case type: motor vehicle"}
PROVIDER_BOXES = ("Provider/Facility 1", "Provider/Facility 2", "Provider/Facility 3", "Provider/Facility 4")

LEFT_FOR_FIRM = (
    "the request type and amount",
    "liability, comparative negligence and causation",
    "citations, the police report and what the carriers have accepted or paid",
    "policy limits, a second layer, uninsured and underinsured motorist coverage and med pay",
    "emergency care, specialties, last date of care and estimated medical expenses",
    "injuries and complaints",
    "how long the firm has represented her, litigation and demand status",
    "the accident description",
    "the attorney signature",
)
LEFT_FOR_CLIENT = (
    "her Social Security number and occupation",
    "seatbelt, prior or later accidents and pre-existing conditions",
    "prior counsel, borrowing against the case and felony history",
    "employment at the time of the accident",
    "her signature",
)


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(reason: str) -> dict[str, Any]:
    return {"status": "refused", "fileId": None, "reason": reason}


def _phone_at(contact: dict[str, Any], key: str) -> str | None:
    """The number stored under exactly ``key`` (``phone`` or ``homePhone`` is
    home, ``cell`` is cell), as "(AAA) NNN-NNNN". Never falls back to another
    kind of number: a cell never prints as a home phone."""
    raw = facts._body(contact).get(key)
    if isinstance(raw, dict) and str(raw.get("number") or "").strip():
        area = str(raw.get("areaCode") or "").strip()
        number = str(raw["number"]).strip()
        return f"({area}) {number}" if area else number
    if isinstance(raw, str):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return None


def _city_line(parts: dict[str, str]) -> str | None:
    tail = " ".join(p for p in (parts.get("state"), parts.get("zip")) if p)
    line = ", ".join(p for p in (parts.get("city"), tail) if p)
    return line or None


def _staff_email(client: Any, staff_id: Any) -> str | None:
    if not isinstance(staff_id, str) or not staff_id:
        return None
    staff = client.get(f"/staff/{staff_id}")
    email = staff.get("email") if isinstance(staff, dict) else None
    return email.strip() if isinstance(email, str) and "@" in email else None


def _is_mva(matter: dict[str, Any]) -> bool:
    """The matter type's own name, as get_matter carries it inline
    (``matterType.name``, e.g. "Motor Vehicle Accident - Plaintiff")."""
    kind = matter.get("matterType")
    name = str(kind.get("name") or "").casefold() if isinstance(kind, dict) else ""
    return any(word in name for word in MVA_WORDS)


def _providers(client: Any, matter_id: str) -> list[str]:
    """Provider names on the client's own Medicals tab, in row order. Exactly
    one tab must be hers (the only tab, or the one at the first claimant's
    position); two candidates return nothing rather than pick one, so another
    claimant's providers can never reach her form."""
    tabs = _pi_items(client, matter_id)
    mine = [t for t in tabs if t.get("parentIndex") in (0, "0", None)] or (tabs if len(tabs) == 1 else [])
    if len(mine) != 1:
        return []
    values = layout_values(client.get(f"/matters/{matter_id}/layouts/{mine[0]['id']}"))
    names = []
    for _, row in sorted(provider_rows(values).items()):
        name = str(row.get("Provider/DisplayName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _company_address_parts(contact: dict[str, Any]) -> dict[str, str]:
    """A carrier's address, business first, then mailing, then residential: the
    order the firm's own carrier letters read (``form_letter_facts.contact_address``)."""
    body = facts._body(contact)
    for key in ("businessAddress", "mailingAddress", "residentialAddress"):
        found = _address_parts({"person": {key: body.get(key)}})
        if found:
            return found
    return {}


def _carrier(
    client: Any, layout: dict[str, Any], parties: facts.Parties, *, side: str, client_side: bool
) -> dict[str, str]:
    """carrier, claim, adjuster, phone (the adjuster's, else the carrier's),
    address and city line for one side. Absent values are absent keys."""
    insurer_id, _ = facts.related_contact(parties, layout, side=side, relationship="Insurer", client_side=client_side)
    adjuster_id, _ = facts.related_contact(parties, layout, side=side, relationship="Adjuster", client_side=client_side)
    insurer = facts.fetch_contact(client, insurer_id)
    adjuster = facts.fetch_contact(client, adjuster_id)
    parts = _company_address_parts(insurer) if insurer else {}
    out = {
        "carrier": facts.contact_name(insurer) if insurer else None,
        "claim": str(layout.get(CLAIM_KEY.format(side=side)) or "").strip() or None,
        "adjuster": facts.contact_name(adjuster) if adjuster else None,
        "phone": (_phone_at(adjuster, "phone") if adjuster else None)
        or (_phone_at(insurer, "phone") if insurer else None),
        "address": parts.get("street") or None,
        "city": _city_line(parts),
    }
    return {k: v for k, v in out.items() if v}


def gather(client: Any, matter_id: str, today: Any) -> tuple[dict[str, str], dict[str, str]]:
    """(text box values, checkbox states). A box with no value is absent."""
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    values: dict[str, str] = {}

    def put(box: str, value: Any) -> None:
        if isinstance(value, facts.Fact):
            value = value.value
        if value not in (None, ""):
            values[box] = str(value)

    put("Date of Request", today.strftime("%m/%d/%Y"))
    put("Attorney Name", facts.staff_signer_facts(client, matter, {})["name"])
    put("Attorney Email", _staff_email(client, matter.get("personResponsibleStaffId")))
    firm = load_firm_identity()
    if firm.authored:
        put("Firm", firm.name)
        put("Attorney Street Address", firm.street)
        put("Attorney City, State, Zip", firm.city_state_zip)
        put("Attorney Phone", firm.phone)
    preparer = facts.preparer_facts(client, matter, None)
    put("Paralegal Name", preparer["name"])
    put("Paralegal Email", preparer["email"])
    ids = [c for c in matter.get("clientIds") or [] if isinstance(c, str)]
    me = facts.fetch_contact(client, ids[0]) if ids else {}
    if isinstance(me.get("person"), dict):  # an entity client has no birth date or home phone
        parts = _address_parts(me)
        put("Client Name", facts.contact_name(me))
        put("Client Street Address", parts.get("street"))
        put("Client City, State, Zip", _city_line(parts))
        put("Client Phone (home)", _phone_at(me, "phone") or _phone_at(me, "homePhone"))
        put("Client Cell/Pager", _phone_at(me, "cell"))
        put("Client's Date of Birth", _birth_date(me))
    put("Date of Loss", facts.date_of_loss(layout))
    for prefix, side, client_side in (
        ("Liability", "Defendants", False),
        ("First Party Insurance", "Plaintiffs", True),
    ):
        found = _carrier(client, layout, parties, side=side, client_side=client_side)
        put(f"{prefix} Carrier", found.get("carrier"))
        put(f"{prefix} Claim #", found.get("claim"))
        put(f"{prefix} Adjuster", found.get("adjuster"))
        put(f"{prefix} Phone #", found.get("phone"))
        put(f"{prefix} Address", found.get("address"))
        put(f"{prefix} City, State, Zip", found.get("city"))
    for box, name in zip(PROVIDER_BOXES, _providers(client, matter_id)):
        put(box, name)
    checks = {"MVA": "/Yes"} if _is_mva(matter) else {}
    return values, checks


def _form_fields(blob: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    return PdfReader(io.BytesIO(blob)).get_fields() or {}


def landed(blob: bytes) -> tuple[set[str], set[str]]:
    """(text boxes carrying a value, checkboxes ticked "/Yes") in a filled PDF."""
    fields = _form_fields(blob)
    text = {n for n in BOXES if n in fields and str(fields[n].get("/V") or "").strip()}
    ticked = {n for n in CHECKS if n in fields and str(fields[n].get("/V") or "") == "/Yes"}
    return text, ticked


def render_funding_case_eval(matter_id: str) -> Any:
    """Prefill the firm's litigation-funding case evaluation form (cash advance
    application) from the matter, and file it on the matter for the firm to
    finish and sign. Use it when the sender asks for the funding application,
    cash advance application or case evaluation form on a matter.

    Filled from the record only: today as the request date; the responsible
    attorney's name and email; the firm's name, address and phone
    (``firm_identity``); the assisting staff member's name and email as the
    paralegal; the client's name, address, home and cell phone (each from its
    own field) and birth date; the date of loss; "MVA" when the matter type is
    a motor vehicle case; each side's carrier, claim number, adjuster, phone and
    address; up to four providers from the client's Medicals tab.

    Never fills the Social Security number, the amount requested, any
    liability, causation or carrier-position answer, limits, medical totals,
    the client's history, employment, the narrative, litigation status or a
    signature: those come back in ``left_for_firm`` and ``left_for_client``.

    Files ``Funding Case Evaluation - prefilled.pdf`` at the matter's root and
    reads it back. Returns ``status`` (``filed``, ``filed_not_visible`` or
    ``refused`` with ``reason``), ``fileId``, ``fileName``, ``filled`` (box
    names read back off the filed PDF, NEVER values), ``not_in_file`` (boxes
    this tool fills that the record had no value for), ``left_for_firm``, ``left_for_client``, ``providers_beyond_four``,
    ``same_name_on_matter``. Sends, signs and submits nothing."""
    matter = str(matter_id or "").strip()
    if not matter:
        return _refused("matter_id is required")
    client = _client()
    cfg = load_library_config()
    resolved = resolve_template(
        client, dataclasses.replace(cfg, templates={DOCUMENT_CLASS: DEFAULT_TEMPLATE, **cfg.templates}), DOCUMENT_CLASS
    )
    if not isinstance(resolved, ResolvedTemplate):
        return _refused(f"the funding case evaluation form did not resolve ({resolved.reason}); nothing was filed")
    try:
        missing = sorted(n for n in (*BOXES, *CHECKS) if n not in _form_fields(resolved.bytes))
    except Exception as exc:  # noqa: BLE001 - an unreadable form is refused, never filed
        return _refused(f"{resolved.name!r} could not be read as a form ({exc.__class__.__name__}); nothing was filed")
    if missing:
        return _refused(
            f"{resolved.name!r} does not match the funding form this tool fills ({len(missing)} boxes not found); "
            "nothing was filed"
        )
    record = client.get(f"/matters/{matter}")
    clients = [c for c in (record.get("clientIds") or []) if isinstance(c, str)] if isinstance(record, dict) else []
    if len(clients) > 1:
        return _refused(
            f"the matter has {len(clients)} clients and the funding form is one client's; "
            "nothing was filed (ask which client, and say the form is filled by hand for a shared matter)"
        )
    try:
        values, checks = gather(client, matter, facts.seat_today())
        providers_total = len(_providers(client, matter))
    except Exception as exc:  # noqa: BLE001 - a record that could not be READ files nothing
        return _refused(f"the matter's record could not be read ({exc.__class__.__name__}); nothing was filed")
    try:
        data = fill_acroform(resolved.bytes, values, checks)
        text, ticked = landed(data)
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
        "filled": sorted(BOXES[b] for b in text) + sorted(CHECKS[c] for c in ticked),
        "not_in_file": sorted(BOXES[b] for b in BOXES if b not in text),
        "left_for_firm": list(LEFT_FOR_FIRM),
        "left_for_client": list(LEFT_FOR_CLIENT),
        "providers_beyond_four": max(0, providers_total - len(PROVIDER_BOXES)),
        "same_name_on_matter": same,
        "template": {"name": resolved.name, "fileId": resolved.file_id},
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def register(server: Any) -> None:
    """Register the funding form tool. Called once, from ``attachment_tools.register``."""
    server.tool()(render_funding_case_eval)


__all__ = [
    "BOXES",
    "CHECKS",
    "DEFAULT_TEMPLATE",
    "FILE_NAME",
    "gather",
    "landed",
    "register",
    "render_funding_case_eval",
]
