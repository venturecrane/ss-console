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
* Three facts only NARROW, because no endpoint searches on them: the claim
  number, the date of loss (both read from the matter's own text) and the date of
  birth (read from a party's contact record). Each narrows the survivors when at
  least one of them carries it, and is otherwise inert -- a date of loss the firm
  never recorded must not wipe a clean number-plus-name resolution.

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

#: The matter fields a claim number or date of loss may be read from. Smokeball's
#: matter record has no structured field for either (surface doc, "Smokeball
#: matter shape"), so the firm records them in the text it does have. The list is
#: closed on purpose: a fact found nowhere in it does not corroborate, which
#: fails toward ``none``.
MATTER_TEXT_FIELDS = ("number", "description", "title")

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


def _contacts_by_name(client: Any, name: str) -> list[dict[str, Any]]:
    """Contacts whose name contains this one, capped. The search term is built
    here, not taken from the invoice: see the module docstring on untrusted text."""
    safe = _SEARCH_META.sub(" ", name).strip()[:_MAX_NAME_CHARS].strip()
    if not safe:
        return []
    rows = _get(client, "/contacts", Search=[f"name:*{safe}*"], Limit=_SEARCH_LIMIT)
    return [c for c in rows if isinstance(c.get("id"), str) and c["id"]][:MAX_NAME_CONTACTS]


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
) -> bool:
    """Does THIS matter carry this narrowing fact, read back from Smokeball?"""
    matter = records.get(matter_id) or {}
    if fact == FACT_CLAIM:
        return contains_run_sequence(_matter_text(matter), invoice_number_key(values[FACT_CLAIM]))
    if fact == FACT_LOSS:
        return _text_has_date(_matter_text(matter), values[FACT_LOSS])
    if fact == FACT_BIRTH:
        want = values[FACT_BIRTH]
        return any(
            _iso_date(contact.get(field)) == want
            for contact in parties.get(matter_id, [])
            for field in CONTACT_BIRTH_FIELDS
        )
    return False


def _narrow(
    survivors: set[str],
    facts: list[str],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
) -> set[str]:
    """Apply each narrowing fact that at least one survivor carries.

    A fact no survivor carries is INERT, never fatal: the firm may simply not
    have recorded a date of loss, and that must not wipe a matter the number and
    the name already agree on. A fact some survivor carries discriminates, and
    that is how a name plus a date of loss reaches one matter."""
    for fact in facts:
        subset = {mid for mid in survivors if _carries(mid, fact, records, parties, values)}
        if subset:
            survivors = subset
    return survivors


def _matched_on(
    matter_id: str,
    supplied: list[str],
    generated: dict[str, set[str]],
    records: dict[str, dict[str, Any]],
    parties: dict[str, list[dict[str, Any]]],
    values: dict[str, str],
) -> list[str]:
    """The facts THIS matter actually carries, in the order they were supplied."""
    out: list[str] = []
    for fact in supplied:
        hit = matter_id in generated[fact] if fact in generated else _carries(matter_id, fact, records, parties, values)
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
    matched_on = _matched_on(matter_id, supplied, generated, records, parties, values)
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
) -> dict[str, Any]:
    """Resolve the matter one invoice belongs to. See the module docstring for
    the four verdicts and the arithmetic; only ``unique`` mints a token."""
    values = {
        FACT_NUMBER: _clean(matter_number),
        FACT_NAME: _clean(client_name),
        FACT_CLAIM: _clean(claim_number),
        FACT_LOSS: _iso_date(date_of_loss),
        FACT_BIRTH: _iso_date(date_of_birth),
    }
    # A date that is not an ISO date is REPORTED, never silently dropped: the
    # sender should learn the fact went unused rather than read a "none" that
    # implies the firm's record lacks it.
    ignored = [
        fact
        for fact, raw in ((FACT_LOSS, date_of_loss), (FACT_BIRTH, date_of_birth))
        if _clean(raw) and not values[fact]
    ]
    supplied = [f for f in (FACT_NUMBER, FACT_NAME, FACT_CLAIM, FACT_LOSS, FACT_BIRTH) if values[f]]
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
    narrowing = [f for f in (FACT_CLAIM, FACT_LOSS, FACT_BIRTH) if values[f]]
    survivors = _narrow(survivors, narrowing, records, parties, values)
    return _verdict(sorted(survivors), base, supplied, generated, records, parties, values)


__all__ = [
    "CONTACT_BIRTH_FIELDS",
    "MATTER_TEXT_FIELDS",
    "MAX_CANDIDATES",
    "MAX_NAME_CONTACTS",
    "VERDICT_AMBIGUOUS",
    "VERDICT_NONE",
    "VERDICT_SEARCH_FAILED",
    "VERDICT_UNIQUE",
    "SearchFailed",
    "resolve_matter",
]
