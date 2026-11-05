"""Two money READS that answer in code what the model used to work out:
a matter's expense totals, and whether a trust balance is under the firm's
floor.

WHY (the model-as-logic audit, 2026-11-04). ``get_expenses`` returns one page
and its docstring told the model to page and total the ledger itself, so a
figure in a reply was the model's arithmetic over rows it may not have read to
the end. ``get_matter_balances`` returned the vendor's balances and the
``trust-balance-nudge`` skill compared ``availableBalance`` to a floor the
model was to find in config; no seat authors one, so the comparison had no
authored side at all. Both answers are now computed here:

* ``get_expense_totals`` reads the WHOLE ledger (``expense_ledger``), totals it
  in ``Decimal``, and says whether the listing was complete. A ledger that
  could not be read to its end gives no total, never a partial one.
* ``get_matter_balances`` adds ``low_trust`` (``availableBalance`` below the
  floor) and ``floor_usd`` from the seat's live customer.yaml:
  ``skills[trust-balance-nudge].settings.trust_floor_usd`` on a persona.
  Unauthored, ``low_trust`` is null with ``reason: "no floor authored"`` and
  the nudge does nothing (ADR 0037: unconfigured is fail-closed, never a
  default floor).

Registered from ``attachment_tools`` because ``server.py`` is held by the
module-size ratchet; the balance read moved here whole, under its own name.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from .expense_ledger import ExpenseListingIncomplete, money, read_whole_ledger_counted, row_amount
from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML

NUDGE_SKILL = "trust-balance-nudge"
FLOOR_KEY = "trust_floor_usd"
NO_FLOOR = "no floor authored"


def _client() -> Any:
    from . import server

    return server._get_client()


def _attach_refs(client: Any, resp: Any, matter_id: str | None) -> None:
    from . import server

    server._attach_matter_refs_to_list(client, resp, matter_id=matter_id)


def _dollars(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


# ---- the trust floor ------------------------------------------------------------


def trust_floor(path: str | None = None) -> tuple[Decimal | None, str]:
    """(the authored floor, why there is none). Every enabled
    ``trust-balance-nudge`` on any persona that authors the floor must author
    the SAME one; two different floors is no floor, said so."""
    try:
        import yaml

        with open(path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 - an unreadable config authors nothing, and says why
        return None, f"the seat's settings could not be read ({exc.__class__.__name__})"
    floors: set[Decimal] = set()
    for persona in doc.get("personas") or [] if isinstance(doc, dict) else []:
        for skill in persona.get("skills") or [] if isinstance(persona, dict) else []:
            if not isinstance(skill, dict) or skill.get("name") != NUDGE_SKILL or skill.get("enabled") is not True:
                continue
            settings = skill.get("settings") if isinstance(skill.get("settings"), dict) else {}
            raw = settings.get(FLOOR_KEY)
            if raw is None:
                continue
            floor = money(raw) if not isinstance(raw, str) else None
            if floor is None or floor <= 0:
                return None, f"{FLOOR_KEY} is not a positive number"
            floors.add(floor)
    if len(floors) > 1:
        return None, "personas author different trust floors"
    return (floors.pop(), "") if floors else (None, NO_FLOOR)


def _row_low(row: dict[str, Any], floor: Decimal) -> bool | None:
    available = money(row.get("availableBalance"))
    return None if available is None else available < floor


def get_matter_balances(
    bank_account_id: str,
    matter_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> Any:
    """Per-matter trust balances (balance / protectedBalance / availableBalance),
    READ ONLY: fund movement is hard-banned.

    The low-trust decision is made HERE, not by you: each row carries
    ``low_trust`` (its ``availableBalance`` below the firm's authored floor),
    and the response carries ``floor_usd`` and, for a read of one matter,
    ``low_trust`` and ``shortfall_usd``. When the seat authors no floor,
    ``low_trust`` is null with ``reason: "no floor authored"``: there is
    nothing to nudge about, and nothing is drafted. A balance that does not
    read as a number is ``low_trust: null`` too, never assumed."""
    resp = _client().get(
        f"/bankaccounts/{bank_account_id}/matter-balances",
        MatterId=matter_id,
        Limit=limit,
        Offset=offset,
    )
    floor, why = trust_floor()
    rows = resp.get("value") if isinstance(resp, dict) else resp
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    out: dict[str, Any] = resp if isinstance(resp, dict) else {"value": rows}
    out["floor_usd"] = _dollars(floor) if floor is not None else None
    for row in rows:
        row["low_trust"] = _row_low(row, floor) if floor is not None else None
    out["low_trust"], out["shortfall_usd"] = None, None
    if floor is None:
        out["reason"] = why
    elif len(rows) == 1 and rows[0]["low_trust"] is not None:
        out["low_trust"] = rows[0]["low_trust"]
        if out["low_trust"]:
            out["shortfall_usd"] = _dollars(floor - (money(rows[0].get("availableBalance")) or Decimal(0)))
    elif len(rows) == 1:
        out["reason"] = "availableBalance did not read as a number"
    elif rows:
        out["reason"] = "several matters were read; each row carries its own low_trust"
    return out


# ---- the expense totals ------------------------------------------------------------


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total, finalized, unfinalized = Decimal(0), Decimal(0), Decimal(0)
    by_type: dict[str, Decimal] = {}
    for row in rows:
        amount = row_amount(row)
        assert amount is not None  # the caller refused unreadable amounts
        total += amount
        if row.get("finalized") is True:
            finalized += amount
        else:
            unfinalized += amount
        cost_type = row.get("costType")
        if isinstance(cost_type, str) and cost_type:
            by_type[cost_type] = by_type.get(cost_type, Decimal(0)) + amount
    out: dict[str, Any] = {
        "total_amount": _dollars(total),
        "finalized_amount": _dollars(finalized),
        "unfinalized_amount": _dollars(unfinalized),
    }
    if by_type:
        out["by_cost_type"] = [{"cost_type": k, "amount": _dollars(v)} for k, v in sorted(by_type.items())]
    return out


def get_expense_totals(matter_id: str) -> Any:
    """A matter's expense totals, computed in code over EVERY page of the
    ledger (AR, not trust). Classified READ.

    Returns ``listingComplete`` true with ``entries``, ``total_amount``,
    ``finalized_amount``, ``unfinalized_amount`` (dollars, two decimals) and,
    where the ledger records a cost type, ``by_cost_type``; soft-deleted entries are left out and counted
    in ``deletedExcluded``. ``listingComplete`` false (the ledger could not be
    read to its end) carries no totals, and an entry whose amount does not read
    as a number gives no totals either (``unreadableAmounts``): a figure in a
    reply is one this read computed, never a sum you made. The envelope carries
    ``matterNumber`` from the matter the read was scoped to."""
    client = _client()
    out: dict[str, Any] = {"matter_id": matter_id, "value": []}
    try:
        rows, deleted = read_whole_ledger_counted(client, matter_id)
    except ExpenseListingIncomplete as exc:
        out.update(listingComplete=False, reason=str(exc))
        _attach_refs(client, out, matter_id)
        return out
    unreadable = sum(1 for r in rows if row_amount(r) is None)
    out.update(listingComplete=True, entries=len(rows), deletedExcluded=deleted)
    if unreadable:
        out.update(unreadableAmounts=unreadable, reason="an entry's amount did not read as a number; no total given")
    else:
        out.update(_totals(rows))
    _attach_refs(client, out, matter_id)
    return out


def register(server: Any) -> None:
    """Register the two reads. Called once, from ``attachment_tools.register``."""
    server.tool()(get_matter_balances)
    server.tool()(get_expense_totals)


__all__ = [
    "FLOOR_KEY",
    "NO_FLOOR",
    "NUDGE_SKILL",
    "get_expense_totals",
    "get_matter_balances",
    "register",
    "trust_floor",
]
