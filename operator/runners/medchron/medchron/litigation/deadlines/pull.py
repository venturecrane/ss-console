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

from ...arrivals import assisting_ids, getter, paged
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


def in_scope(row: dict[str, Any], scope: Any) -> bool:
    """The litigation lane's scope rule on a raw matter row: every matter, or
    one matching EACH side the job names."""
    if scope.scope_all:
        return True
    if not scope.attorney_staff_ids and not scope.paralegal_staff_ids:
        return False
    resp = str(row.get("personResponsibleStaffId") or "").lower()
    if scope.attorney_staff_ids and resp not in scope.attorney_staff_ids:
        return False
    return not scope.paralegal_staff_ids or bool(set(assisting_ids(row)) & set(scope.paralegal_staff_ids))


def staff_names(get: Callable[..., Any]) -> dict[str, str]:
    return {
        s["id"]: ((s.get("firstName") or "").strip() + " " + (s.get("lastName") or "").strip()).strip()
        for s in paged(get, "/staff")
        if isinstance(s, dict) and s.get("id")
    }


def list_in_scope(get: Callable[..., Any], scope: Any) -> tuple[int, list[dict[str, Any]]]:
    """(rows listed, the in-scope matter stubs), Open first then Pending."""
    listed, keep = 0, []
    for status in STATUSES:
        for r in paged(get, "/matters", Status=status, IsLead=False):
            listed += 1
            if not isinstance(r, dict) or r.get("isLead") is True or not r.get("id") or not in_scope(r, scope):
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


def run(seat: Any, scope: Any, data: Path, log: Callable[[str], None]) -> Path:
    """Reads what is not yet on disk and writes ``data/pull.json.gz`` (the
    kit's output shape: staff, listed, matters, errors). Returns its path."""
    get = getter(seat)
    root = data / "pull"
    mdir = root / "m"
    mdir.mkdir(parents=True, exist_ok=True)
    head = root / "listing.json"
    if not head.is_file():
        listed, stubs = list_in_scope(get, scope)
        dump(head, {"started": time.time(), "staff": staff_names(get), "listed": listed, "stubs": stubs})
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
