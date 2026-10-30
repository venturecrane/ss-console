"""Which matter does this invoice belong to? A closed verdict, not a judgement.

THE DEFECT THIS CLOSES (proven live 2026-09-18). A vendor invoice naming only
the client, with no matter number and no second identifying fact, was staged on
one matter of the two the tenant carries open for that client. Nothing in the
skill body was wrong: it already said to resolve from two facts that agree, and
to flag what it could not place. The model resolved anyway, because "is this
ambiguous?" was a question it answered in its own head and then acted on.

Ambiguity is a VALUE now. This module searches the tenant, and returns one of
four verdicts. Only ``unique`` carries a resolution token, and only that token
opens the write (``resolution_token``). "I am confident it is this matter" is
not expressible.

    unique         exactly one matter survives every fact the invoice supplied,
                   AND at least two independent facts corroborate it, each read
                   back from Smokeball in this call. ``matched_on`` names them.
    ambiguous      more than one survives. ``candidates`` names them so a person
                   can say which; nothing is staged.
    none           nothing survives, or one matter survives on a single fact. A
                   NAME ALONE IS NEVER A MATCH, and neither is a number alone.
    search_failed  the tenant could not be searched to the end. This is a fourth
                   value, not a flavour of ``none``, for the reason
                   ``library.lookup_matter`` refuses rather than returning "not
                   found" on a failed page-through: "nothing matched" and "I
                   could not look" are different facts, and collapsing them
                   turns a transient API error into a confident answer about the
                   firm's record. The skill reports it as a failed step.

HOW THE FACTS ARE USED, and the distinction that makes the arithmetic work:

* Two facts GENERATE candidates, because each can be searched on: the matter
  number (exact, on ``/matters?Search=``) and the client or claimant name (via
  ``/contacts?Search=name:*...*`` then ``/matters?ContactId=``, which is the
  membership relation read from the other direction). When both are supplied the
  survivors are the INTERSECTION: a number that names one matter and a name that
  names three leave the one matter they agree on, resolved on two facts.
* Four facts only NARROW, because no endpoint searches on them: the claim
  number, the date of loss, the court's case number and the date of birth (read
  from a party's contact record). Each narrows the survivors when at least one
  of them carries it, and is otherwise inert -- a date of loss the firm never
  recorded must not wipe a clean number-plus-name resolution.

WHERE THE CLAIM NUMBER, DATE OF LOSS AND CASE NUMBER LIVE (2026-10-30). The
first form of this module read the claim number and date of loss only from the
matter's own text (number, title, description), on the premise that Smokeball
has no field for either. The premise was wrong: a firm records them in the
matter's LAYOUT, the fields the form letters already read
(``form_letters._claim_number``, ``form_letter_facts.ACCIDENT_DATE_KEY``). At a
firm that keeps them there, a letter's claim number could never corroborate the
client's name, so every carrier letter for a client with two matters, and every
court notice, came back as a question (a client seat's post, 2026-10-30). The
layout is now read too, for the few survivors a name or number leaves.

Layout evidence narrows only by CONTRADICTION: a survivor is dropped when its
layout records the fact with a different value, never because its field is
empty. One client with an injury matter and a property-damage matter from one
accident, the claim recorded on only one of them, stays ``ambiguous``: an empty
field is not evidence the letter belongs to the other matter (the 2026-09-18
defect in another form). Every value of a key is collected across the layout's
items, so a second claimant's or second defendant's claim number is seen, not
hidden behind the first. The case number is read from the layout only, never
from a description, which may cite a related case.

Absence of corroboration is never a match, in either direction: a fact this
module cannot read back from Smokeball simply does not appear in ``matched_on``,
and a matter with one fact is ``none``.

The invoice's text is UNTRUSTED (ADR 0027). A client name arrives as
vendor-authored text and is used to build a search expression, so it is stripped
of the characters Smokeball's ``field:operator:value`` search syntax gives
meaning to before it goes near a query: an invoice cannot smuggle a structured
term into the search any more than it can smuggle an instruction into the turn.
"""

from __future__ import annotations

import re
from typing import Any

from .expense_ledger import contains_run_sequence, invoice_number_key
from .library import _listing as listing
from .library import _norm as norm
from .layout_shape import layout_values
from .resolution_token import mint

VERDICT_UNIQUE = "unique"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_NONE = "none"
VERDICT_SEARCH_FAILED = "search_failed"

#: Fact names, as they appear in ``matched_on`` and ``searched``. The strings are
#: the tool's vocabulary: the skill quotes them back in a flag line.
FACT_NUMBER = "matter_number"
FACT_NAME = "client_name"
FACT_CLAIM = "claim_number"
FACT_LOSS = "date_of_loss"
FACT_BIRTH = "date_of_birth"

FACT_CASE = "case_number"

#: The matter fields a claim number or date of loss may ALSO be read from, for a
#: firm that writes them into the matter's own text. The layout keys below are
#: where Smokeball keeps them. Both lists are closed on purpose: a fact found
#: nowhere in them does not corroborate, which fails toward ``none``.
MATTER_TEXT_FIELDS = ("number", "description", "title")

#: The layout keys each fact is read from: the keys the form letters and the
#: litigation pull already read (``form_letters._claim_number``,
#: ``form_letter_facts.ACCIDENT_DATE_KEY``, medchron ``litigation/deadlines/pull``).
LAYOUT_KEYS: dict[str, tuple[str, ...]] = {
    FACT_CLAIM: (
        "Matter/Plaintiffs/InsurancePolicy/Claims/Number",
        "Matter/Defendants/InsurancePolicy/Claims/Number",
    ),
    FACT_LOSS: ("Matter/CaseDetails/AccidentDetails/AccidentDate",),
    FACT_CASE: ("Matter/CaseDetails/StandardCaseDetails/CaseNumber",),
}

#: Layouts are read for at most this many survivors. Each costs one listing read
#: plus one read per layout item at 5 requests/second, and more survivors than
#: this is an ambiguous answer a claim number rarely settles.
MAX_LAYOUT_SURVIVORS = 4

#: A claim or case number shorter than this once normalized, or with no digit,
#: is too weak to corroborate ("2024", a misread "1"): it is reported ignored.
_MIN_NUMBER_KEY = 5

#: The contact fields a date of birth may be read from. Several spellings are
#: accepted because the value is compared exactly: a spelling we do not know
#: fails to corroborate, it never corroborates wrongly.
CONTACT_BIRTH_FIELDS = ("dateOfBirth", "birthDate", "dob")

#: Bounds. A name search that matches half the firm is an ambiguous answer, not
#: a reason to spend the seat's rate limit enumerating it (the tenant's usage
#: plan is 5 requests/second).
MAX_NAME_CONTACTS = 10
MAX_CANDIDATES = 10
_SEARCH_LIMIT = 50
_MAX_NAME_CHARS = 80

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Everything Smokeball's contact search gives meaning to, plus whitespace-ish
#: junk. Stripped from a vendor-authored name before it is wrapped in name:*...*.
_SEARCH_META = re.compile(r"[^0-9A-Za-z .,'\-]+")


class SearchFailed(Exception):
    """The tenant could not be searched to the end. Never an answer about it."""


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _matter_id(matter: dict[str, Any]) -> str:
    value = matter.get("id")
    return value if isinstance(value, str) and value else ""


def _matter_text(matter: dict[str, Any]) -> str:
    return " ".join(str(matter.get(f) or "") for f in MATTER_TEXT_FIELDS)


def _date_forms(iso: str) -> list[str]:
    """The ways a firm writes one date: ISO, and the zero-padded and unpadded US
    forms. Matched on word boundaries, so 2/10/2026 never matches 12/10/2026."""
    year, month, day = iso.split("-")
    return [iso, f"{month}/{day}/{year}", f"{int(month)}/{int(day)}/{year}"]


def _text_has_date(text: str, iso: str) -> bool:
    return any(re.search(rf"\b{re.escape(form)}\b", text) for form in _date_forms(iso))


def _iso_date(value: Any) -> str:
    text = _clean(value)
    return text if _ISO_DATE.match(text) else ""


def _get(client: Any, path: str, **params: Any) -> list[dict[str, Any]]:
    """One listing read, or SearchFailed. An exception here is never 'no rows'."""
    try:
        return listing(client.get(path, **params))
    except Exception as exc:
        # Re-raised, not swallowed: any failure to read is reported as a failure
        # to read, never as an answer about the firm's record.
        raise SearchFailed(f"the tenant could not be searched ({exc.__class__.__name__})") from exc


def _number_key(value: str) -> str:
    """A claim or case number as compared: letters and digits only, lowercased.
    Empty when too weak to corroborate (see ``_MIN_NUMBER_KEY``)."""
    key = invoice_number_key(value)
    return key if len(key) >= _MIN_NUMBER_KEY and any(c.isdigit() for c in key) else ""


#: A matter's layout facts: fact -> every value its layout records for it.
Layouts = dict[str, dict[str, list[str]]]


def _layout_facts(client: Any, matter_id: str) -> dict[str, list[str]]:
    """Every value this matter's layout records for each layout-backed fact.

    COLLECTED, never merged: ``form_letter_facts.matter_layout_values`` keeps the
    first non-empty value of a key, which is right for filling one letter and
    wrong for identity, where a matter with two claimants carries two claim
    numbers under one key and the letter may be about the second. A failed read
    of the listing or of any item is ``SearchFailed``: a layout we could not read
    is not a layout that records nothing."""
    wanted = {key: fact for fact, keys in LAYOUT_KEYS.items() for key in keys}
    found: dict[str, list[str]] = {}
    for item in _get(client, f"/matters/{matter_id}/layouts"):
        item_id = item.get("id") or item.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            continue
        try:
            values = layout_values(client.get(f"/matters/{matter_id}/layouts/{item_id}"))
        except Exception as exc:
            raise SearchFailed(f"a matter's layout could not be read ({exc.__class__.__name__})") from exc
        for key, value in values.items():
            fact = wanted.get(key)
            text = _clean(value)
            if fact == FACT_LOSS:
                text = _iso_date(text[:10])
            if fact and text:
                found.setdefault(fact, []).append(text)
    return found


def _layout_says(matter_id: str, fact: str, layouts: Layouts, values: dict[str, str]) -> bool | None:
    """True: this matter's layout records the letter's value. False: it records
    the fact, and every value differs (a contradiction). None: it records
    nothing for the fact, or its layout was not read, which is no evidence."""
    recorded = (layouts.get(matter_id) or {}).get(fact) or []
    if not recorded:
        return None
    if fact == FACT_LOSS:
        return values[FACT_LOSS] in recorded
    want = _number_key(values[fact])
    return any(invoice_number_key(value) == want for value in recorded)


def _matters_by_number(client: Any, number: str, records: dict[str, dict[str, Any]]) -> set[str]:
    """Matters whose own ``number`` equals this one. The endpoint's ``Search`` is
    a plain keyword (live-verified 2026-07-03), so the equality is re-checked
    here against what came back rather than trusted to the query."""
    want = norm(number)
    found: set[str] = set()
    for matter in _get(client, "/matters", Search=number, Limit=_SEARCH_LIMIT):
        mid = _matter_id(matter)
        if mid and norm(matter.get("number")) == want:
            records.setdefault(mid, matter)
            found.add(mid)
    return found


#: Generational suffixes. Never REQUIRED on either side: "John Smith Jr." is
#: the same person as a record carrying "John Smith", and a record carrying the
#: suffix is not contradicted by a name that leaves it off.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def _name_tokens(text: Any) -> list[str]:
    """One side of a name comparison, as tokens in order, casing kept.

    The same normalization runs on BOTH sides -- the caller's name and every
    record's -- so a spelling difference that is not a difference in the person
    cannot decide a match: ``.`` and ``,`` never sit inside a token ("R." is
    "R"), a hyphen joins two tokens ("Rosa-Linda" is "Rosa" "Linda"), and a
    generational suffix is dropped. "Last, First" is reordered to "First Last"
    when both sides of the comma carry a name, so first-and-last below means the
    same thing in either form.
    """
    if not isinstance(text, str):
        return []
    safe = _SEARCH_META.sub(" ", text)
    head, comma, tail = safe.partition(",")

    def _split(part: str) -> list[str]:
        return [t for t in re.split(r"[\s.,\-]+", part) if t and t.lower() not in _SUFFIXES]

    first, second = _split(head), _split(tail)
    if comma and first and second:
        return second + first
    return first + second


def _is_initial_of(short: str, long: str) -> bool:
    return len(short) == 1 and long.startswith(short)


def _agrees(a: str, b: str) -> bool:
    """Two middle tokens name the same middle: equal, or one is the other's
    initial ("R" and "Rupert")."""
    return a == b or _is_initial_of(a, b) or _is_initial_of(b, a)


def _contact_tokens(contact: dict[str, Any]) -> set[str]:
    """The lowercased name tokens a contact record carries, from either shape."""
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    company = contact.get("company") if isinstance(contact.get("company"), dict) else {}
    parts = [
        person.get("firstName"),
        person.get("middleName"),
        person.get("lastName"),
        company.get("name"),
        contact.get("name"),
    ]
    tokens: set[str] = set()
    for part in parts:
        tokens.update(t.lower() for t in _name_tokens(part))
    return tokens


def _name_matches(wanted: list[str], contact: dict[str, Any]) -> bool:
    """Does this record carry the caller's name? ``wanted`` is lowercased
    ``_name_tokens`` output, in order.

    A COMPANY record (and any record without a ``person`` block) keeps the rule
    it always had: every token of the caller's name must be on the record, so
    "Smith Family Trust" never matches "Smith Holdings Trust".

    A PERSON record is matched as COMPATIBLE, NOT CONTRADICTING. The first and
    last tokens are required. A middle token the caller gave and the record does
    not carry is optional when the record has no middle of its own -- a letter
    that says "Tobias R. Wren" is about the "Tobias Wren" the firm recorded --
    but when the record DOES carry a middle, the two must agree (equal, or an
    initial of it), and a middle that contradicts excludes the record: "Maria A.
    Lopez" is not the firm's "Maria B. Lopez", and a lone record that
    contradicts is not offered as a candidate either.
    """
    record = _contact_tokens(contact)
    if not wanted:
        return False
    if not isinstance(contact.get("person"), dict) or len(wanted) < 3:
        return set(wanted).issubset(record)
    first, last, middles = wanted[0], wanted[-1], wanted[1:-1]
    if first not in record or last not in record:
        return False
    unused = record - {first, last} - set(middles)
    for middle in middles:
        if middle in record or not unused:
            continue
        if not any(_agrees(middle, other) for other in unused):
            return False
    return True


def _probe_order(tokens: list[str]) -> list[str]:
    """Which token to search first: longest, and on a tie the LATER one."""
    ordered = sorted(enumerate(tokens), key=lambda it: (len(it[1]), it[0]), reverse=True)
    return [t for _, t in ordered]


def _contacts_by_name(client: Any, name: str) -> list[dict[str, Any]]:
    """Contacts whose name contains this one, capped. The search term is built
    here, not taken from the invoice: see the module docstring on untrusted text.

    THE SEARCH IS ON ONE TOKEN, AND THE FULL NAME IS MATCHED HERE. The vendor's
    ``name:`` index under-reports a MULTI-token term against duplicate records,
    which is precisely the case the ambiguous verdict exists to catch. Measured
    on the pilot tenant 2026-09-23, with two distinct contacts both recorded as
    firstName "Maria" / lastName "Alvarez", one on each of two open matters:

        name:*Maria Alvarez*  -> 1 row    (one of the two, silently)
        name:*Alvarez*        -> 2 rows   (both)

    The shipped one-call form returned a single contact, so the resolver saw one
    survivor and answered "one matter matched, on client_name alone" — a `none`
    verdict naming ONE matter number, when the truth was two matters and an
    `ambiguous` verdict. A reply built on that asks a person to confirm a
    specific matter that is not the letter's, and a one-word yes files a
    client's correspondence onto their own other file.

    So: search the LONGEST token (the most selective single term, a surname in
    western order and the company word otherwise), then match the caller's name
    against each record here (``_name_matches``). It recovers the duplicates the
    vendor drops.

    NAMES WITH A MIDDLE (2026-09-25). The first form of the match required
    EVERY caller token on the record, with ``.`` and ``-`` kept inside tokens.
    A letter naming "Tobias R. Wren" then never matched the firm's "Tobias
    Wren" ("r." is on neither), and "Rosa-Linda" never matched a record whose
    first name is "Rosa Linda": verdict ``none``, zero candidates, for clients
    the firm has. Both sides are now normalized the same way, and a person's
    middle is optional unless both sides carry one, when they must agree.
    """
    tokens = _name_tokens(name[:_MAX_NAME_CHARS] if isinstance(name, str) else name)
    if not tokens:
        return []
    # The probe keeps the caller's CASING: the vendor's search is case
    # insensitive either way, and a query that changes shape for no behavioural
    # reason is a change nobody asked for.
    #
    # ORDER: longest first, and on a TIE the LATER token. "Daniel Porter" ties
    # at six letters, and the first form of this fix searched "Daniel" -- a
    # common first name -- which filled the 50-row page with other Daniels and
    # returned "no matter" for a client who has one (a client seat, 2026-09-23). In
    # western order the later token is the surname, which is the selective one.
    probes = _probe_order(tokens)
    wanted = [t.lower() for t in tokens]

    def _matching(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [c for c in rows if isinstance(c.get("id"), str) and c["id"] and _name_matches(wanted, c)]

    rows = _get(client, "/contacts", Search=[f"name:*{probes[0]}*"], Limit=_SEARCH_LIMIT)
    matched = _matching(rows)
    # A page that came back FULL may have stopped before the contact we want, so
    # a full page proves nothing about absence. The same holds for a page that
    # held nothing matching the whole name. In either case the other tokens are
    # searched too and the results unioned: more calls, never fewer contacts.
    if len(rows) >= _SEARCH_LIMIT or not matched:
        seen = {c["id"] for c in matched}
        for probe in probes[1:]:
            if len(probe) < 2:
                continue
            for c in _matching(_get(client, "/contacts", Search=[f"name:*{probe}*"], Limit=_SEARCH_LIMIT)):
                if c["id"] not in seen:
                    seen.add(c["id"])
                    matched.append(c)
    return matched[:MAX_NAME_CONTACTS]


def contacts_by_name(client: Any, name: str) -> list[dict[str, Any]]:
    """The contact search above, for callers outside this module. The Medicals
    write (``medicals_tools``) names a provider by the name a bill prints and
    needs the firm's contact record for it; this is the one search that
    recovers the duplicates the vendor's index drops, and the one normalization
    of names, so a provider is looked up exactly as a client is."""
    return _contacts_by_name(client, name)


def _matters_by_name(
    client: Any, name: str, records: dict[str, dict[str, Any]]
) -> tuple[set[str], dict[str, list[dict[str, Any]]]]:
    """``(matter ids, matter id -> the matching contacts who are parties to it)``.

    A contact-filtered matter listing IS the membership relation (server.py's
    reply-lane note, ss#2167): every matter it returns names that contact as a
    party. That is what makes the name a fact read back from Smokeball rather
    than a string that appeared on an invoice."""
    found: set[str] = set()
    parties: dict[str, list[dict[str, Any]]] = {}
    for contact in _contacts_by_name(client, name):
        for matter in _get(client, "/matters", ContactId=contact["id"], Limit=_SEARCH_LIMIT):
            mid = _matter_id(matter)
            if not mid:
                continue
            records.setdefault(mid, matter)
            found.add(mid)
            parties.setdefault(mid, []).append(contact)
    return found, parties


def _carries(
    matter_id: str,
    fact: str,
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
    layouts: Layouts,
) -> bool:
    """Does THIS matter carry this narrowing fact, read back from Smokeball, in
    its own text or in its layout?"""
    return _text_carries(matter_id, fact, records, parties, values) or bool(
        _layout_says(matter_id, fact, layouts, values)
    )


def _text_carries(
    matter_id: str,
    fact: str,
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
) -> bool:
    """The fact as the first form of this module read it: the matter's own text,
    or a party's contact record. The case number has no text form."""
    matter = records.get(matter_id) or {}
    if fact == FACT_CLAIM:
        return contains_run_sequence(_matter_text(matter), invoice_number_key(values[FACT_CLAIM]))
    if fact == FACT_LOSS:
        return _text_has_date(_matter_text(matter), values[FACT_LOSS])
    if fact == FACT_BIRTH:
        want = values[FACT_BIRTH]
        return any(_birth_date(contact) == want for contact in parties.get(matter_id, []))
    return False


def _birth_date(contact: dict[str, Any]) -> str:
    """A party's birth date as YYYY-MM-DD, or "".

    The live tenant keeps it at ``person.birthDate`` as ``1979-04-02T00:00:00``
    (read off a client tenant 2026-10-01), not at the top level the first form
    of this read looked at. So a birth date never corroborated anything on the
    firm's real record, and a bill printing the client's name and birth date
    came back ``none`` with one candidate. The date part is what is compared;
    a value that does not start with an ISO date is not a birth date."""
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    for value in (
        *(contact.get(field) for field in CONTACT_BIRTH_FIELDS),
        *(person.get(field) for field in CONTACT_BIRTH_FIELDS),
    ):
        date = _iso_date(_clean(value)[:10])
        if date:
            return date
    return ""


def _narrow(
    survivors: set[str],
    facts: list[str],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
    layouts: Layouts,
) -> set[str]:
    """Apply each narrowing fact that at least one survivor carries.

    A fact no survivor carries is INERT, never fatal: the firm may simply not
    have recorded a date of loss, and that must not wipe a matter the number and
    the name already agree on. A fact some survivor carries discriminates, and
    that is how a name plus a date of loss reaches one matter.

    What a non-carrier loses depends on the evidence against it. Its LAYOUT
    recording the fact with another value drops it. Its layout recording
    nothing drops it only under the text rule this module always had (another
    survivor's own text carries the fact, and this one's layout says nothing);
    a carrier found only in a layout never drops a matter whose field is empty,
    so a claim recorded on one of a client's two matters leaves both standing."""
    for fact in facts:
        carriers = {mid for mid in survivors if _carries(mid, fact, records, parties, values, layouts)}
        if not carriers:
            continue
        text_carriers = {mid for mid in survivors if _text_carries(mid, fact, records, parties, values)}
        kept: set[str] = set()
        for mid in survivors:
            says = _layout_says(mid, fact, layouts, values)
            if mid in carriers or (says is None and not text_carriers):
                kept.add(mid)
        survivors = kept
    return survivors


def _matched_on(
    matter_id: str,
    supplied: list[str],
    generated: dict[str, set[str]],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
    layouts: Layouts,
) -> list[str]:
    """The facts THIS matter actually carries, in the order they were supplied."""
    out: list[str] = []
    for fact in supplied:
        hit = (
            matter_id in generated[fact]
            if fact in generated
            else _carries(matter_id, fact, records, parties, values, layouts)
        )
        if hit:
            out.append(fact)
    return out


def _candidate(matter_id: str, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One row of the ambiguous list. ``matter_number`` is what the reply names;
    ``title`` is there for the person, and the skill body forbids putting it in a
    client-facing line."""
    matter = records.get(matter_id) or {}
    number, title = matter.get("number"), matter.get("title")
    return {
        "matter_id": matter_id,
        "matter_number": number if isinstance(number, str) and number else None,
        "title": title if isinstance(title, str) and title else None,
    }


def _collect(
    client: Any, number: str, name: str, records: dict[str, dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, list[dict[str, Any]]]]:
    """The candidate sets the two searchable facts generate, plus the party
    contacts the name search read back."""
    generated: dict[str, set[str]] = {}
    parties: dict[str, list[dict[str, Any]]] = {}
    if number:
        generated[FACT_NUMBER] = _matters_by_number(client, number, records)
    if name:
        generated[FACT_NAME], parties = _matters_by_name(client, name, records)
    return generated, parties


def _verdict(
    survivors: list[str],
    base: dict[str, Any],
    supplied: list[str],
    generated: dict[str, set[str]],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
    layouts: Layouts,
) -> dict[str, Any]:
    """Turn the surviving set into the closed verdict. ONE survivor is not yet a
    match: it becomes one only when a second fact corroborates it."""
    if not survivors:
        return {**base, "verdict": VERDICT_NONE, "reason": "no matter matched every fact the invoice gave"}
    if len(survivors) > 1:
        return {
            **base,
            "verdict": VERDICT_AMBIGUOUS,
            "reason": f"{len(survivors)} matters match; a person has to say which",
            "candidate_count": len(survivors),
            "truncated": len(survivors) > MAX_CANDIDATES,
            "candidates": [_candidate(mid, records) for mid in survivors[:MAX_CANDIDATES]],
        }
    matter_id = survivors[0]
    matched_on = _matched_on(matter_id, supplied, generated, records, parties, values, layouts)
    candidate = _candidate(matter_id, records)
    if len(matched_on) < 2:
        return {
            **base,
            "verdict": VERDICT_NONE,
            "reason": f"one matter matched, on {matched_on[0] if matched_on else 'nothing'} alone; "
            "a single fact is never a match",
            "matched_on": matched_on,
            "candidates": [candidate],
        }
    return {
        **base,
        "verdict": VERDICT_UNIQUE,
        "matter_id": matter_id,
        "matter_number": candidate["matter_number"],
        "matched_on": matched_on,
        "matter_resolution": mint(matter_id, candidate["matter_number"], tuple(matched_on)),
    }


def resolve_matter(
    client: Any,
    *,
    client_name: Any = None,
    matter_number: Any = None,
    claim_number: Any = None,
    date_of_loss: Any = None,
    date_of_birth: Any = None,
    case_number: Any = None,
) -> dict[str, Any]:
    """Resolve the matter one invoice belongs to. See the module docstring for
    the four verdicts and the arithmetic; only ``unique`` mints a token."""
    values = {
        FACT_NUMBER: _clean(matter_number),
        FACT_NAME: _clean(client_name),
        FACT_CLAIM: _clean(claim_number) if _number_key(_clean(claim_number)) else "",
        FACT_LOSS: _iso_date(date_of_loss),
        FACT_BIRTH: _iso_date(date_of_birth),
        FACT_CASE: _clean(case_number) if _number_key(_clean(case_number)) else "",
    }
    # A fact that cannot be used is REPORTED, never silently dropped: a date that
    # is not an ISO date, or a claim or case number too short or with no digit to
    # corroborate anything. The sender should learn the fact went unused rather
    # than read a "none" that implies the firm's record lacks it.
    ignored = [
        fact
        for fact, raw in (
            (FACT_CLAIM, claim_number),
            (FACT_LOSS, date_of_loss),
            (FACT_BIRTH, date_of_birth),
            (FACT_CASE, case_number),
        )
        if _clean(raw) and not values[fact]
    ]
    supplied = [f for f in (FACT_NUMBER, FACT_NAME, FACT_CLAIM, FACT_LOSS, FACT_BIRTH, FACT_CASE) if values[f]]
    base: dict[str, Any] = {"searched": supplied, "ignored": ignored}
    if not (values[FACT_NUMBER] or values[FACT_NAME]):
        return {
            **base,
            "verdict": VERDICT_NONE,
            "reason": "the invoice gave neither a matter number nor a client name, so there was nothing to search on",
        }
    records: dict[str, dict[str, Any]] = {}
    try:
        generated, parties = _collect(client, values[FACT_NUMBER], values[FACT_NAME], records)
    except SearchFailed as exc:
        return {**base, "verdict": VERDICT_SEARCH_FAILED, "reason": str(exc)}
    survivors = set.intersection(*generated.values())
    narrowing = [f for f in (FACT_CLAIM, FACT_LOSS, FACT_BIRTH, FACT_CASE) if values[f]]
    layouts: Layouts = {}
    if any(f in LAYOUT_KEYS for f in narrowing) and 0 < len(survivors) <= MAX_LAYOUT_SURVIVORS:
        try:
            layouts = {mid: _layout_facts(client, mid) for mid in sorted(survivors)}
        except SearchFailed as exc:
            return {**base, "verdict": VERDICT_SEARCH_FAILED, "reason": str(exc)}
    survivors = _narrow(survivors, narrowing, records, parties, values, layouts)
    return _verdict(sorted(survivors), base, supplied, generated, records, parties, values, layouts)


__all__ = [
    "CONTACT_BIRTH_FIELDS",
    "LAYOUT_KEYS",
    "MATTER_TEXT_FIELDS",
    "MAX_CANDIDATES",
    "MAX_LAYOUT_SURVIVORS",
    "MAX_NAME_CONTACTS",
    "VERDICT_AMBIGUOUS",
    "VERDICT_NONE",
    "VERDICT_SEARCH_FAILED",
    "VERDICT_UNIQUE",
    "SearchFailed",
    "resolve_matter",
]
