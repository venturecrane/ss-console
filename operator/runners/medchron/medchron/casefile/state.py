"""The case file lane's state on the volume: what it already read and paid for.

    <state>/records/<matter_id>.json    the last DELIVERED review of the matter:
                                        {review, rank, fingerprint, manifest,
                                         job_id, delivered_at, read}
    <state>/proposals/<job_id>.json     every numbered line a job proposed, with
                                        the facts its apply needs (the broker
                                        holds only ids and this file's hashes)
    <state>/own-writes.jsonl            every write the lane made (``own_writes``)

Written only after a read-back (``report``), so a held or failed job leaves the
last delivered record standing. A later job reads it back two ways:

* **$0 before paid** (``fingerprint``): a matter whose inputs (the file
  listing, its tasks, events, memos, layouts, status and staff) are unchanged
  since the delivered record is ALREADY CURRENT: no fetch, no model call, its
  stored record is re-ranked in code against today.
* **Reuse every paid read**: a matter that did change is read with its prior
  record plus only the documents new or changed since it (``pack.py``), unless
  the record is older than ``full_reread_days``.

Client data: the records hold matter facts. This directory is a runtime layer
of the seat's volume, removed with the seat ("gone means gone").
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import own_writes

ENV = "MEDCHRON_CASEFILE_STATE_DIR"
DATA_ENV = "MEDCHRON_DATA_DIR"
DEFAULT_DATA_DIR = "/opt/data/medchron"


def state_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        root = Path(explicit)
    elif os.environ.get(ENV):
        root = Path(os.environ[ENV])
    else:
        root = Path(os.environ.get(DATA_ENV) or DEFAULT_DATA_DIR) / "casefile" / "state"
    # Not created here: the entrypoint makes it, owned by the runner's uid; the
    # daemon (root) resolves the path and must never create it root-owned.
    return root


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_prior(state: Path, matter_id: str) -> dict[str, Any] | None:
    got = _read(state / "records" / f"{matter_id}.json")
    return got if isinstance(got, dict) and isinstance(got.get("review"), dict) else None


def commit(state: Path, matter_id: str, record: dict[str, Any]) -> None:
    _write(state / "records" / f"{matter_id}.json", record)


def save_proposals(state: Path, job_id: str, lines: dict[str, Any]) -> None:
    _write(state / "proposals" / f"{job_id}.json", lines)


def load_proposals(state: Path, job_id: str) -> dict[str, Any]:
    got = _read(state / "proposals" / f"{job_id}.json")
    return got if isinstance(got, dict) else {}


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _task_key(t: dict[str, Any]) -> list[Any]:
    return [
        str(t.get("id")),
        str(t.get("subject") or ""),
        bool(t.get("isCompleted")),
        str(t.get("dueDate") or t.get("dueDateOnly") or "")[:10],
        str(t.get("completedDate") or "")[:10],
        str(t.get("note") or ""),
    ]


def fingerprint(inv: dict[str, Any], own: set[str] | None = None) -> str:
    """One hash over everything a review reads, the lane's own writes left out
    (a File Status it filed, a task it closed on an approval): a matter is not
    "changed" by the Operator's own hand."""
    mine = own if own is not None else set()
    files = sorted(
        (str(f["id"]), str(f.get("modified") or f.get("created") or ""))
        for f in inv.get("files") or []
        if f.get("id") and not f.get("deleted") and str(f["id"]) not in mine
    )
    tasks = sorted(
        _task_key(t)
        for t in [*(inv.get("tasks_open") or []), *(inv.get("tasks_done") or [])]
        if str(t.get("id")) not in mine
    )
    events = sorted(
        [str(e.get("id")), str(e.get("startTime") or e.get("start") or ""), str(e.get("subject") or "")]
        for e in inv.get("events") or []
        if str(e.get("id")) not in mine
    )
    memos = sorted(
        [str(m.get("id")), str(m.get("dateCreated") or m.get("createdDate") or ""), str(m.get("title") or "")]
        for m in inv.get("memos") or []
        if str(m.get("id")) not in mine
    )
    layouts = sorted(
        [str(k), digest(v.get("values"))] for k, v in (inv.get("layouts") or {}).items() if isinstance(v, dict)
    )
    head = inv.get("matter") or {}
    who = [head.get("status"), head.get("staff_id"), sorted(head.get("assisting_ids") or [])]
    return digest({"files": files, "tasks": tasks, "events": events, "memos": memos, "layouts": layouts, "who": who})


def own_ids(state: Path) -> set[str]:
    return own_writes.ids(state)


def _stamp(f: dict[str, Any]) -> str:
    return str(f.get("modified") or f.get("created") or "")


def changed_docs(inv: dict[str, Any], manifest: dict[str, Any], own: set[str]) -> dict[str, Any]:
    """What changed in the DOCUMENTS since a delivered record's manifest, the
    Operator's own files left out: ``new`` (ids added or re-saved, sorted) and
    ``any`` (also true when a document the manifest held is gone). A matter
    whose documents did not change needs no read."""
    every = {str(f["id"]): f for f in inv.get("files") or [] if f.get("id")}
    live = {fid: f for fid, f in every.items() if not f.get("deleted")}
    new = sorted(fid for fid, f in live.items() if fid not in own and manifest.get(fid) != _stamp(f))
    # Gone: no longer listed, or deleted since (a manifest also holds entries
    # that were already deleted when it was written; those are not a change).
    gone = [
        fid
        for fid in manifest
        if fid not in live and fid not in own and (fid not in every or manifest[fid] != _stamp(every[fid]))
    ]
    return {"new": new, "any": bool(new or gone)}


def purge_matter(state: Path, matter_id: str, dry_run: bool = False) -> dict[str, int]:
    """Remove one matter from the lane's state ("gone means gone"): its
    delivered record, its proposed lines in every job's proposals, and its rows
    in the write journal. Counts what was (or, with ``dry_run``, would be) removed."""
    out = {"records": 0, "proposal_lines": 0, "own_writes": 0}
    rec = state / "records" / f"{matter_id}.json"
    if rec.is_file():
        out["records"] = 1
        if not dry_run:
            rec.unlink()
    for p in sorted((state / "proposals").glob("*.json")) if (state / "proposals").is_dir() else []:
        got = _read(p)
        if not isinstance(got, dict):
            continue
        keep = {k: v for k, v in got.items() if not (isinstance(v, dict) and v.get("matter_id") == matter_id)}
        out["proposal_lines"] += len(got) - len(keep)
        if not dry_run and len(keep) != len(got):
            _write(p, keep)
    rows = own_writes.rows(state)
    keep_rows = [r for r in rows if r.get("matter_id") != matter_id]
    out["own_writes"] = len(rows) - len(keep_rows)
    if not dry_run and out["own_writes"]:
        (state / own_writes.NAME).write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in keep_rows), encoding="utf-8"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.casefile.state purge --matter <id> [--state-dir D] [--dry-run]``."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="medchron.casefile.state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("purge", help="remove one matter from the case file lane's state")
    pg.add_argument("--matter", required=True)
    pg.add_argument("--state-dir", default=None)
    pg.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(purge_matter(state_dir(args.state_dir), args.matter, dry_run=args.dry_run)), file=sys.stdout)
    return 0


__all__ = [
    "commit",
    "purge_matter",
    "digest",
    "fingerprint",
    "load_prior",
    "load_proposals",
    "own_ids",
    "save_proposals",
    "state_dir",
]


if __name__ == "__main__":
    raise SystemExit(main())
