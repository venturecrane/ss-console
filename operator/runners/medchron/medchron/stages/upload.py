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

That lag is also why the send decision may NOT rest on the vendor's list
alone. Live 2026-09-24 the read-back held at exit 2 with every file already
on the matter, and re-running the stage re-sent all 13 into the same folder:
the list still did not carry them, so `present` was empty and each name read
as missing. A resume cannot tell "absent" from "not indexed yet", and on that
ambiguity it wrote a second copy of an entire client filing -- which only a
DESTRUCTIVE delete can undo, and that is fail-closed on a firm's seat by
design. So each send is recorded in delivery.json AS IT HAPPENS, and a name
this run already sent (same sha, same bytes) is never sent twice; it goes
straight to the read-back. Ambiguity now resolves to exit 2, which a human
can clear, instead of to a duplicate, which they largely cannot.
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
    # What a PRIOR attempt of this stage already put on the matter. The vendor's
    # list lags, so absence from `present` is not evidence a file is missing;
    # this record is, and it outranks the list for the send decision.
    sent_before = {str(f.get("name")): f for f in (delivery.get("files") or []) if f.get("sent") or f.get("confirmed")}

    def record(name: str, sha: str, nbytes: int) -> None:
        """Write the send through to disk BEFORE the next one starts, so a
        crash mid-loop cannot lose the fact that a file is already up."""
        files = [f for f in (delivery.get("files") or []) if str(f.get("name")) != name]
        files.append({"name": name, "sha256": sha, "bytes": nbytes, "sent": True, "confirmed": False})
        delivery["files"] = files
        delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")

    sent = skipped_known = 0
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
        prior = sent_before.get(m["name"])
        if prior and prior.get("sha256") == sha and prior.get("bytes") == len(data):
            skipped_known += 1
            sr.log(f"  sent earlier, not resending  {m['name']} (read-back will confirm)")
            continue
        r = seat.add_file(matter_id, folder_id, m["name"], data)
        record(m["name"], sha, len(data))
        sent += 1
        sr.log(f"  sent     {m['name']} ({len(data)} bytes; file id {(r or {}).get('fileId') or 'pending'})")
    already = len(manifest) - sent - skipped_known
    sr.log(f"{sent} file(s) sent, {already} already present, {skipped_known} sent by an earlier attempt")

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
    # `sent` is carried forward, never recomputed: losing it here would hand the
    # next attempt the same empty-list ambiguity that caused the duplicate.
    sent_now = {str(f.get("name")) for f in (delivery.get("files") or []) if f.get("sent")}
    delivery["files"] = [
        {
            "name": m["name"],
            "sha256": m["sha256"],
            "bytes": m["bytes"],
            "sent": m["name"] in sent_now or m["name"] in sent_before,
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
