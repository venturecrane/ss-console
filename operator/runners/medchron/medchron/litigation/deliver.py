"""`file`: the workbook into the library matter's standing folder, read back.

Unlike the chronology's upload stage, the folder here is a STANDING one (the
firm's "Litigation Status" folder holds every list, by hand or by the
Operator), so an existing folder of that name is the destination, not a
refusal. What stays the same are the lessons the upload stage paid for:

* the file's name is this job's own (date + job suffix), so a name already in
  the folder that this run did not send is somebody else's: refused (held);
* a send is recorded as an intent BEFORE ``add_file`` and as sent after it,
  and a name with either row is never sent again (a lost response must not
  become a second copy on the firm's matter);
* the read-back is the only confirmation: the folder listed until the name
  appears at its byte count, then the file DOWNLOADED and its sha256 compared
  with the bytes built here. A short read-back fails (resumable), it is
  never reported delivered.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from ..stages.upload import _vendor_name
from .outcome import LitigationFailed, LitigationHold

READBACK_TRIES = 8
READBACK_PAUSE_SECONDS = 15.0


def _folder(seat: Any, matter_id: str, name: str) -> str | None:
    for f in seat.folder_tree(matter_id):
        if str(f.get("name") or "").strip() == name and not f.get("parentId"):
            return str(f.get("id"))
    return None


def _in_folder(seat: Any, matter_id: str, folder_id: str, name: str) -> dict[str, Any] | None:
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == folder_id and not f.get("deleted") and _vendor_name(f) == name:
            return f
    return None


def _save(path: Path, rec: dict[str, Any]) -> None:
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _sha_of_remote(seat: Any, matter_id: str, row: dict[str, Any], scratch: Path) -> str | None:
    minted = seat.mint(matter_id, [str(row["id"])])
    url = (minted[0] if minted else {}).get("url")
    if not url:
        return None
    dest = scratch / f"readback-{row['id']}"
    try:
        seat.fetch(url, dest, None)
        return hashlib.sha256(dest.read_bytes()).hexdigest()
    finally:
        dest.unlink(missing_ok=True)


def file_workbook(
    seat: Any,
    matter_id: str,
    folder_name: str,
    local: Path,
    data: Path,
    log: Callable[[str], None],
    *,
    tries: int = READBACK_TRIES,
    pause: float = READBACK_PAUSE_SECONDS,
) -> dict[str, Any]:
    """``{folder_id, file_id, name, size, sha256}`` once read back and equal."""
    blob = local.read_bytes()
    sha, name = hashlib.sha256(blob).hexdigest(), local.name
    rec_path = data / "delivery.json"
    rec: dict[str, Any] = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else {}
    if rec.get("sha256") and rec["sha256"] != sha:
        raise LitigationFailed(
            "stage_unfinished: file: the built workbook changed after a send was recorded; not resent"
        )
    folder_id = rec.get("folder_id") or _folder(seat, matter_id, folder_name)
    if not folder_id:
        created = seat.create_folder(matter_id, folder_name)
        folder_id = str(created.get("id") or created.get("folderId") or "") or _folder(seat, matter_id, folder_name)
        if not folder_id:
            raise LitigationFailed("stage_unfinished: file: the filing folder could not be created")
        log(f"created folder '{folder_name}'")
    rec.update(folder_id=str(folder_id), name=name, sha256=sha, bytes=len(blob))
    _save(rec_path, rec)
    if not (rec.get("pending") or rec.get("sent")):
        if _in_folder(seat, matter_id, str(folder_id), name) is not None:
            raise LitigationHold(
                f"filing_refused: a file named '{name}' is already in the folder and this run did not send it"
            )
        rec["pending"] = True
        _save(rec_path, rec)
        try:
            seat.add_file(matter_id, str(folder_id), name, blob)
        except Exception as exc:  # noqa: BLE001 - the outcome is unknown; the intent row stays, never resent, the read-back decides
            log(f"the send raised ({type(exc).__name__}); the read-back decides")
        else:
            rec.update(pending=False, sent=True)
            _save(rec_path, rec)
    for attempt in range(tries):
        row = _in_folder(seat, matter_id, str(folder_id), name)
        if row is not None and int(row.get("size") or 0) == len(blob):
            got = _sha_of_remote(seat, matter_id, row, data)
            if got == sha:
                rec.update(confirmed=True, file_id=str(row["id"]))
                _save(rec_path, rec)
                return {
                    "folder_id": str(folder_id),
                    "file_id": str(row["id"]),
                    "name": name,
                    "size": len(blob),
                    "sha256": sha,
                }
            if got is not None:
                raise LitigationFailed("stage_unfinished: file: the read-back bytes differ from the workbook built")
        if attempt + 1 < tries:
            time.sleep(pause)
    raise LitigationFailed(
        "stage_unfinished: file: the read-back is short after its retries; the file may still be materializing"
    )
