"""SMD retires a delivered companion file an update left beside its new copy.

WHY (2026-11-17). An update retires the delivered billing worksheet, records
review or review notes it supersedes only when the delivered ledger row holds
the file's hash and the bytes still match it. Every chronology delivered
before file hashes were recorded has an empty file list, and the Smokeball
listing carries no author or version, only ``dateCreated``, ``dateModified``
and the size, which do not prove a copy unedited. So such a file is left in
the folder and recorded in ``update/superseded.json`` with its id. Removing it
is SMD's decision, one recorded act::

    python -m jobs.medchron.update_retire <job dir> --file-id <id> --by <who> --reason <text> [--dry-run]

It refuses an id the update did not leave, and a file whose size in the folder
is no longer the size the update read (changed since: someone else's call).
It deletes by id, reads the folder back until the id reads gone, and moves the
row to ``retired`` with who, why and when.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


class Refused(RuntimeError):
    pass


def _record(job_dir: Path) -> tuple[Any, Path, dict[str, Any]]:
    from . import job as job_mod

    job = job_mod.load(job_dir)
    rd = job.data_root / job.slug / "runs" / job.units[0].unit / "update"
    path = rd / "superseded.json"
    if not path.is_file():
        raise Refused("this job left no delivered companion file beside a new one")
    return job, path, json.loads(path.read_text(encoding="utf-8"))


def plan(job_dir: Path, file_id: str) -> tuple[Any, Path, dict[str, Any], dict[str, Any]]:
    job, path, rec = _record(job_dir)
    row = next((x for x in rec.get("left") or [] if str(x.get("id")) == file_id), None)
    if row is None:
        raise Refused(f"{file_id} is not a file this update left in the folder; nothing was done")
    return job, path, rec, row


def apply(seat: Any, job: Any, path: Path, rec: dict[str, Any], row: dict[str, Any], by: str, reason: str) -> None:
    from ..stages.update_upload import _folder_rows

    where = (job.matter_id, str(row["folder_id"]))
    now = next((r for r in _folder_rows(seat, *where) if str(r.get("id")) == str(row["id"])), None)
    if now is not None and row.get("size") is not None and now.get("size") != row.get("size"):
        raise Refused("the file's size changed since the update read it; it is left as it is")
    if now is not None:
        seat.delete_file(job.matter_id, str(row["id"]))
        for _ in range(8):
            if not any(str(r.get("id")) == str(row["id"]) for r in _folder_rows(seat, *where)):
                break
            time.sleep(4)
        else:
            raise Refused("the delete did not read back gone; the file is recorded as still present")
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec["left"] = [x for x in rec.get("left") or [] if str(x.get("id")) != str(row["id"])]
    rec.setdefault("retired", []).append(
        {"name": row["name"], "kind": row["kind"], "by": by, "reason": reason, "at": at}
    )
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.update_retire", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--file-id", required=True)
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        job, path, rec, row = plan(a.job_dir, a.file_id)
        print(json.dumps({"dry_run": a.dry_run, "kind": row.get("kind"), "why_left": row.get("why")}, indent=1))
        if a.dry_run:
            return 0
        from ..seat import open_seat

        apply(open_seat(job.slug), job, path, rec, row, a.by, a.reason)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"retired the delivered {row.get('kind')} by id; read back gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
