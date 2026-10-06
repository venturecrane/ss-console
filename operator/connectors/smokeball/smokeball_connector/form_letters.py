"""``render_firm_form_letter``: the firm's own rep-letter form, filled and filed.

WHAT IT IS FOR. A&P's staff make the 1st and 3rd party representation letters
from Smokeball task forms. Christa (2026-10-05) asked the Operator to make them
"in that exact formatting". The drafting lane cannot: ``render_docx_draft``
pours composed text into a template's page setup. This tool does not compose a
word. It opens the FIRM'S form from its Document Library, replaces the form's
``{{field}}`` placeholders with facts read from the matter's own record, and
files the result on the matter under the name the firm's own toolbar uses.

THE FORM. Resolved the way every library template is (``library.py``): the
library matter, its folder, then the file named
``self_initiation.document_library.templates[<class>]`` or, unauthored, the
name in ``FORMS``. A form that does not resolve REFUSES and files nothing,
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

from . import cited_facts
from . import form_letter_facts as facts
from .form_docx import FormError, fill_form, placeholders_in
from .form_letter_facts import Fact
from .library import ResolvedTemplate, list_matter_files, load_library_config, name_matches, resolve_template


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


FORMS: dict[str, FormSpec] = {
    "first_party_rep": FormSpec(
        label="1st party rep letter",
        document_class="first_party_rep_letter",
        default_template="Form - 1st Party Rep Letter.docx",
        file_name="1st party letter.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="VIA FAX: ",
        email_label="Email: ",
        paragraph_fields=frozenset({"delivery_lines"}),
        signer_name_from="authored",
    ),
    "third_party_rep": FormSpec(
        label="3rd party rep letter",
        document_class="third_party_rep_letter",
        default_template="Form - 3rd Party Rep Letter.docx",
        file_name="3rd Party Letter.docx",
        side="Defendants",
        client_side=False,
        fax_label="Via Fax: ",
        email_label="Via Email: ",
        paragraph_fields=frozenset({"delivery_lines", "carrier_address"}),
        signer_name_from="staff",
    ),
    # The firm's health-insurer notice for a Blue Shield of California member:
    # Blue Shield's recovery vendor is the addressee; its address and fax are
    # fixed text in the firm's form (6 firm letters, 2026-08-05 to 10-02).
    "health_blue_shield": FormSpec(
        label="health insurer notice (Blue Shield)",
        document_class="health_notice_blue_shield",
        default_template="Form - Med Ins Blue Shield.docx",
        file_name="Med Ins Req. Blue Shield.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="health",
    ),
    # The firm's toolbar FAX COVER SHEETS (2026-10-06). The dec page cover
    # follows up the 1st party rep letter, whose form already asks the carrier
    # to "include a declarations page with your letter of acknowledgement"
    # (the firm's own dec page faxes are 1st party; no 3rd party cover is
    # made). The med pay cover goes with the bills the person attaches.
    "dec_page_fax": FormSpec(
        label="dec page request (fax cover)",
        document_class="dec_page_fax",
        default_template="Form - Fax Dec Page.docx",
        file_name="Fax Cover Sheet - Req Dec Page.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="fax",
    ),
    "med_pay_fax": FormSpec(
        label="med pay request (fax cover)",
        document_class="med_pay_fax",
        default_template="Form - Fax Medpay.docx",
        file_name="Fax Cover Sheet Medpay.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="fax",
    ),
    # The firm's toolbar wage loss letter to the client's employer, with the
    # WAGE LOSS VERIFICATION page the employer fills (18 firm letters; the
    # form is rebuilt from the latest, 2026-07-07). The employer is the client
    # role's Employer relationship, else as the person wrote it; with neither,
    # nothing is filed and the person is asked.
    "wage_loss": FormSpec(
        label="wage loss letter",
        document_class="wage_loss_letter",
        default_template="Form - Wage Loss Letter.docx",
        file_name="Wage Loss Letter.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset({"employer_address"}),
        signer_name_from="preparer",
        facts_kind="wage",
    ),
}

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
_MAX_EMPLOYER = 200

#: The 1st party form's paragraph fields (each delivery channel its own line);
#: each form's own set is ``FormSpec.paragraph_fields``.
PARAGRAPH_FIELDS = FORMS["first_party_rep"].paragraph_fields
MARKER = "[Not in the file: {}]"

#: Read-back waits after the upload, in seconds; module-level so a test can
#: shorten them. Smokeball materializes an upload asynchronously.
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
    from .medicals_provider import _ADDRESS

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


def _carrier_email(client: Any, matter_id: str, spec: FormSpec) -> Fact:
    """The side's carrier email: the insurer contact's, else the adjuster's,
    with the contact named so the person can judge where it goes."""
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
            return Fact(email, f"email from the {relationship.lower()} contact {who} ({src})")
    return Fact(None, "", f"{_side_word(spec)} insurer email")


def draft_firm_email(client: Any, matter_id: str, form: str) -> dict[str, Any]:
    """One of ``EMAILS`` for the person to send from their own mailbox. Nothing
    is filed or sent. ``drafted`` when every fact was in the file, else
    ``incomplete`` with the markers in place and listed in ``unfilled``."""
    words = EMAILS[form]
    spec = FORMS["med_pay_fax"]  # the client's own carrier, the med pay side
    matter = client.get(f"/matters/{matter_id}")
    matter = matter if isinstance(matter, dict) else {}
    layout = facts.matter_layout_values(client, matter_id)
    found = {
        "carrier_email": _carrier_email(client, matter_id, spec),
        "claim_number": _claim_number(layout, spec),
        "client_name": facts.client_name(client, matter),
    }
    values = {k: f.value if f.value is not None else MARKER.format(f.missing) for k, f in found.items()}
    unfilled = [MARKER.format(f.missing) for f in found.values() if f.value is None]
    return {
        "status": "incomplete" if unfilled else "drafted",
        "fileId": None,
        "matterId": matter_id,
        "email": {
            "to": values["carrier_email"],
            "subject": words["subject"].format(**values),
            "body": words["body"].format(**values),
        },
        "unfilled": unfilled,
        "facts_used": {k: f.source for k, f in found.items() if f.value is not None},
    }


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


def _read_back(client: Any, matter_id: str, file_id: str) -> dict[str, Any] | None:
    for wait in READBACK_WAITS:
        SLEEP(wait)
        try:
            record = client.get(f"/matters/{matter_id}/documents/files/{file_id}")
        except Exception:  # noqa: BLE001 - a 404 is "still materializing"; any failure is retried, then reported as not visible
            continue
        if isinstance(record, dict) and record:
            return record
    return None


def render_firm_form_letter(
    matter_id: str,
    form: str,
    date: str | None = None,
    cited: dict[str, Any] | None = None,
    employer: dict[str, Any] | None = None,
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
    own mailbox, ``drafted`` or ``incomplete`` when a fact is missing).
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
    read back. Returns ``status`` (``filed``, ``filed_not_visible`` when the
    upload was accepted but the file did not appear in time, or ``refused``
    with ``reason`` and nothing filed), ``fileId``, ``fileName``, ``unfilled``,
    ``facts_used`` (each field and where it was read), ``template`` (the form
    used) and ``same_name_on_matter`` (ids of files already carrying this
    name, which the person should know about). A health notice also returns
    ``card_values_to_check`` (each card value it printed, which the reply
    lists so the person checks it against the card) and ``cited_refused``. It
    does not send, fax or mail the letter, and does not complete any task."""
    name = str(form or "").strip()
    spec = FORMS.get(name)
    if spec is None and name not in EMAILS:
        return _refused(f"form must be one of {sorted([*FORMS, *EMAILS])}")
    matter = str(matter_id or "").strip()
    if not matter:
        return _refused("matter_id is required")
    problem = _employer_problem(employer)
    if problem:
        return _refused(problem)
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
        return _refused(
            f"the firm's {spec.label} form did not resolve ({resolved.reason}); nothing was filed. "
            "No other letter is made in its place: the firm's form has to be in its Document Library."
        )
    return _fill_and_file(client, matter, spec, when, resolved, cited, employer)


def _fill_and_file(
    client: Any,
    matter: str,
    spec: FormSpec,
    when: date,
    resolved: ResolvedTemplate,
    cited: Any = None,
    employer: dict[str, Any] | None = None,
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
    confirmed: cited_facts.Confirmed | None = None
    try:
        if spec.facts_kind == "fax":
            record = client.get(f"/matters/{matter}")
            found = _fax_facts(client, matter, record if isinstance(record, dict) else {}, spec, when)
        elif spec.facts_kind == "wage":
            record = client.get(f"/matters/{matter}")
            try:
                wage = _wage_facts(client, matter, record if isinstance(record, dict) else {}, when, employer)
            except EmployerUnclear as exc:
                return {
                    "status": "employer_unclear",
                    "fileId": None,
                    "matterId": matter,
                    "reason": f"{exc}; nothing was filed",
                }
            if wage is None:
                return {
                    "status": "needs_employer",
                    "fileId": None,
                    "matterId": matter,
                    "reason": "the matter has no Employer on the client's role and none was given; nothing was filed",
                }
            found = wage
        elif spec.facts_kind == "health":
            record = client.get(f"/matters/{matter}")
            found, confirmed = _health_facts(
                client, matter, record if isinstance(record, dict) else {}, when, cited, carried
            )
        else:
            found = gather_facts(client, matter, spec, when)
    except Exception as exc:  # noqa: BLE001 - a record that could not be READ is never printed as "not in the file"; nothing files
        return _refused(
            f"the matter's record could not be read ({exc.__class__.__name__}: {str(exc)[:200]}); nothing was filed"
        )
    values = {k: f.value if f.value is not None else MARKER.format(f.missing) for k, f in found.items()}
    filled = fill_form(resolved.bytes, values, spec.paragraph_fields)
    used = set(filled.placeholders)
    unfilled = [MARKER.format(found[k].missing) for k in filled.placeholders if k in found and found[k].value is None]
    unfilled += [MARKER.format(name) for name in filled.unknown]
    if "employer_mailing_address" in found:
        unfilled.append(MARKER.format(found["employer_mailing_address"].missing))
    same = [str(e.get("id")) for e in list_matter_files(client, matter) if name_matches(e, spec.file_name)]
    result = client.add_file(matter, spec.file_name, filled.data)
    file_id = result.get("fileId") if isinstance(result, dict) else None
    seen = _read_back(client, matter, str(file_id)) if file_id else None
    out = {
        "status": "filed" if seen else "filed_not_visible",
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
    return out


def register(server: Any) -> None:
    """Register the form-letter tool. Called once, from ``attachment_tools.register``."""
    server.tool()(render_firm_form_letter)


__all__ = [
    "EMAILS",
    "FORMS",
    "MARKER",
    "PARAGRAPH_FIELDS",
    "READBACK_WAITS",
    "gather_facts",
    "register",
    "render_firm_form_letter",
]
