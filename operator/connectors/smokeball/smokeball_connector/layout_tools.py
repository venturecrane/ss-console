"""A matter's custom tabs ("layouts"): read any of them, and add demand and
offer rows to the Negotiation Details tab.

WHY THIS EXISTS. On 2026-10-09 a firm administrator asked the Operator about a
matter's Negotiation Details and was told the fields could not be read. They
could: the connector had no tool that read a matter's layouts, and Smokeball
returns only filled fields, so "nothing came back" looked like "cannot look".
``get_matter_layouts`` reads every tab and says, in its own result, that a
missing field was never entered. ``add_negotiation_rows`` fills rows the way
``add_medicals_row`` fills Medicals: read first, write only into empty fields,
re-read immediately before the write, then read back and report each row.

Classified READ and INTERNAL_WRITE: the firm's own record, nothing sent,
signed or settled."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from .layout_config import load_layout_config
from .layout_sections import (
    AMOUNT_FIELDS,
    DETAILS_KEY,
    MINIMUM_KEY,
    NEG_MARK,
    NEG_SECTION,
    ROWS,
    design_base,
    filled,
    mask,
    match_existing,
    negotiation_rows,
    negotiation_view,
    norm,
    parse_row,
    row_key,
    section_of,
)
from .medicals_layout import layout_values

#: Read-back waits after the PATCH, in seconds; module-level so tests shorten them.
VALUE_WAITS: tuple[float, ...] = (2, 3, 5, 8, 12)
SLEEP: Callable[[float], None] = time.sleep
MAX_BYTES = 30_000
NOT_ENTERED_NOTE = (
    "Smokeball returns only filled fields. A field missing here was never entered; it is not unreadable."
)


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "refused", "written": False, "reason": reason, **extra}


def _list_items(client: Any, matter: str) -> list[dict[str, Any]]:
    """The matter's layout items. Raises on a failed or unrecognised read: an
    answer of "no tabs" must come only from the vendor saying so."""
    resp = client.get(f"/matters/{matter}/layouts")
    items = resp.get("value") if isinstance(resp, dict) else resp
    if not isinstance(items, list):
        raise ValueError(f"unrecognised layout list shape ({type(resp).__name__})")
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)]


def _read_item(client: Any, matter: str, item: dict[str, Any]) -> dict[str, Any]:
    return layout_values(client.get(f"/matters/{matter}/layouts/{item['id']}"))


def _entry(item: dict[str, Any], values: dict[str, Any], design: str | None) -> dict[str, Any]:
    return {
        "item_id": item["id"],
        "section": section_of(sorted(values), design_base(item), design),
        "parent": item.get("parentId"),
        "parent_index": item.get("parentIndex"),
        "description": item.get("description"),
        "field_count": len(values),
    }


def _section_matches(label: str, wanted: str) -> bool:
    return label.lower().startswith(wanted.strip().lower())


def _capped(result: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(result, default=str)) <= MAX_BYTES:
        return result
    for entry in result.get("items", []):
        entry.pop("fields", None)
    result["truncated"] = "fields omitted: too large for one reply; ask for one section, or one item_id"
    return result


def get_matter_layouts(
    matter_id: str, section: str = "", item_id: str = "", include_sensitive: bool = False
) -> Any:
    """Read a matter's custom tabs (Smokeball "layouts": Negotiation Details,
    insurance, health insurer, case details, Medicals, witnesses).

    With no ``section``: an INDEX, one entry per tab with its ``section``
    label, ``parent`` (Plaintiff / Defendant), ``parent_index``,
    ``description`` and ``field_count``. Then call again with ``section`` (for
    example "Negotiation Details") or ``item_id`` to get that tab's ``fields``.
    A Negotiation Details tab also comes back parsed as ``negotiation``:
    ``rows`` (row number, demand_amount, demand_date, offer_amount,
    offer_date, note), ``details``, ``minimum_settlement`` and ``empty_rows``.

    Smokeball returns ONLY filled fields. A field that is not in the result
    was never entered; say "not entered", never "cannot be read". A tab with
    ``field_count`` 0 has nothing entered on it. Sensitive identifiers (SSN,
    tax id, date of birth, licence and card numbers) are masked unless
    ``include_sensitive`` is true; never repeat one in an email.

    ``status`` is ``ok`` or ``error``. An error means the tabs could not be
    read; say exactly that, never that the matter has none."""
    matter = (matter_id or "").strip()
    if not matter:
        return {"status": "error", "reason": "matter_id is required"}
    client = _client()
    try:
        items = _list_items(client, matter)
    except Exception as exc:  # noqa: BLE001 - the vendor raises a wide family; a failed read is reported, never "no tabs"
        return {"status": "error", "reason": f"the matter's tabs could not be read ({exc.__class__.__name__})"}
    config = load_layout_config()
    out: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in items:
        if item_id and item["id"] != item_id.strip():
            continue
        try:
            values = _read_item(client, matter, item)
        except Exception as exc:  # noqa: BLE001 - one unreadable tab is named, never dropped
            errors.append({"item_id": item["id"], "reason": f"could not be read ({exc.__class__.__name__})"})
            continue
        entry = _entry(item, values, config.negotiation_design)
        if (section or item_id) and (item_id or _section_matches(entry["section"], section)):
            entry["fields"] = mask(values, include_sensitive)
            if entry["section"].startswith(NEG_SECTION):
                entry["negotiation"] = negotiation_view(values)
        if section and not item_id and not _section_matches(entry["section"], section):
            continue
        out.append(entry)
    result: dict[str, Any] = {"status": "ok", "matter_id": matter, "items": out, "note": NOT_ENTERED_NOTE}
    if errors:
        result["unreadable_items"] = errors
    if config.error:
        result["config_error"] = config.error
    return _capped(result)


# ---- The write ------------------------------------------------------------


def _negotiation_item(client: Any, matter: str, plaintiff_index: int | None) -> dict[str, Any]:
    """The one Negotiation Details item to write, or a refusal."""
    try:
        items = _list_items(client, matter)
        values = {i["id"]: _read_item(client, matter, i) for i in items}
    except Exception as exc:  # noqa: BLE001 - a failed read refuses, never "no tab"
        return _refused(f"the matter's tabs could not be read ({exc.__class__.__name__})")
    config = load_layout_config()
    found = [
        i
        for i in items
        if any(NEG_MARK in k for k in values[i["id"]])
        or (config.negotiation_design and design_base(i) == config.negotiation_design)
    ]
    plaintiffs = {str(i.get("parentIndex")) for i in items if i.get("parentId") == "Plaintiff"}
    if plaintiff_index is None and len(plaintiffs) > 1:
        # A matter with several plaintiffs (a shared matter): even when only one
        # of them has a negotiation tab, the figures may be another's. Ask.
        return _refused(
            "this matter has more than one plaintiff; say which one the figures are for (plaintiff_index)",
            tabs=[{"plaintiff_index": i.get("parentIndex"), "description": i.get("description")} for i in found],
        )
    if plaintiff_index is not None:
        found = [i for i in found if str(i.get("parentIndex")) == str(plaintiff_index)]
    if not found:
        why = config.error or "this firm's Negotiation Details tab is not set up for the Operator yet"
        return _refused(f"no Negotiation Details tab found on this matter ({why}); nothing was written")
    if len(found) > 1:
        return _refused(
            "this matter has a Negotiation Details tab for more than one plaintiff; say which (plaintiff_index)",
            tabs=[{"plaintiff_index": i.get("parentIndex"), "description": i.get("description")} for i in found],
        )
    return {"item": found[0], "values": values[found[0]["id"]]}


def _plan(rows: list[dict[str, Any]], existing: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Decide, without writing, which row each new entry goes in and which
    keys it sets. Refuses rather than overwrite a filled field."""
    taken = {n: dict(r) for n, r in existing.items()}
    last = max(existing) if existing else -1
    results: list[dict[str, Any]] = []
    writes: dict[str, Any] = {}
    for i, row in enumerate(rows):
        # Against ``taken``, not ``existing``: the same entry twice in one call
        # is one row, not two.
        seen = match_existing(row, taken)
        if seen and "row" not in row:
            results.append({"entry": i, "status": seen[0], "row": seen[1]})
            continue
        if "row" in row:
            target = row["row"]
            current = taken.get(target, {})
            clash = [f for f in row if f != "row" and filled(current.get(f)) and norm(f, current[f]) != norm(f, row[f])]
            if clash:
                return _refused(f"row {target} already has a different {', '.join(clash)}; nothing was written")
        else:
            # The next row after every row filled so far, including rows this
            # call has already claimed (a named ``row`` earlier in the call).
            target = max(taken, default=-1) + 1
            if target >= ROWS:
                return _refused(
                    f"only {ROWS - last - 1} empty row(s) after the last filled one, {len(rows)} asked; nothing was written"
                )
        fields = {f: v for f, v in row.items() if f != "row" and not filled(taken.get(target, {}).get(f))}
        for field, value in fields.items():
            writes[row_key(target, field)] = _vendor_value(field, value)
        taken.setdefault(target, {}).update(fields)
        results.append({"entry": i, "status": "to_write" if fields else "already_present", "row": target, "fields": fields})
    gaps = [n for n in range(max(last, 0)) if n not in existing]
    return {"writes": writes, "results": results, "gaps": gaps}


def _vendor_value(field: str, value: Any) -> Any:
    """Amounts go to Smokeball as numbers (proven on a live tab 2026-10-09);
    dates and notes as text."""
    if field in AMOUNT_FIELDS or field == "minimum_settlement":
        amount = norm("demand_amount", value)
        return int(amount) if amount == amount.to_integral_value() else float(amount)
    return value


def _top_level(values: dict[str, Any], details: str, minimum: str) -> tuple[dict[str, Any], list[str]]:
    writes: dict[str, Any] = {}
    skipped: list[str] = []
    for key, value, name in ((DETAILS_KEY, details, "details"), (MINIMUM_KEY, minimum, "minimum_settlement")):
        if not value:
            continue
        if filled(values.get(key)):
            skipped.append(f"{name} already entered; not changed")
        else:
            writes[key] = _vendor_value(name, value)
    return writes, skipped


def _same(key: str, want: Any, got: Any) -> bool:
    field = next((f for f in ("amount", "date") if key.lower().endswith(f)), "")
    if key == MINIMUM_KEY:
        field = "amount"
    if field == "amount":
        return norm("demand_amount", want) == norm("demand_amount", got)
    if field == "date":
        return norm("demand_date", want) == norm("demand_date", got)
    return str(want).strip() == str(got if got is not None else "").strip()


def _row_prefix(key: str) -> str:
    return key.rsplit("/", 1)[0]


def _write_and_confirm(client: Any, path: str, writes: dict[str, Any]) -> dict[str, Any]:
    try:
        fresh = layout_values(client.get(path))
    except Exception as exc:  # noqa: BLE001 - no re-read, no write
        return _refused(f"the tab could not be re-read before writing ({exc.__class__.__name__}); nothing was written")
    raced = [k for k in writes if filled(fresh.get(k))]
    if raced:
        return _refused("someone entered values on this tab while it was being filled; nothing was written", fields=raced)
    try:
        client.request("PATCH", path, json={"values": [{"key": k, "value": v} for k, v in writes.items()]})
    except Exception as exc:  # noqa: BLE001 - a refused write is reported as one
        return _refused(f"Smokeball refused the write ({exc.__class__.__name__}: {str(exc)[:200]}); nothing confirmed written")
    after: dict[str, Any] = {}
    for wait in VALUE_WAITS:
        SLEEP(wait)
        try:
            after = layout_values(client.get(path))
        except Exception:  # noqa: BLE001 - a failed read-back is a mismatch below, never a crash after a write
            continue
        if all(_same(k, v, after.get(k)) for k, v in writes.items()):
            break
    mismatch = {k: {"wrote": v, "reads": after.get(k)} for k, v in writes.items() if not _same(k, v, after.get(k))}
    # Only the rows written into are checked for collateral change: a colleague
    # editing another field during the poll is not this write's doing.
    rows_written = {_row_prefix(k) for k in writes}
    disturbed = [
        k for k, v in fresh.items() if k not in writes and _row_prefix(k) in rows_written and not _same(k, v, after.get(k))
    ]
    return {"after": after, "mismatch": mismatch, "disturbed": disturbed}


def add_negotiation_rows(
    matter_id: str,
    rows: list[dict[str, Any]],
    plaintiff_index: int | None = None,
    details: str = "",
    minimum_settlement: str = "",
) -> Any:
    """Add demand and offer rows to a matter's Negotiation Details tab.
    Classified INTERNAL_WRITE: the firm's own record; nothing is sent.

    Each entry in ``rows`` carries any of ``demand_amount``, ``demand_date``,
    ``offer_amount``, ``offer_date`` and ``note``: amounts as written
    ("25000" or "25000.00"), dates YYYY-MM-DD. Pass only figures the sender
    wrote or a document states, never one you worked out. A new entry goes in
    the next row after the last filled one. To add the offer that answered a
    demand to that demand's row, pass ``row`` (0 to 9): only that row's EMPTY
    fields are filled. ``details`` and ``minimum_settlement`` are filled only
    if empty. NOTHING already entered is ever changed.

    ``plaintiff_index`` is needed only when the matter has a tab for more than
    one plaintiff; the refusal lists them, and the sender says which.

    Returns ``status``: ``written`` (every value read back as written; each
    entry's ``row``); ``readback_mismatch`` (``mismatch`` names what did not
    read back; nothing retried); ``nothing_to_write`` (every entry was
    ``already_present``); or ``refused`` (``reason`` says why; NOTHING was
    written). Each entry in ``entries`` is ``written``, ``already_present``
    (the same figures and dates are on that ``row``) or
    ``possible_duplicate`` (the same amount with a different date is on that
    ``row``; not written, ask the sender). ``gap_rows`` names empty rows left
    above the last filled one."""
    matter = (matter_id or "").strip()
    if not matter or not isinstance(rows, list) or not rows and not details and not minimum_settlement:
        return _refused("matter_id and at least one row (or details / minimum_settlement) are required")
    parsed: list[dict[str, Any]] = []
    for raw in rows or []:
        row, problem = parse_row(raw)
        if problem:
            return _refused(problem)
        parsed.append(row)  # type: ignore[arg-type]
    if minimum_settlement and parse_row({"demand_amount": minimum_settlement})[1]:
        return _refused('minimum_settlement must be the figure as written, e.g. "50000"')
    client = _client()
    found = _negotiation_item(client, matter, plaintiff_index)
    if found.get("status") == "refused":
        return found
    item, values = found["item"], found["values"]
    plan = _plan(parsed, negotiation_rows(values))
    if plan.get("status") == "refused":
        return plan
    top, skipped = _top_level(values, details.strip(), minimum_settlement.strip())
    writes = {**plan["writes"], **top}
    entries = [{**r, "status": "written" if r["status"] == "to_write" else r["status"]} for r in plan["results"]]
    base = {"matter_id": matter, "item_id": item["id"], "entries": entries, "gap_rows": plan["gaps"], "skipped": skipped}
    if not writes:
        return {"status": "nothing_to_write", "written": False, **base}
    done = _write_and_confirm(client, f"/matters/{matter}/layouts/{item['id']}", writes)
    if done.get("status") == "refused":
        return done
    status = "readback_mismatch" if done["mismatch"] or done["disturbed"] else "written"
    out = {"status": status, "written": True, **base, "negotiation": negotiation_view(done["after"])}
    if done["mismatch"]:
        out["mismatch"] = done["mismatch"]
    if done["disturbed"]:
        out["disturbed"] = done["disturbed"]
    return out


def register(server: Any) -> None:
    """Register the layout read and the Negotiation Details write. Called once,
    from ``attachment_tools.register``."""
    server.tool()(get_matter_layouts)
    server.tool()(add_negotiation_rows)


__all__ = ["MAX_BYTES", "NOT_ENTERED_NOTE", "SLEEP", "VALUE_WAITS", "add_negotiation_rows", "get_matter_layouts", "register"]
