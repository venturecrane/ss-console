"""Place a prepared the vendor order: the COMMITMENT half (see ``records_orders``).

Reached only through the admin-confirmed act: the trust gate replays the order
the broker stored when the administrator was asked, so ``order`` here is the
order she read. This module still re-checks it, because the check is cheap and
the alternative is trusting a payload's provenance:

1. the shape is exactly the one ``prepare`` builds, and ``order_ref`` still
   matches every other field (an edited order is refused, nothing sent);
2. the matter number, client name and SSN last four re-read from Smokeball still
   equal what the act line showed;
3. the HIPAA file is still on the matter and is a PDF the vendor will take.

Then ``_new`` -> PATCH -> upload -> validate -> finish. The client's identifiers
go from the Smokeball contact straight into the request body and appear in no
return value and no error.
"""

from __future__ import annotations

import re
from typing import Any

from .records_vendor import MAX_UPLOAD_BYTES, RecordsVendorApiError, scrub
from .records_orders import (
    LOCATION_KEYS,
    MAX_CUSTODIAN_FEE,
    MAX_LOCATIONS,
    ORDER_KEYS,
    RECORD_TYPES,
    OrderNotPlaced,
    order_ref,
)
from .records_patient import LANGUAGES, OrderRefused, read_matter_and_patient, require_matter_id

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _refuse(why: str) -> OrderRefused:
    return OrderRefused(f"{why} Nothing was ordered.")


def _require_location(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != set(LOCATION_KEYS):
        raise _refuse(f"every location must carry exactly {list(LOCATION_KEYS)}, as prepare returned it.")
    cid = raw["custodian_id"]
    if cid is not None and not (isinstance(cid, str) and cid.isdigit()):
        raise _refuse("a custodian_id is the vendor's numeric id, or null for a new custodian.")
    if not isinstance(raw["custodian_name"], str) or not raw["custodian_name"].strip():
        raise _refuse("every location needs its custodian name.")
    types = raw["record_types"]
    if not isinstance(types, list) or not types or any(t not in RECORD_TYPES for t in types):
        raise _refuse("a location's record_types are not ones the vendor takes.")
    if not all(isinstance(raw[k], str) and _DATE.match(raw[k]) for k in ("service_start", "service_end")):
        raise _refuse("a location's dates must be YYYY-MM-DD.")
    return raw


def require_order(order: Any) -> dict[str, Any]:
    """The order exactly as ``prepare`` built it, digest intact."""
    if not isinstance(order, dict) or set(order) != set(ORDER_KEYS):
        raise _refuse("order must be the object prepare_records_order returned, unchanged.")
    require_matter_id(order["matter_id"])
    locations = order["locations"]
    if not isinstance(locations, list) or not 1 <= len(locations) <= MAX_LOCATIONS:
        raise _refuse(f"an order carries 1 to {MAX_LOCATIONS} locations.")
    for location in locations:
        _require_location(location)
    fee = order["pre_approved_custodian_fee"]
    if isinstance(fee, bool) or not isinstance(fee, (int, float)) or not 0 <= fee <= MAX_CUSTODIAN_FEE:
        raise _refuse("the pre-approved custodian fee is out of range.")
    if order["language"] not in LANGUAGES or order["order_certificate"] not in ("request", "no_request"):
        raise _refuse("the order's language or certification setting is not one the vendor takes.")
    if not isinstance(order["vendor_name"], str) or not 0 < len(order["vendor_name"]) <= 60:
        raise _refuse("the order's vendor name is missing or too long.")
    if order["order_ref"] != order_ref(order):
        raise _refuse("the order was changed after it was prepared; prepare it again.")
    return order


def _location_body(loc: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "record_types": list(loc["record_types"]),
        "service_start": loc["service_start"],
        "service_end": loc["service_end"],
    }
    if loc["custodian_id"]:
        body["custodian_id"] = loc["custodian_id"]
    else:
        # A custodian the vendor does not know yet: by name, with the address
        # she gave, so its team can register it. The vendor's spec also offers a
        # free-text note field for this; its key spells the vendor's name, which
        # this public tree does not, so the address rides in the name instead
        # (255 characters, the field's limit).
        name = loc["custodian_name"]
        if loc["custodian_address"]:
            name = f"{name} ({loc['custodian_address']})"
        body["custodian_name"] = name[:255]
    return body


def _matter_body(order: dict[str, Any]) -> dict[str, Any]:
    number = order["matter_number"]
    return {
        "matter_type": "smokeball",
        "matter_id": order["matter_id"],
        "matter_name": " ".join(p for p in (number, order["client_name"]) if p),
        "case_number": number,
        "order_type": "authorization",
        "order_certificate": order["order_certificate"],
        "retrieval_type": "default",
        "use_yipaa_form": False,
        "pre_approved_custodian_fee": float(order["pre_approved_custodian_fee"]),
    }


def _hipaa_pdf(client: Any, order: dict[str, Any]) -> tuple[str, bytes]:
    try:
        _info, blob = client.download_file(order["matter_id"], order["hipaa_file_id"])
    except Exception as exc:
        raise _refuse(f"the HIPAA authorization could not be read from the matter ({type(exc).__name__}).") from exc
    if not blob.startswith(b"%PDF"):
        raise _refuse("the HIPAA authorization on the matter is not a PDF.")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise _refuse("the HIPAA authorization is over the vendor's 10 MB upload limit.")
    return order["hipaa_file_name"], blob


def _recheck(client: Any, order: dict[str, Any]) -> Any:
    matter, facts = read_matter_and_patient(client, order["matter_id"])
    if facts.missing:
        raise _refuse(f"the client contact is now missing: {', '.join(facts.missing)}.")
    shown = (order["matter_number"], order["client_name"], order["ssn_last4"])
    if (matter["number"], facts.full_name, facts.ssn_last4) != shown:
        raise _refuse("the matter or client in Smokeball no longer matches what the order showed; prepare it again.")
    return facts


def _side_notes(*responses: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for resp in responses:
        for note in resp.get("side_notes") or []:
            notes.append(scrub(note))
    return notes


def _vendor_step(step: str, oid: str, call: Any) -> dict[str, Any]:
    try:
        return call()
    except RecordsVendorApiError as exc:
        raise OrderNotPlaced(
            f"The vendor refused the {step} step (HTTP {exc.status}: {exc.detail}). The order was NOT placed: "
            f"draft {oid} is left unfinished at the vendor and nothing was sent to any custodian."
        ) from exc


def place(client: Any, yc: Any, order: Any) -> dict[str, Any]:
    """Place the order. Raises on every failure; returns only when finished."""
    spec = require_order(order)
    facts = _recheck(client, spec)
    filename, blob = _hipaa_pdf(client, spec)
    body = {
        "order_by_email": spec["order_by_email"],
        "matter": _matter_body(spec),
        "patient": facts.body(spec["language"]),
    }
    try:
        created = yc.create_order(body)
    except RecordsVendorApiError as exc:
        raise OrderNotPlaced(
            f"The vendor refused the order (HTTP {exc.status}: {exc.detail}). Nothing was ordered."
        ) from exc
    oid = str(created.get("order_record_id") or "")
    if not oid:
        raise OrderNotPlaced("The vendor accepted the order but returned no order id. Nothing was finished.")
    locations = [_location_body(loc) for loc in spec["locations"]]
    patched = _vendor_step("locations", oid, lambda: yc.patch_order(oid, {"locations": locations}))
    _vendor_step("HIPAA upload", oid, lambda: yc.upload(oid, filename, blob, "completed_hipaa_form"))
    validated = _vendor_step("validation", oid, lambda: yc.validate(oid))
    finished = _vendor_step("finish", oid, lambda: yc.finish(oid))
    return {
        # ``id`` first: the trust plugin records the act's reference from it.
        "id": oid,
        "status": "placed",
        "matter_number": spec["matter_number"],
        "locations": [loc["custodian_name"] for loc in spec["locations"]],
        "message": scrub(finished.get("message") or validated.get("message") or ""),
        "side_notes": _side_notes(created, patched, validated, finished),
        "next_step": "Read it back with records_orders_for_matter; the vendor assigns request numbers as it processes.",
    }


__all__ = ["place", "require_order"]
