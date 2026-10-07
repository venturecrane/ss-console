"""The three the vendor records-order tools on the Smokeball connector.

* ``prepare_records_order`` (READ): builds the order, orders nothing.
* ``place_records_order`` (COMMITMENT): places it, only on an administrator's
  emailed yes to the ``[act ...]`` line the seat sends when it withholds the call.
* ``records_orders_for_matter`` (READ): what the vendor holds for a matter.

Every tool says plainly when no the vendor token is staged on the seat. Registered
through ``attachment_tools.register`` (the one registrar the size-ratcheted
``server.py`` already calls), so ``server.py`` does not grow.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from .records_vendor import (
    NOT_CONNECTED,
    RecordsVendorApiError,
    RecordsVendorNotConnected,
    client_from_env,
    scrub,
    vendor_name,
)
from .records_orders import prepare
from .records_patient import require_matter_id
from .records_place import place


def _sb() -> Any:
    from . import server

    return server._get_client()


def _not_connected() -> dict[str, Any]:
    return {"status": "not_connected", "message": NOT_CONNECTED}


def _today() -> date:
    """Today on the firm's clock (``business_hours.timezone``), else the host's.
    A records range ending "today" must not end tomorrow in UTC."""
    try:
        import yaml
        from zoneinfo import ZoneInfo

        path = os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
        with open(path, encoding="utf-8") as fh:
            zone = ((yaml.safe_load(fh) or {}).get("business_hours") or {}).get("timezone")
        if not isinstance(zone, str) or not zone:
            return date.today()
        return datetime.now(ZoneInfo(zone)).date()
    except Exception:  # noqa: BLE001 - no readable zone falls back to the host clock rather than refusing the order
        return date.today()


def prepare_records_order(
    matter_id: str,
    facilities: list[dict[str, Any]] | None = None,
    order_by_email: str = "",
    years: int | None = None,
    service_start: str | None = None,
    service_end: str | None = None,
    record_types: list[str] | None = None,
    hipaa_file_id: str | None = None,
    pre_approved_custodian_fee: float | None = None,
    certification: bool = False,
    language: str = "en",
    from_gap_audit: bool = False,
) -> Any:
    """Build a medical-records order for one matter. Orders NOTHING.

    ``from_gap_audit`` true ("order the missing records" on a demand package):
    leave ``facilities`` out; the orders are built from the matter's newest
    filed Gap Audit (gap_orders.py): its orderable rows grouped by provider,
    each provider located from the file's own documents and the vendor's
    directory, the matched ones ordered (10 to an order). Returns
    ``would_order`` (what will be ordered), ``orders`` (each ``ready`` one goes
    to ``place_records_order`` unchanged), and at most ONE ``question`` for
    whatever the file could not settle.

    ``facilities``: one entry per facility, each ``{"name": ...}`` plus any of
    ``zip`` (narrows the directory search), ``custodian_id`` (the requester's
    pick from a previous ``candidates`` list), ``new_custodian`` (true only when
    she says it is not in the vendor's directory; give its ``address``), and its
    own ``years`` / ``service_start`` / ``service_end`` / ``record_types``.
    The order-level ``years`` (or ``service_start``; the end defaults to today)
    and ``record_types`` (default Medical and Billing) apply to every facility
    that does not name its own. ``order_by_email`` is the vendor's portal user
    placing the order (the requester). ``pre_approved_custodian_fee`` defaults
    to the firm's usual $100.00; ``certification`` defaults to not requested.

    Returns ``status``: ``ready`` with ``order`` (pass it to
    ``place_records_order`` unchanged), ``needs_choice`` (a facility matched
    several or no directory locations, or the HIPAA authorization is not
    settled: ask the requester), ``missing_client_facts`` (the client contact
    lacks what the vendor requires, listed by name), or ``not_connected``.
    The client's SSN, date of birth and address are never returned; only the
    SSN's last four digits, for the read-back."""
    yc = client_from_env()
    if yc is None:
        return _not_connected()
    if from_gap_audit:
        from .gap_orders import build

        try:
            require_matter_id(matter_id)
            return build(_sb(), yc, matter_id, order_by_email, _today(), vendor_name())
        except RecordsVendorApiError as exc:
            return {
                "status": "refused",
                "reason": f"The vendor's directory could not be searched (HTTP {exc.status}: {exc.detail}).",
            }
    request = {
        "matter_id": matter_id,
        "facilities": facilities,
        "order_by_email": order_by_email,
        "years": years,
        "service_start": service_start,
        "service_end": service_end,
        "record_types": record_types,
        "hipaa_file_id": hipaa_file_id,
        "pre_approved_custodian_fee": pre_approved_custodian_fee,
        "certification": certification,
        "language": language,
        "vendor_name": vendor_name(),
    }
    try:
        return prepare(_sb(), yc, request, _today())
    except RecordsVendorApiError as exc:
        return {
            "status": "refused",
            "reason": f"The vendor's directory could not be searched (HTTP {exc.status}: {exc.detail}).",
        }


def place_records_order(order: dict[str, Any]) -> Any:
    """Place the records order ``prepare_records_order`` returned, unchanged.

    A Named Administrator confirms this first: the seat withholds the call and
    hands back an ``[act ...]`` line stating the whole order; only her yes to that
    line places it. Then the client's identifiers are read from Smokeball and
    sent to the vendor with the order, the HIPAA authorization is uploaded from the
    matter, and the order is validated and finished. A refusal at any step
    finishes nothing and says so. Returns the vendor's order id on success."""
    yc = client_from_env()
    if yc is None:
        raise RecordsVendorNotConnected()
    return place(_sb(), yc, order)


def records_orders_for_matter(matter_id: str) -> Any:
    """What the vendor holds for this Smokeball matter: each location with its
    request number and status. The read-back proof after an order is placed."""
    yc = client_from_env()
    if yc is None:
        return _not_connected()
    mid = require_matter_id(matter_id)
    try:
        found = yc.orders_for_matter(mid)
    except RecordsVendorApiError as exc:
        if exc.status == 404:
            return {
                "status": "none",
                "matter_id": mid,
                "locations": [],
                "note": "The vendor answered 404: it holds no order linked to this matter.",
            }
        return {"status": "refused", "reason": f"The vendor could not be read (HTTP {exc.status}: {exc.detail})."}
    raw = found.get("locations")
    rows: list[Any] = raw if isinstance(raw, list) else []
    locations = [
        {k: scrub(r.get(k)) for k in ("id", "request_id", "name", "status")} for r in rows if isinstance(r, dict)
    ]
    return {"status": "ok", "matter_id": mid, "locations": locations, "count": len(locations)}


def register(server: Any) -> None:
    """Register the three tools. Called once, from ``attachment_tools.register``."""
    server.tool()(prepare_records_order)
    server.tool()(place_records_order)
    server.tool()(records_orders_for_matter)


__all__ = ["register", "records_orders_for_matter", "place_records_order", "prepare_records_order"]
