"""``render_sr1``: the California DMV SR1, prefilled from the matter, filed for
the client's signature.

WHAT IT IS FOR. "Mail DMV SR1 form" is on every A&P auto file (291 done across
the firm's matters). The SR1 is the DRIVER'S report, certified under penalty of
perjury: the firm's task form produces the blank state form and the client fills
and signs it by hand (both sampled firm SR1s, 2026-09-28 and 09-29). This tool
fills in what the matter already knows so the client checks, completes and
signs instead of starting from a blank page. It never signs, never dates the
certification and never prints the certifying name: those are the client's.

THE FORM. The state's fillable PDF (DMV, AcroForm), resolved from the firm's
Document Library like every firm form (``sr1_form`` class, ``Form - DMV
SR1.pdf``). A form that does not resolve refuses and files nothing.

THE FACTS. From the matter: the client's name, residential address, date of
birth and phone (the client contact), the accident date and location (the
case layout), the other driver's name and address (the other side's contact),
each side's insurer (role relationships) and policy number (the insurance
layouts). From documents, only through ``cited_facts`` (the value must sit
beside its label on the cited document): the driver license number (her
license), and the vehicle's year, make, plate and VIN (the estimate or the
registration). Nothing is inferred: whether she was moving or stopped, the
number of vehicles, the vehicle's owner and the policy period stay blank for
the client.

WHAT IT RETURNS NEVER CARRIES A VALUE. The result names which boxes were filled
and which were left for the client, never what was written in them: a date of
birth or license number in a tool result would sit in the turn that writes the
reply. The filled PDF in the matter is the only place the values live.

Classified INTERNAL_WRITE. It mails and sends nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
from datetime import date
from typing import Any

from . import cited_facts
from . import form_letter_facts as facts
from .form_letter_facts import Fact
from .form_letters import READBACK_WAITS, _read_back  # noqa: F401 - READBACK_WAITS re-exported for tests
from .library import ResolvedTemplate, list_matter_files, load_library_config, name_matches, resolve_template

DOCUMENT_CLASS = "sr1_form"
DEFAULT_TEMPLATE = "Form - DMV SR1.pdf"
FILE_NAME = "DMV SR1 - for client signature.pdf"
CITED_FIELDS = {
    "driver_license_number",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "vehicle_plate",
    "vehicle_vin",
}
LOCATION_KEY = "Matter/CaseDetails/AccidentDetails/AccidentLocation"

#: Box -> what the client sees it called, for the "left for the client" list.
#: Every text box this tool can fill; the certification boxes are absent on
#: purpose (they are never filled).
BOXES: dict[str, str] = {
    "DATE OF ACCIDENT-MONTH": "date of accident",
    "ACCIDENT LOCATION": "accident location",
    "DRIVERS NAME.0": "her name",
    "DRIVER LICENSE NUMBER.0": "her driver license number",
    "DRIVERS STREET ADDRESS.0": "her street address",
    "CITY.0": "her city",
    "STATE2.0": "her state",
    "ZIP CODE.0": "her ZIP code",
    "DATE OF BIRTH-MONTH.0": "her date of birth",
    "HOME PREFIX.0": "her phone area code",
    "HOME PHONE NUMBER.0": "her phone number",
    "REPORTING PARTY'S VEHICLE YEAR": "her vehicle (year and make)",
    "VEHICLE LICENSE PLATE": "her license plate",
    "VEHICLE ID NUMBER111": "her VIN (insurance section)",
    "INSURANCE CO. NAME.22": "her insurance company",
    "POLICY NUMBER.0": "her policy number",
    "DRIVERS NAME.1": "the other driver's name",
    "DRIVERS STREET ADDRESS.1": "the other driver's street address",
    "CITY.1": "the other driver's city",
    "STATE2.1": "the other driver's state",
    "ZIP CODE.1": "the other driver's ZIP code",
    "INSURANCE CO. NAME.221": "the other driver's insurance company",
    "POLICY NUMBER.1": "the other driver's policy number",
    "NAME &  ADDRESS OF INJURED OR DECEASED.0": "injured person's name and address",
}
#: Boxes always left for the client: the facts no record holds. Worded with
#: no dollar figure and no all-capitals word, because the reply quotes this
#: list and its outbound checks hold an untraced dollar amount and a capitals
#: "emphasis" word (rehearsal 2026-10-06: the agent had to reword two lines).
CLIENT_COMPLETES = (
    "number of vehicles",
    "time of accident",
    "moving / stopped / parked",
    "driving for employer",
    "the damages box",
    "vehicle owner",
    "each insurance company's code number",
    "policy periods",
    "policy holder name",
    "the other driver's license number, date of birth and vehicle",
    "the certification: date, printed name and signature",
)


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(reason: str) -> dict[str, Any]:
    return {"status": "refused", "fileId": None, "reason": reason}


def _address_parts(contact: dict[str, Any]) -> dict[str, str]:
    body = facts._body(contact)
    for key in ("residentialAddress", "mailingAddress", "businessAddress"):
        addr = body.get(key)
        if isinstance(addr, dict) and str(addr.get("addressLine1") or "").strip():
            street = " ".join(
                str(addr.get(k) or "").strip()
                for k in ("addressLine1", "addressLine2")
                if str(addr.get(k) or "").strip()
            )
            return {
                "street": street,
                "city": str(addr.get("city") or "").strip(),
                "state": str(addr.get("state") or "").strip(),
                "zip": str(addr.get("zipCode") or "").strip(),
            }
    return {}


def _phone(contact: dict[str, Any]) -> tuple[str, str] | None:
    body = facts._body(contact)
    for key in ("cell", "phone", "homePhone"):
        raw = body.get(key)
        if isinstance(raw, dict) and str(raw.get("number") or "").strip():
            return str(raw.get("areaCode") or "").strip(), str(raw["number"]).strip()
        if isinstance(raw, str) and raw.strip():
            digits = "".join(ch for ch in raw if ch.isdigit())
            if len(digits) == 10:
                return digits[:3], f"{digits[3:6]}-{digits[6:]}"
    return None


def _birth_date(contact: dict[str, Any]) -> str | None:
    raw = str(facts._body(contact).get("birthDate") or "")[:10]
    try:
        return date.fromisoformat(raw).strftime("%m/%d/%Y")
    except ValueError:
        return None


def _other_driver(parties: facts.Parties) -> str | None:
    hits = {facts._contact_of(r) for r in parties.roles if r.get("isOtherSide") is True}
    hits.discard(None)
    return next(iter(hits)) if len(hits) == 1 else None


def gather(client: Any, matter_id: str, cited: Any) -> tuple[dict[str, str], dict[str, str], cited_facts.Confirmed]:
    """(box values, box -> source, confirmed cited facts). A box with no
    value is simply absent; nothing is defaulted."""
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    ids = [c for c in matter.get("clientIds") or [] if isinstance(c, str)]
    me = facts.fetch_contact(client, ids[0]) if ids else {}
    other = facts.fetch_contact(client, _other_driver(parties))
    values: dict[str, str] = {}
    sources: dict[str, str] = {}

    def put(box: str, value: Any, source: str) -> None:
        if isinstance(value, Fact):
            value, source = value.value, value.source or source
        if value not in (None, ""):
            values[box] = str(value)
            sources[box] = source

    dol = facts.date_of_loss(layout)
    put("DATE OF ACCIDENT-MONTH", dol, "")
    put("ACCIDENT LOCATION", str(layout.get(LOCATION_KEY) or "").strip(), f"layout {LOCATION_KEY}")
    # The SR1 is a DRIVER'S report: only a person contact is the driver. A
    # company client (an entity matter) leaves every "her" box for a person.
    person = me.get("person") if isinstance(me.get("person"), dict) else None
    name = facts.contact_name(me) if person else None
    me = me if person else {}
    put("DRIVERS NAME.0", name, "matter client contact")
    mine = _address_parts(me) if me else {}
    for box, part in (
        ("DRIVERS STREET ADDRESS.0", "street"),
        ("CITY.0", "city"),
        ("STATE2.0", "state"),
        ("ZIP CODE.0", "zip"),
    ):
        put(box, mine.get(part), "matter client contact address")
    put("DATE OF BIRTH-MONTH.0", _birth_date(me) if me else None, "matter client contact")
    phone = _phone(me) if me else None
    if phone:
        put("HOME PREFIX.0", phone[0], "matter client contact phone")
        put("HOME PHONE NUMBER.0", phone[1], "matter client contact phone")
    if name:
        injured = name + (f", {mine['street']}, {mine['city']}, {mine['state']} {mine['zip']}".rstrip() if mine else "")
        put("NAME &  ADDRESS OF INJURED OR DECEASED.0", injured, "matter client contact")

    for side, client_side, insurer_box, policy_box in (
        ("Plaintiffs", True, "INSURANCE CO. NAME.22", "POLICY NUMBER.0"),
        ("Defendants", False, "INSURANCE CO. NAME.221", "POLICY NUMBER.1"),
    ):
        insurer_id, src = facts.related_contact(
            parties, layout, side=side, relationship="Insurer", client_side=client_side
        )
        insurer = facts.fetch_contact(client, insurer_id)
        put(insurer_box, facts.contact_name(insurer) if insurer else None, f"insurer contact ({src})")
        key = f"Matter/{side}/InsurancePolicy/PolicyNumber"
        put(policy_box, str(layout.get(key) or "").strip(), f"layout {key}")

    # The other DRIVER is a person too: a company on the other side (a trucking
    # firm, an employer) never prints as the driver on a form the client signs
    # under penalty of perjury (review N15, 2026-10-06; the SR19 already checks).
    if other and isinstance(other.get("person"), dict):
        put("DRIVERS NAME.1", facts.contact_name(other), "the other side's contact")
        theirs = _address_parts(other)
        for box, part in (
            ("DRIVERS STREET ADDRESS.1", "street"),
            ("CITY.1", "city"),
            ("STATE2.1", "state"),
            ("ZIP CODE.1", "zip"),
        ):
            put(box, theirs.get(part), "the other side's contact address")

    confirmed = cited_facts.confirm(client, matter_id, cited, set(CITED_FIELDS))
    got = {k: f.value for k, f in confirmed.facts.items() if f.value}
    put("DRIVER LICENSE NUMBER.0", got.get("driver_license_number"), confirmed.facts["driver_license_number"].source)
    vehicle = " ".join(v for v in (got.get("vehicle_year"), got.get("vehicle_make"), got.get("vehicle_model")) if v)
    if got.get("vehicle_year") or got.get("vehicle_make"):
        put("REPORTING PARTY'S VEHICLE YEAR", vehicle, "cited vehicle facts")
    put("VEHICLE LICENSE PLATE", got.get("vehicle_plate"), "cited license plate")
    put("VEHICLE ID NUMBER111", got.get("vehicle_vin"), "cited VIN")
    return values, sources, confirmed


def _full_name(obj: Any) -> str:
    """A widget's fully qualified field name ("INJURED.0"): its own ``/T`` and
    every ancestor's, outermost first. A radio kid carries no ``/T`` of its own."""
    parts: list[str] = []
    while obj is not None:
        if obj.get("/T") is not None:
            parts.append(str(obj.get("/T")))
        parent = obj.get("/Parent")
        obj = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))


def fill_sr1(blob: bytes, values: dict[str, str], *, injured_driver: bool) -> bytes:
    """The state form with ``values`` in its text boxes (both pages: the SR 1A
    section shares the page-1 names), and, when the client is the injured
    driver, the "Injured" and "Driver" boxes of the first injured row ticked.
    Every other box, and the whole certification, is left as the state ships it."""
    checks = {"INJURED.0": "/INJURED", "DRIVER1": "/DRIVER"} if injured_driver else {}
    return fill_acroform(blob, values, checks)


def fill_acroform(blob: bytes, values: dict[str, str], checks: dict[str, str]) -> bytes:
    """A state AcroForm with ``values`` in its text boxes and each box named in
    ``checks`` (fully qualified name -> the "on" state) ticked; every other
    box exactly as the state ships it. Shared by the SR1 and the SR 19C."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(blob)))
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
        for annot in page.get("/Annots") or []:
            widget = annot.get_object()
            want = checks.get(_full_name(widget))
            states = list((widget.get("/AP") or {}).get("/N", {}).keys())
            if want and want in states:
                widget[NameObject("/AS")] = NameObject(want)
                # The VALUE lives on the field: the widget's parent when the
                # widget is one kid of a radio group, else the widget itself.
                parent = widget.get("/Parent")
                target = parent.get_object() if parent is not None and widget.get("/T") is None else widget
                target[NameObject("/V")] = NameObject(want)
    writer.set_need_appearances_writer(True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def render_sr1(matter_id: str, cited: dict[str, Any] | None = None) -> Any:
    """Prefill the California DMV SR1 (Report of Traffic Accident) for the
    matter's client, as the DRIVER, and file it on the matter for her to check,
    complete and sign. Never sign, date or certify it.

    Filled from the matter: her name, address, date of birth and phone; the
    accident date and location; the other driver's name and address; each
    side's insurer and policy number. ``cited``: what you read with
    ``read_document`` on her license and the vehicle's estimate or
    registration, as ``{field: {"value": "<as printed>", "file_id": "<id>"}}``
    for ``driver_license_number``, ``vehicle_year``, ``vehicle_make``,
    ``vehicle_model``, ``vehicle_plate``, ``vehicle_vin``. A cited value is
    accepted only when that document shows it beside its label. Never cite a
    value from the email or your own reading of the situation.

    Use it only when the client was the driver (the SR1 is the driver's report);
    if the file shows she was a passenger, do not call it and say so.

    Files ``DMV SR1 - for client signature.pdf`` at the matter's root and reads
    it back. Returns ``status`` (``filed``, ``filed_not_visible``, or
    ``refused`` with ``reason``), ``fileId``, ``fileName``, ``filled`` (the
    boxes filled, by name, NEVER their values), ``left_for_client`` (what she
    completes), ``cited_refused``, ``same_name_on_matter``. Mails nothing,
    completes no task."""
    matter = str(matter_id or "").strip()
    if not matter:
        return _refused("matter_id is required")
    client = _client()
    cfg = load_library_config()
    resolved = resolve_template(
        client, dataclasses.replace(cfg, templates={DOCUMENT_CLASS: DEFAULT_TEMPLATE, **cfg.templates}), DOCUMENT_CLASS
    )
    if not isinstance(resolved, ResolvedTemplate):
        return _refused(f"the SR1 form did not resolve ({resolved.reason}); nothing was filed")
    try:
        values, _sources, confirmed = gather(client, matter, cited)
    except Exception as exc:  # noqa: BLE001 - a record that could not be READ files nothing
        return _refused(f"the matter's record could not be read ({exc.__class__.__name__}); nothing was filed")
    try:
        data = fill_sr1(resolved.bytes, values, injured_driver="DRIVERS NAME.0" in values)
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
        "left_for_client": sorted(BOXES[b] for b in BOXES if b not in values) + list(CLIENT_COMPLETES),
        "cited_refused": {cited_facts.WORDS.get(k, k): why for k, why in confirmed.refused.items()},
        "same_name_on_matter": same,
        "template": {"name": resolved.name, "fileId": resolved.file_id},
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def register(server: Any) -> None:
    """Register the SR1 tool. Called once, from ``attachment_tools.register``."""
    server.tool()(render_sr1)


__all__ = ["BOXES", "DEFAULT_TEMPLATE", "FILE_NAME", "fill_acroform", "fill_sr1", "gather", "register", "render_sr1"]
