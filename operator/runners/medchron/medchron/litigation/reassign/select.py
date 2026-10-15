"""`select`: which matters move, read-only.

Every matter in the asked statuses whose Person Assisting is one of the FROM
people is a candidate. A candidate is IN SCOPE when (litigation only) it passes
the deadlines report's litigation test (``deadlines/book.py`` ``Rules.is_lit``:
enough court-named file names, or a Filed date, or a Case number) and (when an
attorney was named) its responsible staff is that attorney. Every candidate
outside the scope is counted by its responsible attorney, so the reply can say
what was left and why.

For each in-scope matter the open tasks naming a FROM person are recorded
BEFORE anything is written (the baseline): after the move, those that name the
TO person and no FROM person were moved by the practice system itself.

A candidate whose reads raise is not guessed at: the stage fails
``read_incomplete`` once every other candidate is read, and the resume reads
only what is missing (``data/select/<id>.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ...arrivals import assisting_ids, getter, paged
from ..deadlines.book import Rules
from ..deadlines.pull import _layout_values
from ..outcome import LitigationFailed
from ..tools import dump

CASE_NUMBER = "Matter/CaseDetails/StandardCaseDetails/CaseNumber"
FILED = "Matter/CaseDetails/PleadingsDetails/FiledDate"


def task_ids(row: dict[str, Any]) -> list[str]:
    """The assignee ids on a task row, lowercased, in order, once each."""
    out = []
    for a in row.get("assignees") or []:
        sid = a.get("id") if isinstance(a, dict) else a
        if isinstance(sid, str) and sid.strip():
            out.append(sid.strip().lower())
    return list(dict.fromkeys(out))


def open_tasks(get: Callable[..., Any], mid: str) -> list[dict[str, Any]]:
    return [
        t
        for t in paged(get, "/tasks", MatterId=mid, IsCompleted=False)
        if isinstance(t, dict) and t.get("id") and not t.get("isCompleted") and not t.get("isDeleted")
    ]


def candidates(get: Callable[..., Any], statuses: list[str], from_ids: set[str]) -> tuple[int, list[dict[str, Any]]]:
    """(rows listed, the matters a FROM person assists)."""
    listed, out = 0, []
    for status in statuses:
        for r in paged(get, "/matters", Status=status, IsLead=False):
            listed += 1
            if not isinstance(r, dict) or r.get("isLead") is True or not r.get("id"):
                continue
            on = sorted(set(assisting_ids(r)) & from_ids)
            if not on:
                continue
            out.append(
                {
                    "id": str(r["id"]).lower(),
                    "number": str(r.get("number") or ""),
                    "status": status,
                    "resp": str(r.get("personResponsibleStaffId") or "").lower(),
                    "from_on": on,
                }
            )
    return listed, out


def read_candidate(get: Callable[..., Any], stub: dict[str, Any], need_lit: bool, from_ids: set[str]) -> dict[str, Any]:
    """The litigation evidence (only when the scope needs it) and the open
    tasks naming a FROM person. Raises on any failed read."""
    rec = dict(stub)
    mid = stub["id"]
    if need_lit:
        vals = _layout_values(get, mid)
        rec["caseNumber"], rec["filed"] = vals.get(CASE_NUMBER), vals.get(FILED)
        files = paged(get, "/matters/" + mid + "/documents/files")
        rec["fileNames"] = [str(f.get("name") or f.get("fileName") or "") for f in files if isinstance(f, dict)]
    rec["baseline"] = [t["id"] for t in open_tasks(get, mid) if set(task_ids(t)) & from_ids]
    return rec


def run(
    seat: Any, data: Path, resolved: dict[str, Any], spec: dict[str, Any], rules: Rules, log: Callable[[str], None]
) -> dict[str, Any]:
    get = getter(seat)
    from_ids = set(resolved["from_ids"])
    root = data / "select"
    root.mkdir(parents=True, exist_ok=True)
    head = root / "listing.json"
    if not head.is_file():
        listed, stubs = candidates(get, spec["statuses"], from_ids)
        dump(head, {"listed": listed, "stubs": stubs})
    stubs = json.loads(head.read_text(encoding="utf-8"))["stubs"]
    failed = 0
    for stub in stubs:
        path = root / f"{stub['id']}.json"
        if path.is_file():
            continue
        try:
            dump(path, read_candidate(get, stub, spec["litigation_only"], from_ids))
        except Exception as exc:  # noqa: BLE001 - counted; the stage fails after the others are read
            failed += 1
            log(f"[select] a matter read raised {type(exc).__name__}")
    if failed:
        raise LitigationFailed(f"read_incomplete: {failed} of {len(stubs)} matters could not be read; resumable")
    recs = [json.loads((root / f"{s['id']}.json").read_text(encoding="utf-8")) for s in stubs]
    return plan(recs, resolved, spec, rules)


def plan(recs: list[dict[str, Any]], resolved: dict[str, Any], spec: dict[str, Any], rules: Rules) -> dict[str, Any]:
    """The move list and what stays, by responsible attorney's name."""
    attorneys = set(resolved["attorney_ids"])
    names = resolved["names"]
    move, outside, moved_from = [], {}, set()
    for m in recs:
        lit_ok = not spec["litigation_only"] or rules.is_lit(m)
        att_ok = not attorneys or m["resp"] in attorneys
        if lit_ok and att_ok:
            move.append({k: m[k] for k in ("id", "number", "resp", "baseline")})
            moved_from.update(m["from_on"])
        else:
            who = names.get(m["resp"]) or "no responsible attorney"
            outside[who] = outside.get(who, 0) + 1
    return {
        "matters_read": len(recs),
        "move": move,
        "outside": dict(sorted(outside.items())),
        "from_moved_ids": sorted(moved_from),
    }


__all__ = ["candidates", "open_tasks", "plan", "read_candidate", "run", "task_ids"]
