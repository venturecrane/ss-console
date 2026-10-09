"""`fetch`: the candidate files of every matter this run reads, onto the job's
disk. $0, read-only, resumable (``demand.pull.fetch_all`` and its
``pulled.jsonl``: a row already ``ok`` is never fetched again).

Two outcomes are kept apart because the 2026-10-07 hand run confused them:

* **Missing in Smokeball.** The file row exists, its stored content does not
  (the presigned GET answers 404, or a body that is the storage service's
  ``NoSuchKey`` XML). That is a FILE-INTEGRITY finding on the matter, recorded
  and carried to the workbook; never "no complaint in the file".
* **Not fetched yet.** A mint or transport failure a retry can fix. The stage
  fails (our machinery, resumable); a read never proceeds treating an
  unfetched paper as absent.

Reads can fetch more (``read.py``'s ``fetch_doc`` tool calls ``fetch_one``),
so a document a verifier names is opened, not reported as missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ..demand.pull import fetch_all
from ..stages.base import read_jsonl

HTTP_ABSENT = 404
MISSING = "missing_in_smokeball"
REFUSED = "refused_by_smokeball"
#: Smokeball answers 403 for a file it will not serve (deleted or restricted
#: since it was listed). A retry does not change that, and one such file must
#: not fail the whole list: it is a finding on its own matter (2026-10-09
#: replay: one 403 email on McHale failed a weekday run of 210 matters).
_REFUSED = re.compile(r"HTTP 403\b")


class FetchError(RuntimeError):
    """Files a retry can fix are still not fetched."""


def matter_dir(data: Path, matter_id: str) -> Path:
    d = data / "m" / matter_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nosuchkey(path: Path) -> bool:
    try:
        head = path.read_bytes()[:600]
    except OSError:
        return False
    return head.lstrip().startswith(b"<?xml") and b"NoSuchKey" in head


def _target(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(f["id"]),
        "name": f.get("name"),
        "ext": f.get("ext") or "",
        "size": f.get("size"),
        "folder": f.get("folderId"),
    }


def classify(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(integrity findings, retryable failures) from the pull rows."""
    missing, retry = [], []
    for r in rows:
        if r.get("ok"):
            if r.get("path") and _nosuchkey(Path(r["path"])):
                missing.append({"file_id": r["id"], "name": r.get("name"), "problem": MISSING})
            continue
        if r.get("http_status") == HTTP_ABSENT or "NoSuchKey" in str(r.get("error") or ""):
            missing.append({"file_id": r["id"], "name": r.get("name"), "problem": MISSING})
        elif r.get("http_status") == 403 or _REFUSED.search(str(r.get("error") or "")):
            missing.append({"file_id": r["id"], "name": r.get("name"), "problem": REFUSED})
        else:
            retry.append(r)
    return missing, retry


def fetch_matter(
    seat: Any, matter_id: str, files: list[dict[str, Any]], ids: list[str], data: Path, log: Callable[[str], None]
) -> dict[str, Any]:
    by_id = {str(f["id"]): f for f in files}
    targets = [_target(by_id[i]) for i in ids if i in by_id]
    rows = fetch_all(seat, matter_id, targets, matter_dir(data, matter_id), log) if targets else []
    missing, retry = classify(rows)
    return {"fetched": sum(1 for r in rows if r.get("ok")), "integrity": missing, "retry": [r["id"] for r in retry]}


def fetch_one(seat: Any, matter_id: str, f: dict[str, Any], data: Path, log: Callable[[str], None]) -> dict[str, Any]:
    """One more file, on a read's request; the row ``fetch_all`` recorded."""
    rows = fetch_all(seat, matter_id, [_target(f)], matter_dir(data, matter_id), log)
    return rows[0]


def pulled(data: Path, matter_id: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in read_jsonl(matter_dir(data, matter_id) / "pulled.jsonl")}


def local_path(data: Path, matter_id: str, file_id: str) -> Path | None:
    """The bytes for a file id; a content duplicate resolves to its original."""
    rows = pulled(data, matter_id)
    r = rows.get(file_id)
    for _ in range(3):
        if not r or not r.get("ok"):
            return None
        if r.get("path") and Path(r["path"]).is_file():
            return Path(r["path"])
        r = rows.get(str(r.get("duplicate_of") or ""))
    return None


def write_report(data: Path, report: dict[str, Any]) -> None:
    (data / "fetch.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


def cached_text(data: Path, matter_id: str, file_id: str) -> str | None:
    """A file's extracted text from the job's ``txt/``; a content duplicate
    resolves to its original. Needs no raw bytes (an offline replay)."""
    rows = pulled(data, matter_id)
    cur = file_id
    for _ in range(3):
        tp = matter_dir(data, matter_id) / "txt" / f"{cur}.txt"
        if tp.is_file():
            return tp.read_text(encoding="utf-8", errors="replace")
        nxt = (rows.get(cur) or {}).get("duplicate_of")
        if not nxt:
            return None
        cur = str(nxt)
    return None
