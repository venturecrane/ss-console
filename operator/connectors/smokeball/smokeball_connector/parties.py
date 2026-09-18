"""Pure readers over Smokeball contact and role records.

Split out of server.py when the module-size ratchet caught that file growing
(the vendor invoice intake tools, 2026-09-18). Every function here is a pure
function of a vendor payload: no client, no I/O, no state. server.py imports
them under their original names, and the reasoning each one carries (ss#2167's
"unresolved is never 'not a party'", the caption-poisoning guards) is stated in
the docstrings that moved with them.
"""

from __future__ import annotations

import re
from typing import Any


def _party_surname(contact: Any) -> str | None:
    """Resolve a contact object to a single plain party label (person surname or
    company name). Tolerates the nested (``person``/``company``) shape confirmed
    live 2026-07-08 and a flat fallback. Structured fields only, never free text;
    stripped and length-bounded; rejects a label that itself looks like a caption
    or a cite so the emitted caption stays a clean single "X v. Y"."""
    if not isinstance(contact, dict):
        return None
    label: str | None = None
    person = contact.get("person")
    company = contact.get("company")
    if isinstance(person, dict):
        label = (person.get("lastName") or "").strip() or None
    elif isinstance(company, dict):
        label = (company.get("name") or "").strip() or None
    if label is None:  # flat fallback
        label = (contact.get("lastName") or contact.get("name") or "").strip() or None
    if not label:
        return None
    # A party label is a name, not a caption or citation. If it already contains a
    # " v. " join or a reporter-cite-shaped number run, drop it (fail-safe: no
    # caption rather than a malformed/poisoned one).
    if re.search(r"\bv\.?\s", label, re.IGNORECASE) or re.search(r"\d{2,}", label):
        return None
    return label[:60]


def _orient_parties(matter: dict[str, Any]) -> tuple[str, list[str]] | None:
    """Return ``(plaintiff_contact_id, defendant_contact_ids)`` for the caption, or
    None when the matter has no two-sided caption (lead / missing party).

    The caption convention is *Plaintiff v. Defendant*. Orientation is derived from
    the matter-type side suffix ("... - Plaintiff" / "... - Defendant", present on
    both ``get_matter`` and ``list_matters`` items), NOT a hardcoded client=plaintiff
    assumption: for a plaintiff-side matter the firm's client is the plaintiff; for
    a defense-side matter the client is the defendant, so the caption flips."""
    clients = [c for c in (matter.get("clientIds") or []) if c]
    others = [o for o in (matter.get("otherSideIds") or []) if o]
    if not clients or not others:
        return None
    mt_name = ((matter.get("matterType") or {}).get("name") or "").strip().lower()
    if mt_name.endswith("defendant"):
        return others[0], clients  # firm defends; plaintiff is the other side
    return clients[0], others  # plaintiff-side (default): client is the plaintiff


def _contact_email(contact: Any) -> str | None:
    """The party's routable address, from the nested (``person``/``company``) shape
    confirmed live 2026-07-08 with a flat fallback. Structured fields only, lowered
    for comparison against a send's recipients."""
    if not isinstance(contact, dict):
        return None
    for holder in (contact.get("person"), contact.get("company"), contact):
        if not isinstance(holder, dict):
            continue
        raw = holder.get("email")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return None


def _contact_roles(contact: Any) -> list[str]:
    """Role-tag names on a contact (``[{"name": "Plaintiff", "type": "Role"}]``,
    live-confirmed 2026-08-10). Non-Role tags are ignored; shape drift yields an
    empty list, never a raise."""
    if not isinstance(contact, dict):
        return []
    out: list[str] = []
    for tag in contact.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        if (tag.get("type") or "") != "Role":
            continue
        name = (tag.get("name") or "").strip()
        if name:
            out.append(name[:40])
    return out


#: Where a role / relationship record can name its contact. Checked in order; an
#: unrecognized shape resolves nothing and the record is left untouched.
_ROLE_CONTACT_KEYS: tuple[str, ...] = ("contactId", "contact_id", "contact", "party")


def _role_contact_id(record: Any) -> str:
    """The contact id a role/relationship record refers to, or ``""``.

    Deliberately does NOT fall back to the record's own ``id``: that is the ROLE
    id, and resolving it as a contact would either 404 (harmless) or, worse,
    collide with a real contact id and attach a WRONG address to a matter. A
    wrong party is the one output this whole control exists to prevent.
    """
    if not isinstance(record, dict):
        return ""
    for key in _ROLE_CONTACT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("id")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _iter_role_records(resp: Any) -> list[dict]:
    """The role/relationship records in a response envelope.

    Handles the two shapes the connector sees elsewhere — a HATEOAS envelope
    (``{"value": [...]}``) and a bare list — plus a single record. An
    unrecognized shape yields nothing, which attaches nothing.
    """
    if isinstance(resp, dict):
        items = resp.get("value")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        return [resp]
    if isinstance(resp, list):
        return [i for i in resp if isinstance(i, dict)]
    return []
