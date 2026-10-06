"""Order medical records from the records vendor for a Smokeball matter, as an admin-confirmed act.

THE SAME SHAPE AS THE CALENDAR DELETION (``event_delete.py``), for the same
reason: the act's payload is what a Named Administrator reads and answers, so
every value in it is read or resolved here, not composed by the model.

* ``prepare`` (READ) resolves each facility against the vendor's own custodian
  directory, finds the signed HIPAA authorization on the matter, checks the
  client contact holds what an order needs, and returns the ORDER: a plain spec
  with no identifier in it beyond the SSN's last four digits. It orders nothing.
* ``place`` (COMMITMENT) takes that order. On a seat that authors
  ``commitment: confirm`` the trust gate WITHHOLDS the call, the broker renders
  the order as one ``[act ...]`` line (``workspace_broker/act_records_order.py``),
  and only the administrator's emailed yes replays the stored order into this
  function. Then, and only then, the client's SSN, date of birth and address are
  read from Smokeball in process and sent to the vendor with the order.

What keeps "yes" meaning what she read: the broker renders the line from the
stored order and the gate replays the stored order, never the model's re-send;
``order_ref`` is a digest of every other field, so an order edited after
``prepare`` built it is refused here; and the matter number, client name and SSN
last four are re-read and must still equal what the line showed.

The vendor sequence is ``_new`` (matter + patient) -> PATCH (locations) ->
upload (the HIPAA PDF, fetched from Smokeball by file id, in process) ->
validate -> finish. A failure at any step after ``_new`` leaves an unfinished
DRAFT at the vendor and is raised, so nothing reads as placed and the trust
plugin commits no act row; a validate failure finishes nothing.
"""

from __future__ import annotations

import dataclasses

import hashlib
import json
import os
import re
from datetime import date
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from .records_vendor import MAX_UPLOAD_BYTES, RecordsVendorApiError, scrub
from .records_patient import LANGUAGES, OrderRefused, read_matter_and_patient, require_matter_id

RECORD_TYPES = ("Medical", "Billing", "Radiology Image", "Radiology Record", "EHR", "Other")
#: The firm's observed practice on its own portal orders (67 of 67 confirmation
#: emails): authorization, certification not requested, Medical and Billing, a
#: $100.00 pre-approved custodian fee. Shown in the read-back for her to change.
DEFAULT_RECORD_TYPES = ("Medical", "Billing")
# The fee is the FIRM'S standing term, authored per seat in customer.yaml
# (records_orders.pre_approved_custodian_fee), never a code default: a figure
# the administrator approves has to be one the firm set. It is also returned as
# text ("$100.00") because the outbound fabrication gate admits a dollar figure
# in the reply only when a read this turn carried it in that form; a bare 100.0
# seeds nothing and the administrator's read-back is held (2026-10-05).
FEE_CONFIG_BLOCK = "records_orders"
FEE_CONFIG_KEY = "pre_approved_custodian_fee"
#: How the client's HIPAA authorization reaches the vendor, authored per firm:
#: ``upload`` (a signed authorization PDF on the matter) or ``e_auth`` (the
#: vendor emails the client its own authorization to sign; nothing uploaded).
#: A&P chose e_auth on 2026-10-06: its retainer packets' authorization pages
#: are signed but otherwise blank. Unauthored reads as ``upload``.
AUTH_CONFIG_KEY = "authorization"
AUTH_MODES = ("upload", "e_auth")
MAX_CUSTODIAN_FEE = 10_000.0
MAX_LOCATIONS = 10
MAX_YEARS = 20
MAX_CANDIDATES = 8

ORDER_KEYS = (
    "order_ref",
    "vendor_name",
    "matter_id",
    "matter_number",
    "client_name",
    "ssn_last4",
    "order_by_email",
    "language",
    "authorization",
    "esign_to",
    "hipaa_file_id",
    "hipaa_file_name",
    "pre_approved_custodian_fee",
    "order_certificate",
    "locations",
)
LOCATION_KEYS = ("custodian_id", "custodian_name", "custodian_address", "record_types", "service_start", "service_end")

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_HIPAA_NAME = re.compile(r"hipaa|authori[sz]ation", re.IGNORECASE)
_FILE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_CUSTODIAN_ID = re.compile(r"^\d{1,20}$")
_PAGE = 500
_MAX_PAGES = 20


class OrderNotPlaced(RuntimeError):
    """The vendor refused a step. Nothing was finished; a draft may remain."""


# ---- the order spec ---------------------------------------------------------
def order_ref(order: dict[str, Any]) -> str:
    """A digest of every field but itself: the order as it was prepared."""
    body = {k: order.get(k) for k in ORDER_KEYS if k != "order_ref"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise OrderRefused(f"{field} must be a date written YYYY-MM-DD. Nothing was ordered.") from exc


def _years_back(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def service_range(spec: dict[str, Any], default: dict[str, Any], today: date) -> tuple[str, str]:
    """``(start, end)`` from a facility's own years/start/end, else the order's.
    ``end`` omitted or ``present`` is today; nothing may run past today."""
    years = spec.get("years", default.get("years"))
    start_raw = spec.get("service_start", default.get("service_start"))
    end_raw = spec.get("service_end", default.get("service_end"))
    end = today if end_raw in (None, "", "present") else _date(end_raw, "service_end")
    if start_raw not in (None, ""):
        start = _date(start_raw, "service_start")
    elif isinstance(years, int) and not isinstance(years, bool) and 1 <= years <= MAX_YEARS:
        start = _years_back(end, years)
    else:
        raise OrderRefused(
            f"each facility needs a date range: years (1 to {MAX_YEARS}) or service_start. Nothing was ordered."
        )
    if start > end or end > today:
        raise OrderRefused("the date range must start before it ends and end no later than today. Nothing was ordered.")
    return start.isoformat(), end.isoformat()


def record_types(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_RECORD_TYPES)
    if not isinstance(value, list) or not value or any(v not in RECORD_TYPES for v in value):
        raise OrderRefused(
            f"record_types must be a non-empty list drawn from {list(RECORD_TYPES)}. Nothing was ordered."
        )
    return list(dict.fromkeys(value))


def authored_custodian_fee(path: str | None = None) -> Any:
    """The seat's authored standing fee, or None when the firm authored none
    (or the config cannot be read: "cannot tell" never becomes a figure)."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable config means nothing authored
        return None
    block = cfg.get(FEE_CONFIG_BLOCK) if isinstance(cfg, dict) else None
    return block.get(FEE_CONFIG_KEY) if isinstance(block, dict) else None


def authored_authorization(path: str | None = None) -> str:
    """``records_orders.authorization``: ``e_auth`` or ``upload`` (the default,
    and what anything unreadable or unrecognized reads as)."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable config: the long-standing upload mode
        return "upload"
    block = cfg.get(FEE_CONFIG_BLOCK) if isinstance(cfg, dict) else None
    mode = block.get(AUTH_CONFIG_KEY) if isinstance(block, dict) else None
    return mode if mode in AUTH_MODES else "upload"


def custodian_fee(value: Any, authored: Any = None) -> float:
    if value is None:
        value = authored
    if value is None:
        raise OrderRefused(
            "No pre-approved custodian fee: the firm has not authored one "
            f"({FEE_CONFIG_BLOCK}.{FEE_CONFIG_KEY}) and the request named none. Ask the requester for the amount."
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_CUSTODIAN_FEE:
        raise OrderRefused(f"pre_approved_custodian_fee must be a dollar amount from 0 to {MAX_CUSTODIAN_FEE:,.0f}.")
    return round(float(value), 2)


# ---- the HIPAA authorization on the matter ----------------------------------
def _matter_files(client: Any, matter_id: str) -> list[dict[str, Any]]:
    """Every file on the matter, paged in full. A failed page RAISES: a partial
    listing must never read as "no authorization on file"."""
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        resp = client.get(f"/matters/{matter_id}/documents/files", Limit=_PAGE, Offset=page * _PAGE)
        batch = resp.get("value") if isinstance(resp, dict) else resp
        batch = [r for r in batch if isinstance(r, dict)] if isinstance(batch, list) else []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
    raise OrderRefused("the matter holds too many files to search for the authorization in full. Nothing was ordered.")


def _is_pdf(row: dict[str, Any]) -> bool:
    ext = _norm(row.get("fileExtension")).lstrip(".")
    return ext == "pdf" or (not ext and _norm(row.get("name")).endswith(".pdf"))


def _file_label(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").strip()
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def find_hipaa(client: Any, matter_id: str, file_id: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """``(the file, [])`` when one authorization is settled, else ``(None, candidates)``."""
    files = [r for r in _matter_files(client, matter_id) if isinstance(r.get("id"), str)]
    if file_id not in (None, ""):
        chosen = next((r for r in files if r["id"] == file_id), None)
        if chosen is None or not _FILE_ID.match(str(file_id)):
            raise OrderRefused(f"file {file_id!r} is not on this matter. Nothing was ordered.")
        if not _is_pdf(chosen):
            raise OrderRefused("The vendor accepts the authorization only as a PDF; that file is not one.")
        return chosen, []
    named = [r for r in files if _is_pdf(r) and _HIPAA_NAME.search(str(r.get("name") or ""))]
    named.sort(key=lambda r: str(r.get("dateCreated") or ""), reverse=True)
    if len(named) == 1:
        return named[0], []
    return None, [
        {"file_id": r["id"], "name": _file_label(r), "date": str(r.get("dateCreated") or "")[:10]}
        for r in named[:MAX_CANDIDATES]
    ]


# ---- the custodian, from the vendor's own directory ---------------------------
def _candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("id") in (None, ""):
        return None
    zip_ = str(row.get("postalcode") or "").strip()
    place = ", ".join(p for p in (str(row.get(k) or "").strip() for k in ("street", "city")) if p)
    region = " ".join(p for p in (str(row.get("state") or "").strip(), zip_) if p)
    return {
        "custodian_id": str(row["id"]),
        "name": scrub(row.get("value") or row.get("name") or ""),
        "address": scrub(", ".join(p for p in (place, region) if p)),
    }


def resolve_facility(yc: Any, facility: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(location, None)`` when settled, else ``(None, question)``.

    Settled means: exactly one directory match for the name (and ZIP, if given);
    or the custodian_id she chose IS among the directory's matches for the name;
    or ``new_custodian`` true, which orders it by name for the vendor to register.
    """
    name = str(facility.get("name") or "").strip()
    if not name or len(name) > 255:
        raise OrderRefused("every facility needs its name (up to 255 characters). Nothing was ordered.")
    address = scrub(facility.get("address") or "")[:255]
    if facility.get("new_custodian") is True:
        return {"custodian_id": None, "custodian_name": name, "custodian_address": address or None}, None
    rows = yc.get_locations(name, str(facility.get("zip") or "").strip() or None)
    found = [c for c in (_candidate(r) for r in rows) if c is not None]
    wanted = str(facility.get("custodian_id") or "").strip()
    if wanted:
        if not _CUSTODIAN_ID.match(wanted):
            raise OrderRefused(f"custodian_id {wanted!r} is not a the vendor custodian id. Nothing was ordered.")
        found = [c for c in found if c["custodian_id"] == wanted]
    if len(found) == 1:
        c = found[0]
        return {
            "custodian_id": c["custodian_id"],
            "custodian_name": c["name"] or name,
            "custodian_address": c["address"] or None,
        }, None
    reason = (
        "no location in the vendor's directory matches this name"
        if not found
        else f"{len(found)} locations in the vendor's directory match this name"
    )
    if wanted:
        reason = f"custodian {wanted} is not among the directory matches for this name"
    return None, {"facility": name, "reason": reason, "candidates": found[:MAX_CANDIDATES]}


# ---- prepare (READ) ---------------------------------------------------------
def _require_request(facilities: Any, order_by_email: Any, language: Any) -> tuple[list[dict[str, Any]], str, str]:
    if not isinstance(facilities, list) or not facilities or len(facilities) > MAX_LOCATIONS:
        raise OrderRefused(f"facilities must list 1 to {MAX_LOCATIONS} facilities, one per entry. Nothing was ordered.")
    if any(not isinstance(f, dict) for f in facilities):
        raise OrderRefused("every facility must be an object with at least its name. Nothing was ordered.")
    email = str(order_by_email or "").strip()
    if not _EMAIL.match(email):
        raise OrderRefused("order_by_email must be the vendor's portal user placing the order. Nothing was ordered.")
    lang = str(language or "en").strip().lower()
    if lang not in LANGUAGES:
        raise OrderRefused(f"language must be one of {list(LANGUAGES)}.")
    return facilities, email, lang


def prepare(client: Any, yc: Any, request: dict[str, Any], today: date) -> dict[str, Any]:
    """Build the order, or say exactly what has to be settled first. Orders nothing."""
    matter_id = require_matter_id(request.get("matter_id"))
    facilities, email, lang = _require_request(
        request.get("facilities"), request.get("order_by_email"), request.get("language")
    )
    default = {k: request.get(k) for k in ("years", "service_start", "service_end")}
    types, fee = (
        record_types(request.get("record_types")),
        custodian_fee(request.get("pre_approved_custodian_fee"), authored_custodian_fee()),
    )
    matter, facts = read_matter_and_patient(client, matter_id)
    auth = authored_authorization()
    if auth == "e_auth" and not facts.esign_to and "email or cell phone" not in facts.missing:
        facts = dataclasses.replace(facts, missing=(*facts.missing, "email or cell phone (for the signing request)"))
    if facts.missing:
        return {
            "status": "missing_client_facts",
            "matter_number": matter["number"],
            "client": facts.summary(),
            "next_step": "Smokeball's client contact is missing what the vendor requires (listed by name). Ask the "
            "requester to complete it in Smokeball, then prepare again. Nothing was ordered.",
        }
    hipaa, hipaa_choices = (
        (None, []) if auth == "e_auth" else find_hipaa(client, matter_id, request.get("hipaa_file_id"))
    )
    locations, questions = [], []
    for facility in facilities:
        location, question = resolve_facility(yc, facility)
        if question is not None or location is None:
            questions.append(question or {"facility": str(facility.get("name") or "")})
            continue
        start, end = service_range(facility, default, today)
        location.update(
            {
                "record_types": record_types(facility.get("record_types")) if "record_types" in facility else types,
                "service_start": start,
                "service_end": end,
            }
        )
        locations.append(location)
    if questions or (hipaa is None and auth == "upload"):
        return {
            "status": "needs_choice",
            "matter_number": matter["number"],
            "facilities": questions,
            "hipaa_candidates": hipaa_choices if (hipaa is None and auth == "upload") else [],
            "next_step": "Ask the requester to settle each item (a facility from its candidates, or new_custodian "
            "true with its address; the authorization by file_id), then prepare again. Nothing was ordered.",
        }
    order: dict[str, Any] = {
        "vendor_name": str(request.get("vendor_name") or "the records vendor")[:60],
        "matter_id": matter_id,
        "matter_number": matter["number"],
        "client_name": facts.full_name,
        "ssn_last4": facts.ssn_last4,
        "order_by_email": email,
        "language": lang,
        "authorization": auth,
        "esign_to": facts.esign_label if auth == "e_auth" else None,
        "hipaa_file_id": hipaa["id"] if hipaa else None,
        "hipaa_file_name": _file_label(hipaa) if hipaa else None,
        "pre_approved_custodian_fee": fee,
        "order_certificate": "request" if request.get("certification") is True else "no_request",
        "locations": locations,
    }
    order["order_ref"] = order_ref(order)
    return {
        "status": "ready",
        "order": {k: order[k] for k in ORDER_KEYS},
        "client": facts.summary(),
        "pre_approved_custodian_fee_shown": f"${fee:,.2f}",
        "next_step": "Pass `order` to place_records_order exactly as returned. Nothing has been ordered."
        + (
            f" When it is placed, the vendor sends {facts.full_name} its own authorization to sign at "
            f"{facts.esign_label}; the records are requested once it is signed."
            if auth == "e_auth"
            else ""
        ),
    }


__all__ = [
    "AUTH_MODES",
    "authored_authorization",
    "authored_custodian_fee",
    "DEFAULT_RECORD_TYPES",
    "LOCATION_KEYS",
    "MAX_LOCATIONS",
    "MAX_UPLOAD_BYTES",
    "ORDER_KEYS",
    "RECORD_TYPES",
    "OrderNotPlaced",
    "OrderRefused",
    "RecordsVendorApiError",
    "find_hipaa",
    "order_ref",
    "prepare",
    "resolve_facility",
    "service_range",
]
