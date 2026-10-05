"""The facts a firm form letter prints, read from the matter's own record.

Split from ``form_letters`` so the tool module stays under the size ceiling.
Every value here is READ: the matter, its roles, its layouts, the contacts the
roles name, the responsible staff member and the firm's authored signer map.
Nothing is composed, defaulted or guessed. A fact the record does not hold is
a ``Fact`` with ``value=None`` and a ``missing`` description, and the caller
prints the marker ``[NOT IN THE FILE: <missing>]`` in its place, visibly.

OBSERVED SHAPES (a client tenant, 2026-10-05):

* The personal-injury layouts hold insurer and adjuster as RELATIONSHIP ids
  (``Matter/Plaintiffs/InsurancePolicy/Adjuster``), not contact ids:
  ``/contacts/{that id}`` answers 404. ``/matters/{id}/roles`` maps them: each
  role carries ``relationships: [{id, name, contactId}]``.
* A company contact carries ``name``, ``businessAddress`` (``addressLine1``,
  ``city``, ``state``, ``zipCode``), ``fax`` and ``phone`` as
  ``{areaCode, number}``, and ``email``. A blank address still carries
  ``state`` and ``country``, so an address needs its first line to count.
* ``Matter/CaseDetails/AccidentDetails/AccidentDate`` is ``YYYY-MM-DD``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from .medicals_layout import _items, layout_values

CONFIG_BLOCK = "form_letters"
ACCIDENT_DATE_KEY = "Matter/CaseDetails/AccidentDetails/AccidentDate"


@dataclass(frozen=True)
class Fact:
    value: str | None
    source: str
    missing: str = ""


def _absent(missing: str, source: str = "not in the record") -> Fact:
    return Fact(None, source, missing)


# ---- The authored signer map and the seat's clock ---------------------------


def _load_yaml(path: str | None) -> dict[str, Any]:
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            import yaml

            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable or unparseable config means nothing authored; every signer field then prints its marker
        return {}
    return data if isinstance(data, dict) else {}


def load_signers(path: str | None = None) -> dict[str, dict[str, str]]:
    """``form_letters.signers``: ``{staff full name or staff id: {name, title,
    initials}}``, keys normalized. How a staff member signs is the firm's to
    author; the staff record's own name ("Chris Price") is not it ("Christopher
    A. Price"), so an unauthored signer prints markers rather than a guess."""
    block = _load_yaml(path).get(CONFIG_BLOCK)
    signers = block.get("signers") if isinstance(block, dict) else None
    if not isinstance(signers, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, entry in signers.items():
        if isinstance(entry, dict):
            out[_key(key)] = {k: str(v).strip() for k, v in entry.items() if isinstance(v, str) and v.strip()}
    return out


def seat_today(path: str | None = None) -> date:
    """Today on the firm's clock (``business_hours.timezone``), else the host's."""
    hours = _load_yaml(path).get("business_hours")
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    if isinstance(zone, str) and zone:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(zone)).date()
        except Exception:  # noqa: BLE001 - an unknown zone name falls back to the host clock rather than refusing the letter
            pass
    return date.today()


def _key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def long_date(day: date) -> str:
    """``October 5, 2026``: the firm's letter date."""
    return f"{day.strftime('%B')} {day.day}, {day.year}"


# ---- Reading the matter ----------------------------------------------------


def matter_layout_values(client: Any, matter_id: str) -> dict[str, Any]:
    """Every layout value on the matter, merged; the first non-empty wins."""
    merged: dict[str, Any] = {}
    for item in _items(client.get(f"/matters/{matter_id}/layouts")):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") or item.get("itemId")
        if not isinstance(item_id, str):
            continue
        for key, value in layout_values(client.get(f"/matters/{matter_id}/layouts/{item_id}")).items():
            if merged.get(key) in (None, "") and value not in (None, ""):
                merged[key] = value
    return merged


def _roles(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return [r for r in resp["roles"] if isinstance(r, dict)]
    return [r for r in _items(resp) if isinstance(r, dict)]


def _contact_of(record: dict[str, Any]) -> str | None:
    cid = record.get("contactId")
    if not cid and isinstance(record.get("contact"), dict):
        cid = record["contact"].get("id")
    return cid if isinstance(cid, str) and cid and cid != "MyFirm" else None


@dataclass(frozen=True)
class Parties:
    """The role records, indexed the two ways a letter needs."""

    by_relationship: dict[str, str]
    roles: list[dict[str, Any]]

    def related(self, side_is_client: bool, relationship: str) -> str | None:
        """The contact named by ``relationship`` (e.g. "Insurer") under the
        client's role, or under the other side's."""
        flag = "isClient" if side_is_client else "isOtherSide"
        hits = [
            _contact_of(rel)
            for role in self.roles
            if role.get(flag) is True
            for rel in role.get("relationships") or []
            if isinstance(rel, dict) and _key(rel.get("name")) == _key(relationship)
        ]
        hits = [h for h in hits if h]
        return hits[0] if len(set(hits)) == 1 else None


def read_parties(client: Any, matter_id: str) -> Parties:
    roles = _roles(client.get(f"/matters/{matter_id}/roles"))
    index: dict[str, str] = {}
    for role in roles:
        for rel in role.get("relationships") or []:
            if isinstance(rel, dict) and isinstance(rel.get("id"), str) and _contact_of(rel):
                index[rel["id"]] = _contact_of(rel) or ""
    return Parties(by_relationship=index, roles=roles)


def related_contact(
    parties: Parties, layout: dict[str, Any], *, side: str, relationship: str, client_side: bool
) -> tuple[str | None, str]:
    """The contact id for a party relationship, and where it was read: the
    layout's relationship id first, then the role's relationship by name."""
    key = f"Matter/{side}/InsurancePolicy/{relationship}"
    rel_id = layout.get(key)
    if isinstance(rel_id, str) and rel_id in parties.by_relationship:
        return parties.by_relationship[rel_id], f"layout {key} -> matter role relationship"
    contact = parties.related(client_side, relationship)
    if contact:
        role = "client" if client_side else "other side"
        return contact, f"the {role} role's {relationship} relationship"
    return None, ""


# ---- Reading one contact ---------------------------------------------------


def contact_name(contact: dict[str, Any]) -> str | None:
    company = contact.get("company") if isinstance(contact.get("company"), dict) else {}
    person = contact.get("person") if isinstance(contact.get("person"), dict) else {}
    name = company.get("name") or " ".join(p for p in (person.get("firstName"), person.get("lastName")) if p)
    return name.strip() if isinstance(name, str) and name.strip() else None


def _body(contact: dict[str, Any]) -> dict[str, Any]:
    for key in ("company", "person"):
        if isinstance(contact.get(key), dict):
            return contact[key]
    return contact


def contact_fax(contact: dict[str, Any]) -> str | None:
    fax = _body(contact).get("fax")
    if not isinstance(fax, dict) or not str(fax.get("number") or "").strip():
        return None
    area = str(fax.get("areaCode") or "").strip()
    number = str(fax["number"]).strip()
    return f"({area}) {number}" if area else number


def contact_email(contact: dict[str, Any]) -> str | None:
    email = _body(contact).get("email")
    return email.strip() if isinstance(email, str) and "@" in email else None


def contact_address(contact: dict[str, Any]) -> str | None:
    """The mailing lines, ``\\n``-joined; None without a first line."""
    body = _body(contact)
    for key in ("businessAddress", "mailingAddress", "residentialAddress"):
        addr = body.get(key)
        if isinstance(addr, dict) and str(addr.get("addressLine1") or "").strip():
            lines = [str(addr["addressLine1"]).strip()]
            if str(addr.get("addressLine2") or "").strip():
                lines.append(str(addr["addressLine2"]).strip())
            city = str(addr.get("city") or "").strip()
            state_zip = " ".join(
                p for p in (str(addr.get("state") or "").strip(), str(addr.get("zipCode") or "").strip()) if p
            )
            last = ", ".join(p for p in (city, state_zip) if p)
            if last:
                lines.append(last)
            return "\n".join(lines)
    return None


def fetch_contact(client: Any, contact_id: str | None) -> dict[str, Any]:
    if not contact_id:
        return {}
    record = client.get(f"/contacts/{contact_id}")
    return record if isinstance(record, dict) else {}


# ---- Matter-level facts ----------------------------------------------------


def date_of_loss(layout: dict[str, Any]) -> Fact:
    raw = str(layout.get(ACCIDENT_DATE_KEY) or "")[:10]
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return _absent("date of loss")
    return Fact(day.strftime("%m/%d/%Y"), f"layout {ACCIDENT_DATE_KEY}")


def client_name(client: Any, matter: dict[str, Any]) -> Fact:
    ids = [c for c in matter.get("clientIds") or [] if isinstance(c, str)]
    if not ids:
        return _absent("client name")
    name = contact_name(fetch_contact(client, ids[0]))
    if not name:
        return _absent("client name")
    note = f" (first of {len(ids)} clients)" if len(ids) > 1 else ""
    return Fact(name, f"matter client contact{note}")


def signer_facts(client: Any, matter: dict[str, Any], signers: dict[str, dict[str, str]]) -> dict[str, Fact]:
    """name, title and initials for the matter's responsible staff member, from
    the authored signer map only."""
    staff_id = matter.get("personResponsibleStaffId")
    if not isinstance(staff_id, str) or not staff_id:
        return {f: _absent(f"signer {f} (no responsible staff on the matter)") for f in ("name", "title", "initials")}
    staff = client.get(f"/staff/{staff_id}")
    staff = staff if isinstance(staff, dict) else {}
    full = " ".join(p for p in (staff.get("firstName"), staff.get("lastName")) if isinstance(p, str) and p)
    entry = signers.get(_key(staff_id)) or signers.get(_key(full)) or {}
    who = full or "the responsible staff member"
    out: dict[str, Fact] = {}
    for f in ("name", "title", "initials"):
        value = entry.get(f)
        out[f] = (
            Fact(value, f"form_letters.signers for {who} (the matter's responsible staff)")
            if value
            else _absent(f"how {who} signs: {f}", "form_letters.signers")
        )
    return out


__all__ = [
    "ACCIDENT_DATE_KEY",
    "CONFIG_BLOCK",
    "Fact",
    "Parties",
    "client_name",
    "contact_address",
    "contact_email",
    "contact_fax",
    "contact_name",
    "date_of_loss",
    "fetch_contact",
    "load_signers",
    "long_date",
    "matter_layout_values",
    "read_parties",
    "related_contact",
    "seat_today",
    "signer_facts",
]
