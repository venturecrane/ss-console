"""The lane's write journal: every id the Case File Manager wrote to a matter.

Each File Status it filed, each folder it created, each task it completed and
each Medicals row it added is one line in ``<state>/own-writes.jsonl``::

    {"job_id", "matter_id", "kind": "file"|"folder"|"task"|"medicals"|"contact",
     "id", "at"}

so a later read can tell the Operator's own activity from the firm's: the
fingerprint (``state.fingerprint``) leaves these ids out, and the change
detection a recheck will run reads :func:`ids` / :func:`by_matter` the same
way. Appended before the job reports its writes; never rewritten.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

NAME = "own-writes.jsonl"
KINDS = ("file", "folder", "task", "medicals", "contact")


def record(state: Path, job_id: str, matter_id: str, kind: str, write_id: str) -> None:
    if kind not in KINDS or not write_id:
        return
    row = {
        "job_id": job_id,
        "matter_id": matter_id,
        "kind": kind,
        "id": str(write_id),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.mkdir(parents=True, exist_ok=True)
    with (state / NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def rows(state: Path) -> list[dict[str, Any]]:
    try:
        lines = (state / NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            got = json.loads(ln)
        except ValueError:
            continue
        if isinstance(got, dict) and got.get("id"):
            out.append(got)
    return out


def ids(state: Path, kinds: tuple[str, ...] = KINDS) -> set[str]:
    return {str(r["id"]) for r in rows(state) if r.get("kind") in kinds}


def by_matter(state: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in rows(state):
        out.setdefault(str(r.get("matter_id") or ""), set()).add(str(r["id"]))
    return out


__all__ = ["KINDS", "by_matter", "ids", "record", "rows"]
