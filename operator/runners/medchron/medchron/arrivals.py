"""`arrivals`: which documents were saved to a matter since a lane last looked.

Lane-neutral new-document detection, shared by every runner lane that keeps a
standing view of open matters current (the litigation status list, the offer
watcher). $0, read-only against Smokeball.

* ``open_matters`` lists the firm's open matters (leads excluded), paged with
  Offset until a short page. A listing that does not parse raises rather than
  passing a partial list for a whole one.
* ``list_files`` is the seat's full file listing for one matter.
* The cursor is a per-matter manifest ``{file_id: dateModified}``, stored under
  ``$MEDCHRON_DATA_DIR/arrivals/<lane>/manifest/<matter_id>.json``. It is
  namespaced by lane: one lane's commit never moves another's.
* ``arrivals`` returns, per matter, the file rows that are new or whose
  ``dateModified`` moved since that lane's cursor, filtered by the lane's own
  predicate. A matter with no cursor returns every file passing the predicate:
  a lane seeds its cursor (``commit`` with the current listing) before its
  first scheduled run, or run one is the whole firm.
* ``commit`` advances the cursor. Call it only after the lane's delivery is
  read back, so a failed run sees the same arrivals again.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

PAGE = 500
MAX_ROWS = 50_000
LIST_PAUSE_SECONDS = 0.3
DATA_ENV = "MEDCHRON_DATA_DIR"
DEFAULT_DATA_DIR = "/opt/data/medchron"
_LANE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_MATTER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class ArrivalsError(RuntimeError):
    pass


def _listing(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("value", "items", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def paged(get: Callable[..., Any], path: str, pause: float = LIST_PAUSE_SECONDS, **params: Any) -> list[Any]:
    rows: list[Any] = []
    offset = 0
    while True:
        page = _listing(get(path, Limit=PAGE, Offset=offset, **params))
        if page is None:
            raise ArrivalsError(f"{path}: the listing did not parse; no partial list is used")
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE
        if offset > MAX_ROWS:
            raise ArrivalsError(f"{path}: the listing did not end")
        if pause:
            time.sleep(pause)


def getter(seat: Any) -> Callable[..., Any]:
    client = getattr(seat, "client", None)
    if client is None or not hasattr(client, "get"):
        raise ArrivalsError("this seat backend cannot list matters; the job runs on the Machine")
    return client.get


def open_matters(seat: Any, statuses: Iterable[str]) -> list[dict[str, Any]]:
    get = getter(seat)
    out: dict[str, dict[str, Any]] = {}
    for status in statuses:
        for row in paged(get, "/matters", Status=status, IsLead=False):
            if not isinstance(row, dict) or not row.get("id") or row.get("isLead") is True:
                continue
            mid = str(row["id"])
            out[mid] = {
                "id": mid,
                "number": str(row.get("number") or ""),
                "title": str(row.get("title") or ""),
                "staff_id": str(row.get("personResponsibleStaffId") or "").lower(),
                "status": status,
            }
    return list(out.values())


def list_files(seat: Any, matter_id: str) -> list[dict[str, Any]]:
    return seat.list_files(matter_id)


def stamp(f: dict[str, Any]) -> str:
    return str(f.get("modified") or f.get("created") or "")


def manifest_of(files: list[dict[str, Any]]) -> dict[str, str]:
    return {str(f["id"]): stamp(f) for f in files if f.get("id") and not f.get("deleted")}


def lane_dir(lane: str, data: str | Path | None = None) -> Path:
    if not _LANE.match(lane):
        raise ArrivalsError(f"lane name {lane!r} is not a plain lowercase identifier")
    root = Path(data) if data else Path(os.environ.get(DATA_ENV) or DEFAULT_DATA_DIR)
    return root / "arrivals" / lane


def _path(lane: str, matter_id: str, data: str | Path | None) -> Path:
    if not _MATTER.match(matter_id):
        raise ArrivalsError(f"matter id {matter_id!r} is not a plain identifier")
    return lane_dir(lane, data) / "manifest" / f"{matter_id}.json"


def cursor(lane: str, matter_id: str, data: str | Path | None = None) -> dict[str, str] | None:
    """The lane's last committed manifest for the matter, or None if it has
    never committed one. A cursor that does not parse raises: reading it as
    empty would replay the whole matter as new."""
    path = _path(lane, matter_id, data)
    if not path.exists():
        return None
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArrivalsError(f"{path}: the cursor did not parse") from exc
    if not isinstance(v, dict):
        raise ArrivalsError(f"{path}: the cursor is not a manifest")
    return {str(k): str(x or "") for k, x in v.items()}


def new_since(files: list[dict[str, Any]], prior: dict[str, str] | None) -> list[dict[str, Any]]:
    """Rows new or modified since ``prior``; every live row when ``prior`` is None."""
    live = [f for f in files if f.get("id") and not f.get("deleted")]
    if prior is None:
        return live
    return [f for f in live if prior.get(str(f["id"])) != stamp(f)]


def arrivals(
    seat: Any,
    lane: str,
    matters: Iterable[dict[str, Any] | str],
    select: Callable[[dict[str, Any]], bool] | None = None,
    data: str | Path | None = None,
    listings: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """``{matter_id: [new or modified rows passing select]}``, matters with
    none omitted. ``listings`` (matter id -> file rows) reuses a listing the
    caller already holds instead of listing again."""
    out: dict[str, list[dict[str, Any]]] = {}
    for m in matters:
        mid = str(m["id"] if isinstance(m, dict) else m)
        files = listings[mid] if listings is not None and mid in listings else list_files(seat, mid)
        rows = [f for f in new_since(files, cursor(lane, mid, data)) if select is None or select(f)]
        if rows:
            out[mid] = rows
    return out


def commit(lane: str, matter_id: str, files: list[dict[str, Any]], data: str | Path | None = None) -> None:
    """Advance the lane's cursor to ``files`` (the full listing the delivery
    was built from). Atomic: a crash leaves the old cursor whole."""
    path = _path(lane, matter_id, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(manifest_of(files), indent=1), encoding="utf-8")
    tmp.replace(path)
