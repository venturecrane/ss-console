"""`upload`: the deliverable set onto the matter, into its own dated folder.
$0. The last stage; nothing is written to the firm's system before it and
nothing after.

Idempotent by construction (ss#2614). The folder id is recorded in
`runs/<unit>/delivery.json` BEFORE the first file goes up, so a crash between
create_folder and the last add_file resumes by reconciling: list the folder,
add only the names that are missing or the wrong size. A folder of the target
name that this run did not create is somebody else's and the stage refuses
(exit 1); the runner never deletes anything on the matter.

`add_file` returning a null file id is normal and is not confirmation. The
only confirmation is the folder read back with every name at its byte count,
retried across the vendor's index lag; a short read-back after the retries is
exit 2 (held: the files may still be materializing).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .base import StageRun

READBACK_TRIES = 8
READBACK_PAUSE_SECONDS = 15.0


def _folder_by_name(seat: Any, matter_id: str, name: str) -> dict[str, Any] | None:
    for f in seat.folder_tree(matter_id):
        if str(f.get("name") or "").strip() == name and not f.get("parentId"):
            return f
    return None


def _files_in(seat: Any, matter_id: str, folder_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == str(folder_id) and not f.get("deleted"):
            out[str(f.get("name") or "")] = int(f.get("size") or 0)
    return out


def run(sr: StageRun, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    manifest = json.loads((out / "upload_manifest.json").read_text(encoding="utf-8"))
    if not manifest:
        sr.log("empty upload manifest")
        return 1
    folder_name = str(manifest[0]["folder"])
    matter_id = sr.job.matter_id
    delivery_path = sr.slug_dir / "runs" / sr.unit.unit / "delivery.json"
    delivery: dict[str, Any] = json.loads(delivery_path.read_text(encoding="utf-8")) if delivery_path.is_file() else {}
    seat = sr.seat

    if delivery.get("folder_id"):
        folder_id = str(delivery["folder_id"])
        sr.log(f"resuming into folder {folder_id} ('{folder_name}') created by this run")
    else:
        existing = _folder_by_name(seat, matter_id, folder_name)
        if existing:
            sr.log(
                f"a folder named '{folder_name}' already exists on the matter (id {existing.get('id')}) and this "
                f"run did not create it; refusing to write into it"
            )
            return 1
        created = seat.create_folder(matter_id, folder_name)
        folder_id = str(created.get("id") or created.get("folderId") or "")
        if not folder_id:
            again = _folder_by_name(seat, matter_id, folder_name)
            folder_id = str((again or {}).get("id") or "")
        if not folder_id:
            sr.log("create_folder returned no id and the folder is not visible on the matter")
            return 1
        delivery = {"folder": folder_name, "folder_id": folder_id, "files": []}
        delivery_path.parent.mkdir(parents=True, exist_ok=True)
        delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
        sr.log(f"created folder '{folder_name}' (id {folder_id})")

    present = _files_in(seat, matter_id, folder_id)
    sent = 0
    for m in manifest:
        p = Path(m["local_path"])
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != m["sha256"]:
            sr.log(f"{m['name']}: local bytes changed since the manifest (sha mismatch); refusing")
            return 1
        if present.get(m["name"]) == len(data):
            sr.log(f"  present  {m['name']}")
            continue
        r = seat.add_file(matter_id, folder_id, m["name"], data)
        sent += 1
        sr.log(f"  sent     {m['name']} ({len(data)} bytes; file id {(r or {}).get('fileId') or 'pending'})")
    sr.log(f"{sent} file(s) sent, {len(manifest) - sent} already present")

    expected = {m["name"]: m["bytes"] for m in manifest}
    for attempt in range(tries):
        present = _files_in(seat, matter_id, folder_id)
        short = [n for n, b in expected.items() if present.get(n) != b]
        if not short:
            break
        if attempt + 1 < tries:
            sr.log(f"read-back: {len(short)} of {len(expected)} not yet at size; waiting")
            time.sleep(pause)
    else:
        short = [n for n, b in expected.items() if _files_in(seat, matter_id, folder_id).get(n) != b]
    delivery["files"] = [
        {
            "name": m["name"],
            "sha256": m["sha256"],
            "bytes": m["bytes"],
            "confirmed": present.get(m["name"]) == m["bytes"],
        }
        for m in manifest
    ]
    delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
    if short:
        sr.log(f"read-back short after {tries} tries: {', '.join(short)}")
        return 2
    sr.log(f"read-back complete: {len(expected)} file(s) at the expected byte counts in folder {folder_id}")
    return 0
