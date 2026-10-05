"""The client's identifiers for a the vendor order, read from Smokeball in process.

A records order names the patient by legal name, Social Security number, date of
birth and address. Those live on the matter's client contact (the first of the
matter's ``clientIds``), and this module is the only place they are read. It
returns two things, deliberately separate:

* :class:`PatientFacts` holds the values themselves. It is passed straight to
  the vendor's request body and never serialized into a tool result;
* :meth:`PatientFacts.summary` is what a tool may return: the client's name, the
  last four digits of the SSN, and which required facts the record is missing,
  by NAME, never by value.

Nothing is defaulted from outside the record except the two values the vendor
requires that Smokeball has no field for: the patient's preferred language
(the requester's choice, English unless she says otherwise) and the address
country, which is ``US`` only when the record's own state is a two-letter US
state code.

Observed contact shape (a client tenant, 2026-10-05): ``person`` carries
``firstName`` / ``middleName`` / ``lastName``, ``identificationNumber``,
``birthDate`` (``YYYY-MM-DDT00:00:00``), ``residentialAddress`` (``addressLine1``,
``addressLine2``, ``city``, ``state``, ``zipCode``, ``country``), ``cell`` /
``phone`` as ``{areaCode, number}``, and ``email``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .client import _MATTER_NUMBERISH

_SSN_DIGITS = re.compile(r"^\d{9}$")
_US_STATE = re.compile(r"^[A-Za-z]{2}$")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_US_COUNTRY_WORDS = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"}
LANGUAGES = ("en", "es")


class OrderRefused(ValueError):
    """A records order this connector will not prepare or place. Nothing was ordered."""


@dataclass(frozen=True)
class PatientFacts:
    """The client as the order names them. Never returned by a tool."""

    first_name: str
    last_name: str
    middle_name: str
    ssn: str
    date_birth: str
    address: dict[str, str] = field(default_factory=dict)
    contact: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def ssn_last4(self) -> str:
        return self.ssn[-4:] if self.ssn else ""

    def summary(self) -> dict[str, Any]:
        """What a tool may say about the client: name, SSN last four, gaps by name."""
        return {
            "client_name": self.full_name,
            "ssn_last4": self.ssn_last4,
            "has_address": bool(self.address),
            "has_contact": bool(self.contact),
            "missing": list(self.missing),
        }

    def body(self, language: str) -> dict[str, Any]:
        """The vendor's ``patient`` object. Goes to the vendor and nowhere else."""
        name = {"first_name": self.first_name, "last_name": self.last_name}
        if self.middle_name:
            name["middle_name"] = self.middle_name
        out: dict[str, Any] = {
            "hipaa_type": "upload",
            "name": name,
            "ssn": self.ssn,
            "date_birth": self.date_birth,
            "language": language,
        }
        if self.address:
            out["address"] = dict(self.address)
        if self.contact:
            out["contact"] = dict(self.contact)
        return out


def require_matter_id(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if _MATTER_NUMBERISH.fullmatch(text) or not _UUID.match(text):
        raise OrderRefused(
            f"{value!r} is not a matter id (Smokeball matter ids are UUIDs). Resolve the matter "
            "first with list_matters. Nothing was ordered."
        )
    return text


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _ssn(raw: Any) -> str:
    digits = re.sub(r"[\s-]", "", _text(raw))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if _SSN_DIGITS.match(digits) else ""


def _birth_date(raw: Any) -> str:
    text = _text(raw)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _phone(person: dict[str, Any]) -> str:
    for key in ("cell", "phone"):
        value = person.get(key)
        if isinstance(value, dict) and _text(value.get("number")):
            area = _text(value.get("areaCode"))
            number = _text(value.get("number"))
            return f"({area}) {number}" if area else number
    return ""


def _country(addr: dict[str, Any]) -> str:
    raw = _text(addr.get("country"))
    if raw.casefold() in _US_COUNTRY_WORDS:
        return "US"
    if raw:
        return raw if len(raw) <= 4 else ""
    return "US" if _US_STATE.match(_text(addr.get("state"))) else ""


def _address(person: dict[str, Any]) -> dict[str, str]:
    """The vendor's address object, or {} when the record cannot fill its two
    required keys (city and a country code)."""
    for key in ("residentialAddress", "mailingAddress"):
        addr = person.get(key)
        if not isinstance(addr, dict):
            continue
        city, country = _text(addr.get("city")), _country(addr)
        if not (city and country):
            continue
        out = {"country": country, "city": city[:50]}
        for src, dst, limit in (("state", "state", 4), ("zipCode", "zip", 8), ("addressLine1", "street", 50)):
            value = _text(addr.get(src))
            if value:
                out[dst] = value[:limit]
        if _text(addr.get("addressLine2")):
            out["suite"] = _text(addr.get("addressLine2"))[:50]
        return out
    return {}


def patient_from_contact(contact: dict[str, Any]) -> PatientFacts:
    """Read the client contact. A gap is recorded by name in ``missing``."""
    raw = contact.get("person")
    person: dict[str, Any] = raw if isinstance(raw, dict) else {}
    first, last = _text(person.get("firstName")), _text(person.get("lastName"))
    ssn, dob = _ssn(person.get("identificationNumber")), _birth_date(person.get("birthDate"))
    email, phone = _text(person.get("email")), _phone(person)
    missing = [
        label
        for label, ok in (
            ("first name", first),
            ("last name", last),
            ("Social Security number (9 digits)", ssn),
            ("date of birth", dob),
        )
        if not ok
    ]
    return PatientFacts(
        first_name=first[:50],
        last_name=last[:50],
        middle_name=_text(person.get("middleName"))[:50],
        ssn=ssn,
        date_birth=dob,
        address=_address(person),
        # The vendor requires BOTH when a contact object is sent, so it is sent
        # only when the record holds both.
        contact={"email": email, "phone": phone} if (email and "@" in email and phone) else {},
        missing=tuple(missing),
    )


def read_matter_and_patient(client: Any, matter_id: str) -> tuple[dict[str, Any], PatientFacts]:
    """The matter (id, number) and its first client's facts. Refuses a matter
    with no client or a client that is not a person."""
    matter = client.get(f"/matters/{matter_id}")
    if not isinstance(matter, dict) or not matter:
        raise OrderRefused(f"matter {matter_id} could not be read. Nothing was ordered.")
    raw_ids = matter.get("clientIds")
    client_ids: list[Any] = raw_ids if isinstance(raw_ids, list) else []
    client_id = next((c for c in client_ids if isinstance(c, str) and c.strip()), "")
    if not client_id:
        raise OrderRefused("the matter names no client, so there is no patient to order for. Nothing was ordered.")
    contact = client.get(f"/contacts/{client_id}")
    if not isinstance(contact, dict) or not isinstance(contact.get("person"), dict):
        raise OrderRefused("the matter's client is not a person contact in Smokeball. Nothing was ordered.")
    facts = patient_from_contact(contact)
    number = _text(matter.get("number"))
    return {"id": matter_id, "number": number}, facts


__all__ = [
    "LANGUAGES",
    "OrderRefused",
    "PatientFacts",
    "patient_from_contact",
    "read_matter_and_patient",
    "require_matter_id",
]
