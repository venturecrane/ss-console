"""``render_firm_form_letter``: the firm's own rep-letter form, filled and filed.

WHAT IT IS FOR. A&P's staff make the 1st and 3rd party representation letters
from Smokeball task forms. Christa (2026-10-05) asked the Operator to make them
"in that exact formatting". The drafting lane cannot: ``render_docx_draft``
pours composed text into a template's page setup. This tool does not compose a
word. It opens the FIRM'S form from its Document Library, replaces the form's
``{{field}}`` placeholders with facts read from the matter's own record, and
files the result on the matter under the name the firm's own toolbar uses.

THE CATALOG. Which forms exist is the firm's, authored in its customer.yaml
(``form_letters.forms``, :func:`parse_forms`); this module holds none.

THE FORM. Resolved the way every library template is (``library.py``): the
library matter, its folder, then the file named
``self_initiation.document_library.templates[<class>]`` or, unauthored, the
catalog entry's ``default_template``. A form that does not resolve REFUSES and files nothing,
naming what was looked for. There is no fallback letter: a rep letter on the
wrong form is the defect this tool exists to end.

THE FACTS (``form_letter_facts``). Every value is read from the matter: the
carrier is the insurer the matter's role relationships name for that side;
fax and email come from that insurer's contact, else from the side's ADJUSTER
contact (the carrier's claims desk), never the adjuster's name as the carrier's.
The signer is the matter's responsible staff member: on the 1st party letter
as the firm's authored signer map writes them, on the 3rd party letter by the
staff record's own name (what the firm's 3rd party task form merges), with
the title from the signer map either way. Anything the record does not hold prints as
``[Not in the file: <what>]``, visibly, in the letter, and is listed in
``unfilled``: nothing is defaulted, and a reader cannot mistake a gap for a
fact.

Classified INTERNAL_WRITE: the Operator saving work product into the firm's
own record. It sends, faxes and mails nothing. Every write goes through the
client's recorded write path (``write_record.py``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from . import cited_facts, readback
from . import form_letter_facts as facts
from .form_docx import FormError, fill_form, placeholders_in
from .form_letter_facts import Fact
from .library import ResolvedTemplate, load_library_config, resolve_template, same_named_ids


@dataclass(frozen=True)
class FormSpec:
    label: str
    document_class: str
    default_template: str
    file_name: str
    side: str
    client_side: bool
    fax_label: str
    email_label: str
    #: Fields whose paragraph repeats once per line of the value. The 3rd
    #: party task form lays the carrier's address out one paragraph per line;
    #: the 1st party form breaks it with ``w:br`` inside the carrier block.
    paragraph_fields: frozenset[str]
    #: Where the signer's NAME is read. ``authored``: the firm's signer map
    #: (the 1st party task form prints "Christopher A. Price"). ``staff``: the
    #: responsible staff record's own name (the 3rd party task form merges
    #: Attorney Responsible/Full Name, "Chris Price"); the title is authored
    #: either way, and the 3rd party form has no initials line. ``preparer``:
    #: the matter's ASSISTING staff record, who signs the firm's notice letters
    #: with ``form_letters.preparer_title``.
    signer_name_from: str
    #: ``rep``: a carrier letter (carrier, claim number, delivery lines).
    #: ``health``: a health-insurer notice, whose addressee is fixed text in
    #: the firm's form and whose member ID is read from the client's card
    #: (``cited_facts``), never from a Smokeball field or the email.
    facts_kind: str = "rep"


#: How each catalog field is read. Every field is required: a form whose signer
#: or fact source were defaulted would be a letter nobody authored.
_STRING_FIELDS = ("label", "document_class", "default_template", "file_name", "side")
_LABEL_FIELDS = ("fax_label", "email_label")  # printed verbatim; "" is a form without the line
_SIGNER_SOURCES = frozenset({"authored", "staff", "preparer"})
#: The fact readers ``_gather`` has. ``police`` is not here: the police request
#: builds its own spec per agency (``police_request.py``).
_FACTS_KINDS = frozenset({"rep", "health", "fax", "wage"})
_FIELDS = frozenset(
    {*_STRING_FIELDS, *_LABEL_FIELDS, "client_side", "paragraph_fields", "signer_name_from", "facts_kind"}
)


class CatalogError(ValueError):
    """The seat's ``form_letters.forms`` is authored but cannot be read as a catalog."""


def _check_entry(where: str, entry: Any) -> None:
    """Raise :class:`CatalogError` naming the first thing wrong with one entry."""
    if not isinstance(entry, dict):
        raise CatalogError(f"{where} must be a mapping")
    unknown = sorted(set(entry) - _FIELDS)
    missing = sorted(_FIELDS - set(entry))
    if unknown or missing:
        raise CatalogError(f"{where}: unknown {unknown or 'none'}, missing {missing or 'none'}")
    for field in _STRING_FIELDS:
        if not isinstance(entry[field], str) or not entry[field].strip():
            raise CatalogError(f"{where}.{field} must be a non-empty string")
    for field in _LABEL_FIELDS:
        if not isinstance(entry[field], str):
            raise CatalogError(f"{where}.{field} must be a string")
    if not isinstance(entry["client_side"], bool):
        raise CatalogError(f"{where}.client_side must be true or false")
    fields = entry["paragraph_fields"]
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields):
        raise CatalogError(f"{where}.paragraph_fields must be a list of placeholder names")
    if entry["signer_name_from"] not in _SIGNER_SOURCES:
        raise CatalogError(f"{where}.signer_name_from must be one of {sorted(_SIGNER_SOURCES)}")
    if entry["facts_kind"] not in _FACTS_KINDS:
        raise CatalogError(f"{where}.facts_kind must be one of {sorted(_FACTS_KINDS)}")


def parse_forms(block: Any) -> dict[str, FormSpec]:
    """``form_letters.forms`` -> ``{form name: FormSpec}``.

    THE CATALOG IS THE FIRM'S. Which letters a firm makes on its own forms, the
    name each files under, which side's carrier it addresses, how its delivery
    lines are labelled and who signs it are the firm's toolbar, so they are
    authored per seat in customer.yaml (ADR 0100 W4), never a list in this
    module. The code keeps only what a form can do: the fact readers
    (``facts_kind``) and the signer sources (``signer_name_from``). Unauthored
    is an empty catalog (no form letter is offered); a malformed entry raises
    and nothing is filed."""
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise CatalogError("form_letters.forms must be a mapping of form name to its entry")
    out: dict[str, FormSpec] = {}
    for name, entry in block.items():
        _check_entry(f"form_letters.forms.{name}", entry)
        out[str(name)] = FormSpec(
            **{k: entry[k] for k in _FIELDS if k != "paragraph_fields"},
            paragraph_fields=frozenset(entry["paragraph_fields"]),
        )
    return out


def load_forms(path: str | None = None) -> dict[str, FormSpec]:
    """The seat's form catalog, read off its live customer.yaml at call time
    (``form_letters.forms``, the shape of ``form_letter_facts.load_signers``)."""
    block = facts._load_yaml(path).get(facts.CONFIG_BLOCK)
    return parse_forms(block.get("forms") if isinstance(block, dict) else None)


_MAX_EMPLOYER = 200

MARKER = "[Not in the file: {}]"

#: Read-back waits after the upload, in seconds. Since 2026-10-13 every form
#: files through ``readback.prove_file`` (whose own ``WAITS``/``SLEEP`` run the
#: poll); these stay exported because the form tests and sr1 import them.
READBACK_WAITS: tuple[float, ...] = (2, 3, 5, 8)
SLEEP: Callable[[float], None] = time.sleep


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "refused", "fileId": None, "reason": reason, **extra}


def _side_word(spec: FormSpec) -> str:
    return "1st party" if spec.client_side else "3rd party"


def _carrier_facts(client: Any, spec: FormSpec, layout: dict[str, Any], parties: facts.Parties) -> dict[str, Fact]:
    """carrier_name, carrier_address and delivery_lines for the form's side."""
    who = f"{_side_word(spec)} insurer"
    insurer_id, insurer_src = facts.related_contact(
        parties, layout, side=spec.side, relationship="Insurer", client_side=spec.client_side
    )
    adjuster_id, adjuster_src = facts.related_contact(
        parties, layout, side=spec.side, relationship="Adjuster", client_side=spec.client_side
    )
    insurer = facts.fetch_contact(client, insurer_id)
    adjuster = facts.fetch_contact(client, adjuster_id)
    name = facts.contact_name(insurer) if insurer else None
    address = facts.contact_address(insurer) if insurer else None
    out = {
        "carrier_name": Fact(name, f"insurer contact ({insurer_src})") if name else Fact(None, "", f"{who} name"),
        "carrier_address": (
            Fact(address, f"insurer contact ({insurer_src})") if address else Fact(None, "", f"{who} address")
        ),
    }
    lines, sources = [], []
    for label, read, what in (
        (spec.fax_label, facts.contact_fax, "fax"),
        (spec.email_label, facts.contact_email, "email"),
    ):
        for contact, src, role in ((insurer, insurer_src, "insurer"), (adjuster, adjuster_src, "adjuster")):
            value = read(contact) if contact else None
            if value:
                lines.append(f"{label}{value}")
                sources.append(f"{what} from the {role} contact ({src})")
                break
    out["delivery_lines"] = (
        Fact("\n".join(lines), "; ".join(sources)) if lines else Fact(None, "", f"{who} fax or email")
    )
    return out


def _claim_number(layout: dict[str, Any], spec: FormSpec) -> Fact:
    key = f"Matter/{spec.side}/InsurancePolicy/Claims/Number"
    value = str(layout.get(key) or "").strip()
    return Fact(value, f"layout {key}") if value else Fact(None, "", f"{_side_word(spec)} claim number")


def _fax_facts(
    client: Any, matter_id: str, matter: dict[str, Any], spec: FormSpec, letter_date: date
) -> dict[str, Fact]:
    """A fax cover sheet to the side's carrier: its name and FAX number (the
    insurer contact's, else the adjuster's, the same order as the rep letters),
    the claim number, the client, the date as the firm's covers print it
    (MM/DD/YYYY), and the preparer who sends it. The page count is the
    sender's: she attaches the pages, so it is always left for her."""
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    who = f"{_side_word(spec)} insurer"
    insurer_id, insurer_src = facts.related_contact(
        parties, layout, side=spec.side, relationship="Insurer", client_side=spec.client_side
    )
    adjuster_id, adjuster_src = facts.related_contact(
        parties, layout, side=spec.side, relationship="Adjuster", client_side=spec.client_side
    )
    insurer = facts.fetch_contact(client, insurer_id)
    adjuster = facts.fetch_contact(client, adjuster_id)
    name = facts.contact_name(insurer) if insurer else None
    fax = None
    for contact, src, role in ((insurer, insurer_src, "insurer"), (adjuster, adjuster_src, "adjuster")):
        number = facts.contact_fax(contact) if contact else None
        if number:
            fax = Fact(number, f"fax from the {role} contact ({src})")
            break
    preparer = facts.preparer_facts(client, matter, facts.load_preparer_title())
    return {
        "carrier_name": Fact(name, f"insurer contact ({insurer_src})") if name else Fact(None, "", f"{who} name"),
        "carrier_fax": fax or Fact(None, "", f"{who} fax number"),
        "date_numeric": Fact(letter_date.strftime("%m/%d/%Y"), "the letter date"),
        "claim_number": _claim_number(layout, spec),
        "client_name": facts.client_name(client, matter),
        "signer_name": preparer["name"],
        "signer_title": preparer["title"],
        "preparer_email": preparer["email"],
        "page_count": Fact(None, "", "page count (attach the bills, then count the pages)"),
    }


def _address_lines(address: str) -> str:
    """ "100 Main St, Sacramento, CA 95814" as the firm's address block prints
    it (street, then city line); any other shape is printed as written."""
    from .medicals_layout import _ADDRESS

    match = _ADDRESS.match(address)
    if not match:
        return address
    return f"{match['line1'].strip()}\n{match['city'].strip()}, {match['state'].upper()} {match['zip']}"


class EmployerUnclear(Exception):
    """The matter names an Employer the letter cannot use: several of them,
    or one whose contact reads back with no name. Never read as "none"."""


def _employer(client: Any, matter_id: str, given: dict[str, Any] | None) -> tuple[Fact, Fact] | None:
    """The employer's name and address block: the client role's Employer
    relationship first, else as the person wrote it; None with neither.
    Raises ``EmployerUnclear`` when the matter's Employer cannot be used, so
    an unreadable or ambiguous one never falls through to the sender's."""
    parties = facts.read_parties(client, matter_id)
    named = {
        facts._contact_of(rel)
        for role in parties.roles
        if role.get("isClient") is True
        for rel in role.get("relationships") or []
        if isinstance(rel, dict) and str(rel.get("name") or "").strip().lower() == "employer"
    }
    named.discard(None)
    if len(named) > 1:
        names = sorted(facts.contact_name(facts.fetch_contact(client, cid)) or "an unnamed contact" for cid in named)
        raise EmployerUnclear(f"the client's role names {len(named)} Employers ({', '.join(names)}); say which")
    if named:
        contact = facts.fetch_contact(client, next(iter(named)))
        name = facts.contact_name(contact) if contact else None
        if not name:
            raise EmployerUnclear("the client's role names an Employer whose contact has no name")
        src = "the client role's Employer relationship"
        address = facts.contact_address(contact)
        return Fact(name, src), (Fact(address, src) if address else Fact(None, "", "employer's mailing address"))
    if not given:
        return None
    src = "as the sender wrote it (no Employer on the matter)"
    name = str(given.get("name") or "").strip()
    address = str(given.get("address") or "").strip()
    return (
        Fact(name, src),
        Fact(_address_lines(address), src) if address else Fact(None, "", "employer's mailing address"),
    )


def _wage_facts(
    client: Any, matter_id: str, matter: dict[str, Any], letter_date: date, employer: dict[str, Any] | None
) -> dict[str, Fact] | None:
    """The wage loss letter: the employer (``_employer``), the client with
    their own title and birth date, the date of loss both ways the firm's
    letter prints it, and the preparer who signs. None: no employer at all."""
    from .sr1_form import _birth_date

    found = _employer(client, matter_id, employer)
    if found is None:
        return None
    name, address = found
    block = (
        Fact(f"{name.value}\n{address.value}", name.source)
        if address.value
        else Fact(f"{name.value}\n{MARKER.format(address.missing)}", name.source)
    )
    layout = facts.matter_layout_values(client, matter_id)
    loss = facts.date_of_loss(layout)
    long_loss = (
        Fact(facts.long_date(date(int(loss.value[6:]), int(loss.value[:2]), int(loss.value[3:5]))), loss.source)
        if loss.value
        else loss
    )
    ids = [c for c in matter.get("clientIds") or [] if isinstance(c, str)]
    me = facts.fetch_contact(client, ids[0]) if ids else {}
    dob = _birth_date(me) if isinstance(me.get("person"), dict) else None
    preparer = facts.preparer_facts(client, matter, facts.load_preparer_title())
    out = {
        "date": Fact(facts.long_date(letter_date), "the letter date"),
        "employer_name": name,
        "employer_address": block,
        "client_name": facts.client_name(client, matter),
        "client_salutation": facts.client_salutation(client, matter),
        "client_dob": Fact(dob, "matter client contact") if dob else Fact(None, "", "client's date of birth"),
        "date_of_loss": loss,
        "accident_date_long": long_loss,
        "signer_name": preparer["name"],
        "signer_title": preparer["title"],
    }
    if not address.value:
        out["employer_mailing_address"] = address  # listed in unfilled; the block prints its marker
    return out


def _employer_problem(employer: Any) -> str | None:
    if employer is None:
        return None
    if not isinstance(employer, dict) or not str(employer.get("name") or "").strip():
        return 'employer must be {"name": ..., "address": ...} as the person wrote it, with a name'
    for key in ("name", "address"):
        value = employer.get(key)
        if value is not None and (not isinstance(value, str) or len(value.strip()) > _MAX_EMPLOYER):
            return f"employer {key} must be text up to {_MAX_EMPLOYER} characters"
    return None


def _health_facts(
    client: Any, matter_id: str, matter: dict[str, Any], letter_date: date, cited: Any, wanted: set[str]
) -> tuple[dict[str, Fact], cited_facts.Confirmed]:
    """A health-insurer notice: the client, the date of loss, the preparer who
    signs it, and the card's identifiers confirmed on the card itself."""
    layout = facts.matter_layout_values(client, matter_id)
    preparer = facts.preparer_facts(client, matter, facts.load_preparer_title())
    confirmed = cited_facts.confirm(client, matter_id, cited, wanted & set(cited_facts.LABELS))
    found = {
        "date": Fact(facts.long_date(letter_date), "the letter date"),
        "client_name": facts.client_name(client, matter),
        "client_salutation": facts.client_salutation(client, matter),
        "date_of_loss": facts.date_of_loss(layout),
        "signer_name": preparer["name"],
        "signer_title": preparer["title"],
        "preparer_email": preparer["email"],
        **confirmed.facts,
    }
    return found, confirmed


def gather_facts(client: Any, matter_id: str, spec: FormSpec, letter_date: date) -> dict[str, Fact]:
    """Every field the two rep-letter forms carry, read from the matter."""
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    parties = facts.read_parties(client, matter_id)
    if spec.signer_name_from == "staff":
        signer = facts.staff_signer_facts(client, matter, facts.load_signers())
    else:
        signer = facts.signer_facts(client, matter, facts.load_signers())
    found = {
        "date": Fact(facts.long_date(letter_date), "the letter date"),
        "client_name": facts.client_name(client, matter),
        "date_of_loss": facts.date_of_loss(layout),
        "claim_number": _claim_number(layout, spec),
        **{f"signer_{k}": v for k, v in signer.items()},
    }
    found.update(_carrier_facts(client, spec, layout, parties))
    return found


def _letter_date(raw: str | None) -> date | str:
    if raw is None or not str(raw).strip():
        return facts.seat_today()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return "date must be YYYY-MM-DD, or omitted for today"


def _resolve_form(client: Any, spec: FormSpec) -> Any:  # ResolvedTemplate | NotResolved
    cfg = load_library_config()
    templates = {spec.document_class: spec.default_template, **cfg.templates}
    return resolve_template(client, dataclasses.replace(cfg, templates=templates), spec.document_class)


def _file_proved(client: Any, matter_id: str, file_name: str, data: bytes) -> tuple[Any, dict[str, Any]]:
    """Upload a filled form and prove it (readback.py): ``(fileId, proof)``.
    Until 2026-10-13 the forms read back existence only and reported a miss as
    ``filed_not_visible``, which paged nobody; every form now proves id, size
    and sha256, and a miss is ``status: link_not_visible``."""
    result = client.add_file(matter_id, file_name, data)
    file_id = result.get("fileId") if isinstance(result, dict) else None
    return file_id, readback.prove_file(client, matter_id, file_id, data, file_name=file_name)


def render_firm_form_letter(
    matter_id: str,
    form: str,
    date: str | None = None,
    cited: dict[str, Any] | None = None,
    employer: dict[str, Any] | None = None,
    agency_words: str | None = None,
    agency_given: dict[str, Any] | None = None,
) -> Any:
    """Make one of the firm's letters on its OWN form and file it on the
    matter. Never write the letter yourself.

    ``form`` is ``first_party_rep`` (to the client's own carrier),
    ``third_party_rep`` (to the other side's carrier), or
    ``dec_page_fax`` (the firm's fax cover sheet asking the client's own
    carrier for her declarations page, a follow-up to the 1st party rep
    letter's request), ``med_pay_fax`` (the firm's med pay fax cover sheet
    asking that carrier to pay the attached bills directly to the providers;
    the person attaches the bills and fills the page count), or
    ``health_blue_shield`` (the health-insurer notice for a Blue
    Shield of California member; Blue Shield's recovery vendor is the
    addressee printed in the firm's form), ``wage_loss`` (the firm's letter
    to the client's employer with the wage loss verification page the
    employer fills), or ``med_pay_ledger_email`` (the firm's email asking the
    client's own carrier for the med pay ledger: NOTHING is filed; the result
    carries ``email`` {to, subject, body} for the person to send from their
    own mailbox and ``reply_block``, which goes into the reply exactly as returned, ``drafted`` or ``incomplete`` when a fact is missing).
    ``police_report`` makes the firm's police report request letter (the
    Highway Patrol letter or the city police letter, by agency): pass
    ``agency_words`` (the sender's own words naming the agency) and, from a
    crash card in the file, ``cited`` crash_time / ncic_number / officer_id /
    report_number; with no directory match, ``agency_given`` {"name",
    "address"} exactly as the sender wrote it or the card prints it. Its
    result carries ``reply_block`` for the reply as is, or ``needs_agency`` /
    ``agency_unclear`` with nothing filed.
    ``date`` is the letter date as YYYY-MM-DD; omit it for today.

    ``employer`` (wage loss only): ``{"name": ..., "address": ...}`` exactly
    as the person wrote it in this thread, used only when the matter holds no
    Employer on the client's role. With neither, the result is
    ``needs_employer`` and nothing is filed: ask the person for the employer's
    name and mailing address. Never supply an employer yourself.

    ``cited`` (health notice only): what you read on the client's insurance
    card with ``read_document``, as ``{"member_id": {"value": "<as printed>",
    "file_id": "<the card's file id>"}}``. The value is accepted only when the
    card's own transcription shows it beside its "ID#"/"Member" label;
    otherwise the letter prints a marker. Never cite a value from the email.
    The health notice is signed by the matter's ASSISTING staff member (the
    legal assistant on the file), with their email in the letter, and opens
    "retained by Ms. Rodriguez" from the client contact's own title.

    Every value is read here from the matter: the client's name, the date of
    loss, the claim number, the carrier and its fax/email/address from the
    matter's insurer and adjuster contacts, and the signer from the matter's
    responsible staff member (1st party: as the firm's signer list writes
    them; 3rd party: the staff record's own name, the title from the list).
    A fact the record does not hold is printed in the letter as ``[Not in the file: what]``
    and listed in ``unfilled``: tell the person who asked exactly those, so
    they fill them in Smokeball or on the letter. Never supply them yourself.

    The letter files at the matter's root as ``1st party letter.docx`` or
    ``3rd Party Letter.docx`` (the names the firm's own toolbar uses) and is
    read back by id, size and sha256. Returns ``status`` (``filed`` with
    ``readback: "confirmed"``; ``link_not_visible`` when the upload was
    accepted but the file did not appear in time, or ``readback_mismatch``
    when it appeared with other bytes: SMD is alerted on both, and the letter
    must NOT be made again, a retry files a duplicate; or ``refused`` with
    ``reason`` and nothing filed), ``fileId``, ``fileName``, ``unfilled``,
    ``facts_used`` (each field and where it was read), ``template`` (the form
    used) and ``same_name_on_matter`` (ids of files already carrying this
    name, which the person should know about; null when the listing could not
    be read, which is unknown, never none). A health notice also returns
    ``card_values_to_check`` (each card value it printed, which the reply
    lists so the person checks it against the card) and ``cited_refused``. It
    does not send, fax or mail the letter, and does not complete any task."""
    name = str(form or "").strip()
    from .form_emails import EMAILS, draft_firm_email

    try:
        forms = load_forms()
    except CatalogError as exc:
        if name not in EMAILS and name != "police_report":
            return _refused(f"the firm's form catalog could not be read ({exc}); nothing was filed")
        forms = {}
    spec = forms.get(name)
    if spec is None and name not in EMAILS and name != "police_report":
        return _refused(f"form must be one of {sorted([*forms, *EMAILS, 'police_report'])}")
    matter = str(matter_id or "").strip()
    if not matter:
        return _refused("matter_id is required")
    problem = _employer_problem(employer)
    if problem:
        return _refused(problem)
    if name == "police_report":
        return _police(matter, date, cited, agency_words, agency_given)
    if spec is None:
        try:
            return draft_firm_email(_client(), matter, name)
        except Exception as exc:  # noqa: BLE001 - a record that could not be READ is never printed as "not in the file"
            return _refused(f"the matter's record could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    when = _letter_date(date)
    if isinstance(when, str):
        return _refused(when)
    client = _client()
    resolved = _resolve_form(client, spec)
    if not isinstance(resolved, ResolvedTemplate):
        if getattr(resolved, "transient", False):
            return _refused(
                f"the firm's {spec.label} form could not be reached just now ({resolved.reason}); "
                "nothing was filed. The form is not known to be missing: this was a failed read, "
                "so ask again and it will be made from the same form."
            )
        return _refused(
            f"the firm's {spec.label} form did not resolve ({resolved.reason}); nothing was filed. "
            "No other letter is made in its place: the firm's form has to be in its Document Library."
        )
    return _fill_and_file(client, matter, spec, when, resolved, cited, employer)


def _gather(
    client: Any,
    matter: str,
    spec: FormSpec,
    when: date,
    carried: set[str],
    cited: Any,
    employer: dict[str, Any] | None,
) -> tuple[dict[str, Fact] | None, cited_facts.Confirmed | None]:
    """The form's facts by its kind. None (wage loss only): no employer at all."""
    if spec.facts_kind == "rep":
        return gather_facts(client, matter, spec, when), None
    record = client.get(f"/matters/{matter}")
    record = record if isinstance(record, dict) else {}
    if spec.facts_kind == "fax":
        return _fax_facts(client, matter, record, spec, when), None
    if spec.facts_kind == "wage":
        return _wage_facts(client, matter, record, when, employer), None
    return _health_facts(client, matter, record, when, cited, carried)


def _fill_and_file(
    client: Any,
    matter: str,
    spec: FormSpec,
    when: date,
    resolved: ResolvedTemplate,
    cited: Any = None,
    employer: dict[str, Any] | None = None,
    gather: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    template = {"name": resolved.name, "fileId": resolved.file_id}
    try:
        carried = set(placeholders_in(resolved.bytes))
        if not carried:
            return _refused(
                f"{resolved.name!r} carries no {{{{field}}}} placeholders, so it is not a form", template=template
            )
    except FormError as exc:
        return _refused(f"{resolved.name!r} could not be filled: {exc}", template=template)
    try:
        if gather is not None:
            found, confirmed = gather(client, matter, spec, when, carried, cited)
        else:
            found, confirmed = _gather(client, matter, spec, when, carried, cited, employer)
    except EmployerUnclear as exc:
        return {"status": "employer_unclear", "fileId": None, "matterId": matter, "reason": f"{exc}; nothing was filed"}
    except Exception as exc:  # noqa: BLE001 - a record that could not be READ is never printed as "not in the file"; nothing files
        return _refused(
            f"the matter's record could not be read ({exc.__class__.__name__}: {str(exc)[:200]}); nothing was filed"
        )
    if found is None:
        return {
            "status": "needs_employer",
            "fileId": None,
            "matterId": matter,
            "reason": "the matter has no Employer on the client's role and none was given; nothing was filed",
        }
    values = {k: f.value if f.value is not None else MARKER.format(f.missing) for k, f in found.items()}
    filled = fill_form(resolved.bytes, values, spec.paragraph_fields)
    used = set(filled.placeholders)
    unfilled = [MARKER.format(found[k].missing) for k in filled.placeholders if k in found and found[k].value is None]
    unfilled += [MARKER.format(name) for name in filled.unknown]
    unfilled += [MARKER.format(f.missing) for k, f in found.items() if k.endswith("_mailing_address")]
    same = same_named_ids(client, matter, spec.file_name)
    file_id, proof = _file_proved(client, matter, spec.file_name, filled.data)
    out = {
        "status": "filed",
        "fileId": file_id,
        "fileName": spec.file_name,
        "matterId": matter,
        "unfilled": unfilled,
        "facts_used": {k: f.source for k, f in found.items() if k in used and f.value is not None},
        "template": template,
        "same_name_on_matter": same,
        "sha256": hashlib.sha256(filled.data).hexdigest(),
    }
    if "employer_name" in found:
        out["employer_used"] = values["employer_address"]  # the reply shows the sender what the letter is addressed to
    if confirmed is not None:
        out["card_values_to_check"] = {cited_facts.WORDS[k]: v for k, v in confirmed.shown.items() if k in used}
        out["cited_refused"] = {cited_facts.WORDS.get(k, k): why for k, why in confirmed.refused.items()}
    return readback.verdict(out, proof)


def _police(matter: str, date: str | None, cited: Any, words: str | None, given: Any) -> dict[str, Any]:
    from .police_request import render_police

    when = _letter_date(date)
    if isinstance(when, str):
        return _refused(when)
    if given is not None and not isinstance(given, dict):
        return _refused('agency_given must be {"name": ..., "address": ...}')
    return render_police(_client(), matter, when, cited, words, given)


def register(server: Any) -> None:
    """Register the form-letter tool. Called once, from ``attachment_tools.register``."""
    server.tool()(render_firm_form_letter)


__all__ = [
    "CatalogError",
    "MARKER",
    "READBACK_WAITS",
    "gather_facts",
    "load_forms",
    "parse_forms",
    "register",
    "render_firm_form_letter",
]
