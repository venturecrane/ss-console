"""Each provider the gap audit says the file lacks, resolved against the firm's
records vendor's custodian directory, so the requester's "yes, order it" has a
location to act on. Free (a directory read), and run AFTER the paid stages:
each lookup took about 23 seconds live (2026-10-06), so they run one at a time,
capped, and never hold up spending that has not happened yet.

Each provider gets exactly one of: the matched location (its id, name and
address as the directory states them), "no vendor match", "several matches"
(with the count; the requester chooses), "not looked up" (over the cap), or
"lookup failed" (with the vendor's status). Nothing is chosen for her: one
match is reported as the match, several are reported as several.

The vendor client is the connector's own (``records_vendor.client_from_env``),
with its read limit; a seat whose vendor is not connected gets one line saying
so, never an empty table that reads as "nothing matched".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def missing_providers(gap_md: str) -> list[str]:
    """The provider column of the gap audit's item tables (each table whose
    header names Provider, What's missing and Priority; gapaudit.render leads
    them with an Item column), in order, once each. The possible list carries
    no Priority, so a provider only inferable is never looked up."""
    out: list[str] = []
    col: int | None = None
    for line in gap_md.splitlines():
        m = _ROW.match(line)
        if not m:
            col = None
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        low = [c.lower() for c in cells]
        if "provider" in low and any("missing" in c for c in low) and "priority" in low:
            col = low.index("provider")
            continue
        if col is None or col >= len(cells) or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        name = re.sub(r"[*_`]", "", cells[col]).strip()
        if name and name.lower() not in {p.lower() for p in out}:
            out.append(name)
    return out


def _resolve(vendor: Any, name: str) -> dict[str, Any]:
    from smokeball_connector.records_orders import _candidate

    try:
        rows = vendor.get_locations(name)
    except Exception as exc:  # noqa: BLE001 - one provider's failed lookup is that provider's row, named
        status = getattr(exc, "status", None)
        return {"provider": name, "status": "lookup failed", "detail": f"{type(exc).__name__} {status or ''}".strip()}
    found = [c for c in (_candidate(r) for r in rows) if c is not None]
    if not found:
        return {"provider": name, "status": "no vendor match"}
    if len(found) > 1:
        return {"provider": name, "status": "several matches", "count": len(found), "candidates": found[:5]}
    return {"provider": name, "status": "matched", **found[0]}


def run(
    data: Path, gap_md: str, vendor_factory: Callable[[], Any], cap: int, log: Callable[[str], None]
) -> dict[str, Any]:
    """Resolve each missing provider; resumable from ``vendor.json``."""
    path = data / "vendor.json"
    done: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"rows": []}
    names = missing_providers(gap_md)
    try:
        vendor = vendor_factory()
    except Exception as exc:  # noqa: BLE001 - not connected is a stated outcome, not a crash
        vendor, done["not_connected"] = None, f"{type(exc).__name__}"
    if vendor is None:
        done["not_connected"] = done.get("not_connected") or "no records vendor on this seat"
        path.write_text(json.dumps(done, indent=1), encoding="utf-8")
        return done
    have = {r["provider"] for r in done["rows"]}
    for i, name in enumerate(names):
        if name in have:
            continue
        row = _resolve(vendor, name) if i < cap else {"provider": name, "status": "not looked up (over the cap)"}
        done["rows"].append(row)
        path.write_text(json.dumps(done, indent=1), encoding="utf-8")
        log(f"  vendor lookup {i + 1}/{len(names)}: {row['status']}")
    return done


def section(result: dict[str, Any]) -> str:
    """The table appended to the gap audit."""
    head = "\n\n## Records vendor locations for the missing providers\n\n"
    if result.get("not_connected"):
        return head + "The records vendor is not connected on this seat, so no locations were looked up.\n"
    if not result["rows"]:
        return head + "The item table names no provider to look up.\n"
    lines = ["| Provider | Vendor location | Location id |", "|---|---|---|"]
    for r in result["rows"]:
        if r["status"] == "matched":
            lines.append(f"| {r['provider']} | {r.get('name') or ''}, {r.get('address') or ''} | {r['custodian_id']} |")
        elif r["status"] == "several matches":
            opts = "; ".join(f"{c['name']}, {c['address']} ({c['custodian_id']})" for c in r.get("candidates") or [])
            lines.append(f"| {r['provider']} | several matches ({r.get('count')}): {opts} | choose one |")
        else:
            lines.append(f"| {r['provider']} | {r['status']}{(': ' + r['detail']) if r.get('detail') else ''} | none |")
    return head + "\n".join(lines) + "\n"
