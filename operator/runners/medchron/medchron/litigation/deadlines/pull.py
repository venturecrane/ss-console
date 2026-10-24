"""`pull`: every Open and Pending matter in the job's scope, read-only.

The hand kit's ``seat_pull.py`` call for call (so the counts match it on the
same data): the matter list (leads excluded), then per matter the statute,
Case number and Filed date from the layouts, every calendar entry by
``GET /events?MatterId=`` (the only call that proves a matter link; the
unfiltered list shows ``matterId`` null for all), the open tasks, and every
document file name.

Two differences, both about failure, never about content:

* each matter is written to ``data/pull/m/<id>.json`` as it is read, so a
  resume reads only what is missing;
* a matter any of whose four reads raised is NOT written, and the stage fails
  ``read_incomplete`` naming the count once every other matter is read. The
  kit recorded the error on the row and built anyway; a job must not file a
  workbook missing a matter's dates because one call failed (a failed read
  never looks like an empty result).
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any, Callable

from ...arrivals import assisting_ids, getter, originating_listed, paged, staff_roles
from .. import people
from ..outcome import LitigationFailed
from ..progress import Progress
from ..tools import dump

#: The statuses the report covers: the work still in the office.
STATUSES = ("Open", "Pending")
SOL = "Matter/CaseDetails/StandardCaseDetails/StatuteOfLimitationDate"
CASE_NUMBER = "Matter/CaseDetails/StandardCaseDetails/CaseNumber"
FILED = "Matter/CaseDetails/PleadingsDetails/FiledDate"
WANT = (SOL, CASE_NUMBER, FILED)
PULL_NAME = "pull.json.gz"


def _listing(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "value", "results", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def view(row: dict[str, Any]) -> dict[str, Any]:
    """A raw matter row as ``people.in_scope`` reads it (the status list's shape)."""
    return {
        "staff_id": str(row.get("personResponsibleStaffId") or "").lower(),
        "assisting_ids": assisting_ids(row),
        "roles": staff_roles(row),
    }


def in_scope(row: dict[str, Any], scope: Any, people_ids: frozenset[str] = frozenset()) -> bool:
    """The litigation lane's ONE scope rule (``people.in_scope``) on a raw matter row."""
    return people.in_scope(view(row), scope, people_ids)


def staff_rows(get: Callable[..., Any]) -> list[dict[str, Any]]:
    return [s for s in paged(get, "/staff") if isinstance(s, dict) and s.get("id")]


def staff_names(get: Callable[..., Any], rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    return {
        s["id"]: ((s.get("firstName") or "").strip() + " " + (s.get("lastName") or "").strip()).strip()
        for s in (rows if rows is not None else staff_rows(get))
    }


def list_in_scope(
    get: Callable[..., Any], scope: Any, people_ids: frozenset[str] = frozenset()
) -> tuple[int, list[dict[str, Any]]]:
    """(rows listed, the in-scope matter stubs), Open first then Pending. A
    people scope over a list with no originating key on any row holds
    (``people.check_roles_listed``)."""
    listed, keep, originating = 0, [], []
    for status in STATUSES:
        for r in paged(get, "/matters", Status=status, IsLead=False):
            listed += 1
            if not isinstance(r, dict) or r.get("isLead") is True or not r.get("id"):
                continue
            originating.append(originating_listed(r))
            if not in_scope(r, scope, people_ids):
                continue
            keep.append(
                {
                    "id": r["id"],
                    "number": r.get("number"),
                    "title": r.get("title"),
                    "status": status,
                    "resp": r.get("personResponsibleStaffId"),
                    "assist": r.get("personAssistingStaffId"),
                    "matterType": r.get("matterTypeId"),
                }
            )
    people.check_roles_listed(scope, originating, len(originating))
    return listed, keep


def _layout_values(get: Callable[..., Any], mid: str) -> dict[str, Any]:
    vals: dict[str, Any] = {}
    for d in _listing(get("/matters/" + mid + "/layouts")):
        for it in (d.get("values") or []) if isinstance(d, dict) else []:
            k, v = it.get("key"), it.get("value")
            if k in WANT and v not in (None, "") and k not in vals:
                vals[k] = v
    return vals


def read_matter(get: Callable[..., Any], stub: dict[str, Any]) -> dict[str, Any]:
    """One matter's record, the kit's shape. Raises on any failed read."""
    rec = dict(stub)
    mid = stub["id"]
    vals = _layout_values(get, mid)
    rec["statute"], rec["caseNumber"], rec["filed"] = vals.get(SOL), vals.get(CASE_NUMBER), vals.get(FILED)
    rec["events"] = paged(get, "/events", MatterId=mid)
    rec["tasks"] = [
        t
        for t in paged(get, "/tasks", MatterId=mid, IsCompleted=False)
        if isinstance(t, dict) and not t.get("isCompleted") and not t.get("isDeleted")
    ]
    files = paged(get, "/matters/" + mid + "/documents/files")
    rec["fileNames"] = [str(f.get("name") or f.get("fileName") or "") for f in files if isinstance(f, dict)]
    return rec


def run(seat: Any, scope: Any, data: Path, log: Callable[[str], None], roster: dict[str, str] | None = None) -> Path:
    """Reads what is not yet on disk and writes ``data/pull.json.gz`` (the
    kit's output shape: staff, listed, matters, errors). Returns its path.
    ``roster`` (the firm's name -> staff id maps) places a people scope's
    names the staff list's full names do not (``people.by_name``)."""
    get = getter(seat)
    root = data / "pull"
    mdir = root / "m"
    mdir.mkdir(parents=True, exist_ok=True)
    head = root / "listing.json"
    if not head.is_file():
        rows = staff_rows(get)
        spec = getattr(scope, "people", None)
        who = people.resolve(spec, rows, roster or {}) if spec is not None else ()
        listed, stubs = list_in_scope(get, scope, people.ids_of(who))
        dump(
            head,
            {
                "started": time.time(),
                "staff": staff_names(get, rows),
                "listed": listed,
                "stubs": stubs,
                "people": [p.as_dict() for p in who],
            },
        )
    listing = json.loads(head.read_text(encoding="utf-8"))
    stubs = listing["stubs"]
    prog = Progress(log, "pull", len(stubs), "matters read", every=25)
    failed = 0
    for stub in stubs:
        path = mdir / f"{stub['id']}.json"
        if not path.is_file():
            try:
                dump(path, read_matter(get, stub))
            except Exception as exc:  # noqa: BLE001 - counted; the stage fails after the others are read
                failed += 1
                prog.add("not read")
                log(f"[pull] a matter read raised {type(exc).__name__}")
        prog.step()
    if failed:
        raise LitigationFailed(f"read_incomplete: {failed} of {len(stubs)} matters could not be read; resumable")
    out = {
        "started": listing["started"],
        "staff": listing["staff"],
        "listed": listing["listed"],
        "matters": [json.loads((mdir / f"{s['id']}.json").read_text(encoding="utf-8")) for s in stubs],
        "errors": [],
        "finished": time.time(),
    }
    dest = data / PULL_NAME
    with gzip.open(dest, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
    return dest
