"""`inventory`: which matters this job covers, and everything a review of each
reads that is not a document's text. $0, read-only.

* The caseload: every matter in the firm's ``caseload_statuses`` (leads
  excluded) on which one of the named people is the Person Assisting, or the
  matters named by number. A name is resolved against ``/staff`` and must name
  exactly one person; a number must name a matter in those statuses. Anything
  unresolved holds the job (``scope_unresolved``): whose files to read is the
  firm's answer, never a guess.
* Per matter (``data/m/<id>/inv.json``): the matter record, its clients and
  other side by name, every task (open AND completed), its calendar events,
  memos, every layout item with its values, the folder tree, and the full file
  listing (also ``data/files/<id>.json``, the shape the shared fetch reads).
* A read that fails is never an empty list (a matter once read as holding no
  documents because its listing raised): the matter is held alone
  (``inventory``), every other matter goes on.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from ..arrivals import ArrivalsError, getter, open_matters, paged
from .outcome import CasefileHold

PAUSE_SECONDS = 0.15


class InventoryError(RuntimeError):
    """The caseload itself could not be listed."""


def staff_rows(get: Callable[..., Any]) -> list[dict[str, Any]]:
    return [p for p in paged(get, "/staff") if isinstance(p, dict) and p.get("id")]


def full_name(p: dict[str, Any]) -> str:
    return " ".join(str(p.get(k) or "").strip() for k in ("firstName", "lastName")).strip()


def resolve_names(names: tuple[str, ...], rows: list[dict[str, Any]]) -> list[str]:
    """Each name to exactly one staff id, or a ``scope_unresolved`` hold."""
    out: list[str] = []
    for name in names:
        want = " ".join(name.split()).casefold()
        hits = {str(p["id"]).lower() for p in rows if full_name(p).casefold() == want}
        if len(hits) != 1:
            why = "matches no one" if not hits else "matches more than one person"
            raise CasefileHold(f"scope_unresolved: the name {name!r} {why} on the firm's staff list")
        out += sorted(hits)
    return out


def select(job: Any, firm: Any, seat: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(the matters in scope, {staff id: name})."""
    try:
        get = getter(seat)
        rows = staff_rows(get)
        listed = open_matters(seat, list(firm.get("caseload_statuses")))
    except ArrivalsError as exc:
        raise InventoryError(str(exc)) from exc
    roster = {str(p["id"]).lower(): full_name(p) for p in rows}
    scope = job.scope
    if scope.numbers:
        by_number = {m["number"]: m for m in listed}
        missing = [n for n in scope.numbers if n not in by_number]
        if missing:
            raise CasefileHold(
                f"scope_unresolved: {len(missing)} matter number(s) are not open matters in the caseload statuses"
            )
        chosen = [by_number[n] for n in scope.numbers]
    else:
        ids = set(scope.staff_ids) | set(resolve_names(scope.names, rows))
        chosen = [m for m in listed if ids & set(m.get("assisting_ids") or [])]
    for m in chosen:
        m["responsible"] = roster.get(m.get("staff_id") or "", "")
        m["assisting"] = ", ".join(roster.get(i, "") for i in m.get("assisting_ids") or [] if roster.get(i))
    chosen.sort(key=lambda m: m["number"])
    return chosen, roster


def _items(r: Any) -> list[dict[str, Any]]:
    if isinstance(r, dict):
        for k in ("value", "items", "data", "results"):
            if isinstance(r.get(k), list):
                return [x for x in r[k] if isinstance(x, dict)]
        return []
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def layout_values(item: Any) -> dict[str, Any]:
    """Layout values come back as a list of {key, value}; accept a dict too."""
    if isinstance(item, dict) and isinstance(item.get("value"), list) and item["value"]:
        first = item["value"][0]
        if isinstance(first, dict) and "values" in first:
            item = first
    vals = item.get("values") if isinstance(item, dict) else None
    if isinstance(vals, dict):
        return dict(vals)
    return {v["key"]: v.get("value") for v in vals or [] if isinstance(v, dict) and "key" in v}


def _contact_name(c: Any) -> str:
    c = c if isinstance(c, dict) else {}
    p = c.get("person") or {}
    if p.get("firstName") or p.get("lastName"):
        return " ".join(str(x) for x in (p.get("firstName"), p.get("lastName")) if x)
    return str((c.get("company") or {}).get("name") or c.get("name") or c.get("displayName") or "(no name entered)")


def _layouts(get: Callable[..., Any], mid: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in _items(get(f"/matters/{mid}/layouts")):
        key = str(it.get("id") or "")
        if not key:
            continue
        out[key] = {"row": it, "values": layout_values(get(f"/matters/{mid}/layouts/{key}"))}
        time.sleep(PAUSE_SECONDS)
    return out


def read_matter(seat: Any, m: dict[str, Any]) -> dict[str, Any]:
    """Everything one review reads besides text. Raises on any failed read."""
    get, mid = getter(seat), m["id"]
    detail = get(f"/matters/{mid}")
    detail = detail if isinstance(detail, dict) else {}
    clients = [{"id": c, "name": _contact_name(get(f"/contacts/{c}"))} for c in detail.get("clientIds") or []]
    others = [{"id": c, "name": _contact_name(get(f"/contacts/{c}"))} for c in detail.get("otherSideIds") or []]
    return {
        "matter": m,
        "detail": {k: detail.get(k) for k in ("title", "status", "description", "openedDate", "matterType")},
        "clients": clients,
        "other_sides": others,
        "tasks_open": paged(get, "/tasks", MatterId=mid, IsCompleted=False),
        "tasks_done": paged(get, "/tasks", MatterId=mid, IsCompleted=True),
        "events": paged(get, "/events", MatterId=mid, ExcludeDeletedEvents=True),
        "memos": paged(get, f"/matters/{mid}/memos"),
        "layouts": _layouts(get, mid),
        "folders": seat.folder_tree(mid),
        "files": seat.list_files(mid),
    }


def build(seat: Any, job: Any, firm: Any, data: Path, log: Callable[[str], None]) -> dict[str, Any]:
    chosen, roster = select(job, firm, seat)
    log(f"{len(chosen)} matters in scope")
    (data / "files").mkdir(parents=True, exist_ok=True)
    held: dict[str, str] = {}
    for i, m in enumerate(chosen, 1):
        mdir = data / "m" / m["id"]
        if (mdir / "inv.json").is_file():
            continue  # a resume keeps what it already read
        try:
            inv = read_matter(seat, m)
        except Exception as exc:  # noqa: BLE001 - one matter's failed read holds that matter, never the job
            held[m["id"]] = f"inventory: {type(exc).__name__}: {str(exc)[:160]}"
            continue
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "inv.json").write_text(json.dumps(inv, indent=1, default=str), encoding="utf-8")
        (data / "files" / f"{m['id']}.json").write_text(json.dumps(inv["files"], indent=1), encoding="utf-8")
        if i % 10 == 0:
            log(f"[inventory] {i} of {len(chosen)} matters read")
    return {"matters": chosen, "staff": roster, "held": held}


def inv_of(data: Path, matter_id: str) -> dict[str, Any]:
    return json.loads((data / "m" / matter_id / "inv.json").read_text(encoding="utf-8"))


__all__ = ["InventoryError", "build", "inv_of", "layout_values", "read_matter", "resolve_names", "select"]
