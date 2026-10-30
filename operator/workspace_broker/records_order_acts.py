"""The records orders the Operator placed, read back for the weekly chase (letter 416, #10).

WHY A VERB. The weekly records chase (``operator/skills/records-order-chase``)
lists only orders placed THROUGH the Operator, never an order the firm placed
on the vendor's own portal. The one record of "placed through the Operator" is
the confirmed act: ``establishment_acts.act_commit`` appends an
``ACT_COMMITTED`` row whose ``tool`` is the place-order tool
(``act_records_order.PLACE_ORDER_TOOL``). That row lives in the audit file this
broker owns, which the cron pre_run (agent uid) cannot open. This verb is the
narrow read that closes the gap.

WHAT IT RETURNS, AND WHAT IT NEVER DOES. One entry per committed records-order
act: when it was committed (the row's own ``ts``), the Smokeball matter id, the
vendor's order id, the order's digest and its location count. Never the order's
facilities, the client's name or any identifier: the chase reads those fresh,
the client from Smokeball and each location from the vendor.

WHERE THE MATTER ID COMES FROM. Since 2026-10-30 the act row carries it
(``act_records_order.committed_metadata``). A row committed before that holds
``matter_id: null`` (the generic field is rebuilt from the tool's outcome, and
the place-order outcome carries only ``ref``), so the id is read from the act's
stored proposal (``pending_rules.payload_json``) while that row is still kept
(``TERMINAL_RETENTION_SECONDS``, seven days after commit). An order whose matter
id is in neither place is still returned, with ``matter_id: null``: the chase
then lists it as an order it could not check, never drops it.

READ-ONLY, a fresh ``mode=ro`` connection per call, fixed statements. A ledger
that cannot be opened RAISES, so the caller reports "could not look" rather than
"nothing ordered".
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .act_records_order import PLACE_ORDER_TOOL
from .broker_context import BrokerContext
from .establishment_constants import ACT_COMMITTED_ACTION_TYPE

VERB = "records_orders_placed"
#: A seat places a handful of orders a week; past this the read is a bug.
MAX_ORDERS = 2000

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_REF = re.compile(r"^[0-9a-f]{32}$")

_ACTS_SQL = "SELECT ts, metadata FROM audit_log WHERE action_type = ? ORDER BY ts ASC, rowid ASC"
_PROPOSAL_SQL = "SELECT payload_json FROM pending_rules WHERE proposal_id = ?"


def _decoded(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _uuid(value: Any) -> str | None:
    return value if isinstance(value, str) and _UUID.match(value) else None


def _proposal_matter_id(conn: sqlite3.Connection, proposal_id: Any) -> str | None:
    """The stored order's matter id, while its proposal row is still kept."""
    if not isinstance(proposal_id, str) or not proposal_id:
        return None
    try:
        row = conn.execute(_PROPOSAL_SQL, (proposal_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    order = _decoded(row[0] if row else None).get("order")
    return _uuid(order.get("matter_id")) if isinstance(order, dict) else None


def _entry(conn: sqlite3.Connection, ts: Any, meta: dict[str, Any]) -> dict[str, Any]:
    matter_id = _uuid(meta.get("matter_id")) or _proposal_matter_id(conn, meta.get("proposal_id"))
    count = meta.get("location_count")
    ref = meta.get("order_ref")
    return {
        "committed_at": ts if isinstance(ts, str) else None,
        "matter_id": matter_id,
        "vendor_order_record_id": _uuid(meta.get("vendor_order_record_id")),
        "order_ref": ref if isinstance(ref, str) and _REF.match(ref) else None,
        "location_count": count if isinstance(count, int) and not isinstance(count, bool) else None,
    }


def read_orders(audit_db_path: str | None) -> list[dict[str, Any]]:
    """Every committed records-order act, oldest first. Raises when the ledger cannot be read."""
    if not audit_db_path:
        raise ValueError(f"{VERB}: no audit ledger is configured on this seat")
    conn = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute(_ACTS_SQL, (ACT_COMMITTED_ACTION_TYPE,)).fetchall()
        out: list[dict[str, Any]] = []
        for ts, raw in rows:
            meta = _decoded(raw)
            if meta.get("tool") != PLACE_ORDER_TOOL:
                continue
            out.append(_entry(conn, ts, meta))
            if len(out) >= MAX_ORDERS:
                break
        return out
    finally:
        conn.close()


def records_orders_placed(
    broker: BrokerContext, _action: str, _request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return {"ok": True, "orders": read_orders(broker.audit_db_path)}


__all__ = ["MAX_ORDERS", "VERB", "read_orders", "records_orders_placed"]
