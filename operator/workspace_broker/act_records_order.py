"""The vendor's records-order act: a payload the CALL carries, like the deletion.

The Smokeball connector's ``prepare_records_order`` builds the order (each
custodian resolved against the vendor's own directory, the HIPAA authorization
found on the matter, the client contact checked); ``place_records_order``
carries it, and the trust gate withholds that call at an authored
``commitment: confirm``. This module validates the order and renders it as the
one ``[act ...]`` line a Named Administrator answers. What keeps "yes" meaning
what she read is the same three things as the calendar deletion
(``act_event_set``): the line is rendered here from the stored order, the gate
replays the STORED order on the confirming turn, and the connector re-checks the
order against Smokeball before it sends anything.

One addition the deletion does not need: ``order_ref`` is a digest the
connector computed over every other field, and it is recomputed here. An order
the model edited after ``prepare`` built it is refused before anybody is asked.

The order holds no client identifier beyond the SSN's last four digits, which
the line shows so she can tell one client from another. The full SSN, date of
birth and address are read by the connector at placement and never reach this
broker, the act row, or the line.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .act_event_set import _shown
from .establishment_validation import EstablishmentValidationError

#: The order act's tool, as the overlay names it at runtime.
PLACE_ORDER_TOOL = "mcp_smokeball_place_records_order"

#: Acts whose payload rides on the call, with the exposure class a seat must
#: author at ``confirm`` before one can be proposed.
CALL_PAYLOAD_ACTS: dict[str, str] = {PLACE_ORDER_TOOL: "commitment"}

#: Mirrors the connector (``smokeball_connector/records_orders.py``).
ORDER_KEYS = (
    "order_ref",
    "vendor_name",
    "matter_id",
    "matter_number",
    "client_name",
    "ssn_last4",
    "order_by_email",
    "language",
    "hipaa_file_id",
    "hipaa_file_name",
    "pre_approved_custodian_fee",
    "order_certificate",
    "locations",
)
LOCATION_KEYS = ("custodian_id", "custodian_name", "custodian_address", "record_types", "service_start", "service_end")
RECORD_TYPES = ("Medical", "Billing", "Radiology Image", "Radiology Record", "EHR", "Other")
MAX_LOCATIONS = 10
MAX_CUSTODIAN_FEE = 10_000.0

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_FILE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DIGITS = re.compile(r"^\d{1,20}$")
_LAST4 = re.compile(r"^\d{4}$")
_REF = re.compile(r"^[0-9a-f]{32}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


def _refuse(why: str) -> EstablishmentValidationError:
    return EstablishmentValidationError(f"{PLACE_ORDER_TOOL}: {why}")


def _text(value: Any, field: str, limit: int, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _refuse(f"{field} must be a non-empty string of at most {limit} characters")
    if pattern is not None and not pattern.match(value):
        raise _refuse(f"{field} is not in the shape prepare_records_order writes")
    return value


def order_ref(order: dict[str, Any]) -> str:
    """The connector's digest, recomputed: every field but itself."""
    body = {k: order.get(k) for k in ORDER_KEYS if k != "order_ref"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def _require_location(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != set(LOCATION_KEYS):
        raise _refuse(f"{where} must carry exactly {list(LOCATION_KEYS)}")
    if raw["custodian_id"] is not None:
        _text(raw["custodian_id"], f"{where}.custodian_id", 20, pattern=_DIGITS)
    _text(raw["custodian_name"], f"{where}.custodian_name", 255)
    if raw["custodian_address"] is not None:
        _text(raw["custodian_address"], f"{where}.custodian_address", 255)
    types = raw["record_types"]
    if not isinstance(types, list) or not types or any(t not in RECORD_TYPES for t in types):
        raise _refuse(f"{where}.record_types must be drawn from {list(RECORD_TYPES)}")
    start = _text(raw["service_start"], f"{where}.service_start", 10, pattern=_DATE)
    end = _text(raw["service_end"], f"{where}.service_end", 10, pattern=_DATE)
    if start > end:
        raise _refuse(f"{where} starts after it ends")
    return raw


def require_order(value: Any) -> dict[str, Any]:
    """The ``{"order": {...}}`` payload, shape-checked and digest-checked.
    Refused by name, never repaired: a repaired order is one she was not shown."""
    if not isinstance(value, dict) or set(value) != {"order"}:
        raise _refuse("takes exactly one field, 'order', the object prepare_records_order returned")
    order = value["order"]
    if not isinstance(order, dict) or set(order) != set(ORDER_KEYS):
        raise _refuse(f"the order must carry exactly {list(ORDER_KEYS)}")
    _text(order["vendor_name"], "vendor_name", 60)
    _text(order["matter_id"], "matter_id", 40, pattern=_UUID)
    _text(order["matter_number"], "matter_number", 64)
    _text(order["client_name"], "client_name", 120)
    _text(order["ssn_last4"], "ssn_last4", 4, pattern=_LAST4)
    _text(order["order_by_email"], "order_by_email", 254, pattern=_EMAIL)
    _text(order["hipaa_file_id"], "hipaa_file_id", 64, pattern=_FILE_ID)
    _text(order["hipaa_file_name"], "hipaa_file_name", 255)
    if order["language"] not in ("en", "es") or order["order_certificate"] not in ("request", "no_request"):
        raise _refuse("language or order_certificate is not a value the vendor takes")
    fee = order["pre_approved_custodian_fee"]
    if isinstance(fee, bool) or not isinstance(fee, (int, float)) or not 0 <= fee <= MAX_CUSTODIAN_FEE:
        raise _refuse(f"pre_approved_custodian_fee must be 0 to {MAX_CUSTODIAN_FEE:,.0f}")
    locations = order["locations"]
    if not isinstance(locations, list) or not 1 <= len(locations) <= MAX_LOCATIONS:
        raise _refuse(f"an order carries 1 to {MAX_LOCATIONS} locations")
    for i, loc in enumerate(locations):
        _require_location(loc, f"locations[{i}]")
    if not (isinstance(order["order_ref"], str) and _REF.match(order["order_ref"])) or order["order_ref"] != order_ref(
        order
    ):
        raise _refuse("the order was changed after prepare_records_order built it; prepare it again")
    return {"order": order}


def _types(values: list[str]) -> str:
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + f" and {values[-1]}"


def _location_line(n: int, loc: dict[str, Any]) -> str:
    name = _shown(loc["custodian_name"], 120)
    where = f", {_shown(loc['custodian_address'], 120)}" if loc["custodian_address"] else ""
    known = f"directory id {loc['custodian_id']}" if loc["custodian_id"] else "not in the vendor's directory yet"
    return (
        f"{n}) {name} ({known}{where}): {_types(loc['record_types'])}, {loc['service_start']} to {loc['service_end']}"
    )


def order_readback(payload: dict[str, Any]) -> str:
    """The order as one line she answers: who, which matter, every facility with
    its records and dates, the authorization, the fee and the certification."""
    order = payload["order"]
    lines = "; ".join(_location_line(i, loc) for i, loc in enumerate(order["locations"], start=1))
    cert = "certification requested" if order["order_certificate"] == "request" else "certification not requested"
    noun = "facility" if len(order["locations"]) == 1 else "facilities"
    return (
        f"Place a medical-records order with {_shown(order['vendor_name'], 60)} on matter "
        f"{_shown(order['matter_number'], 64)} for "
        f"{_shown(order['client_name'], 120)} (SSN ending {order['ssn_last4']}), ordered by "
        f"{_shown(order['order_by_email'], 254)}, {len(order['locations'])} {noun}: {lines}. Authorization, with the HIPAA "
        f"form uploaded from the matter ('{_shown(order['hipaa_file_name'], 120)}'); pre-approved custodian fee "
        f"${float(order['pre_approved_custodian_fee']):,.2f}; {cert}. "
        'Reply "yes, place it" to proceed.'
    )


def proposed_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {"location_count": len(payload["order"]["locations"]), "order_ref": payload["order"]["order_ref"]}


def committed_metadata(payload: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Counts and the vendor's order id (the ``ref`` the seat's post-call hook
    forwards), bounded; nothing else the vendor wrote is kept."""
    ref = outcome.get("ref") if isinstance(outcome, dict) else None
    order_id = ref if isinstance(ref, str) and _UUID.match(ref) else None
    return {**proposed_metadata(payload), "vendor_order_record_id": order_id}


__all__ = [
    "CALL_PAYLOAD_ACTS",
    "ORDER_KEYS",
    "PLACE_ORDER_TOOL",
    "committed_metadata",
    "order_readback",
    "order_ref",
    "proposed_metadata",
    "require_order",
]
