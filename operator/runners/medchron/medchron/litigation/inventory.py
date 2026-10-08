"""`inventory`: which matters this job covers, who is responsible for each,
and every file on each. $0, read-only.

* The open matters come from the firm's matter list (``matter_statuses``,
  leads excluded), paged with Offset until a short page, the way the
  statute-watch pull reads them; a listing that does not parse refuses the job
  rather than passing a partial list for a whole one.
* Scope is the envelope's: the staff ids responsible, or every matter.
* A matter is a litigation matter when at least ``min_court_hits`` of its file
  names read as court papers (``court_paper_patterns``), or when an earlier run
  already tracked it (it stays on the list until a person closes it).
* Every file on every kept matter is listed in full (pages of 500; a matter
  with more is read past the cap) and written to ``data/files/<id>.json``:
  the change detection and every later stage read that list, never a sample.
* The responsible person is the Smokeball staff roster's name for the matter's
  ``personResponsibleStaffId``: code-read, never model-written.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .firm import LitigationFirm
from .progress import Progress

PAGE = 500
MAX_ROWS = 50_000
LIST_PAUSE_SECONDS = 0.3


class InventoryError(RuntimeError):
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
            raise InventoryError(f"{path}: the listing did not parse; no partial list is used")
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE
        if offset > MAX_ROWS:
            raise InventoryError(f"{path}: the listing did not end")
        if pause:
            time.sleep(pause)


def _getter(seat: Any) -> Callable[..., Any]:
    client = getattr(seat, "client", None)
    if client is None or not hasattr(client, "get"):
        raise InventoryError("this seat backend cannot list matters; the litigation job runs on the Machine")
    return client.get


def open_matters(seat: Any, statuses: list[str]) -> list[dict[str, Any]]:
    get = _getter(seat)
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


def staff_roster(seat: Any) -> dict[str, str]:
    """``{staff id: "First Last"}``. A roster that cannot be read raises: the
    workbook's responsible column is never left to a guess."""
    roster: dict[str, str] = {}
    for p in paged(_getter(seat), "/staff"):
        if isinstance(p, dict) and p.get("id"):
            name = " ".join(str(p.get(k) or "").strip() for k in ("firstName", "lastName")).strip()
            roster[str(p["id"]).lower()] = name
    return roster


def court_hits(files: list[dict[str, Any]], firm: LitigationFirm) -> int:
    rx = firm.court_rx
    return sum(1 for f in files if not f.get("deleted") and any(p.search(str(f.get("name") or "")) for p in rx))


def in_scope(matter: dict[str, Any], staff_ids: tuple[str, ...], everything: bool) -> bool:
    return everything or matter["staff_id"] in staff_ids


def build(
    seat: Any,
    firm: LitigationFirm,
    job: Any,
    tracked: set[str],
    data: Path,
    log: Callable[[str], None],
) -> dict[str, Any]:
    roster = staff_roster(seat)
    listed = [
        m
        for m in open_matters(seat, list(firm.get("matter_statuses")))
        if in_scope(m, job.attorney_staff_ids, job.scope_all)
    ]
    log(f"{len(listed)} open matters in scope")
    files_dir = data / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    kept: list[dict[str, Any]] = []
    floor = int(firm.get("min_court_hits"))
    prog = Progress(log, "inventory", len(listed), "matters listed", every=25)
    for m in listed:
        files = seat.list_files(m["id"])
        prog.counts["files"] = prog.counts.get("files", 0) + len(files)
        prog.step()
        hits = court_hits(files, firm)
        if hits < floor and m["id"] not in tracked:
            continue
        (files_dir / f"{m['id']}.json").write_text(json.dumps(files, indent=1), encoding="utf-8")
        kept.append({**m, "court_hits": hits, "files": len(files), "responsible": roster.get(m["staff_id"], "")})
        prog.counts["kept"] = len(kept)
    log(f"{len(kept)} litigation matters kept")
    return {"matters": kept, "staff": roster, "listed": len(listed)}


def files_of(data: Path, matter_id: str) -> list[dict[str, Any]]:
    return json.loads((data / "files" / f"{matter_id}.json").read_text(encoding="utf-8"))
