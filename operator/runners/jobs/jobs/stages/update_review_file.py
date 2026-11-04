"""File an already-current update's records review beside the chronology.

An update that adds nothing to the delivered chronology still answers the
firm's question, file by file (``update/review_list``). That one document is
filed into the delivered chronology's folder by the same machinery as every
upload (``update_upload``): a name the folder already carries and this run did
not send is versioned, the send is written through so a resume never sends
twice, the folder is read back at size, then the file is downloaded by id and
hashed. Nothing is retired: the delivered chronology stays exactly as it is.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .base import StageRun
from .update_base import plan
from .update_upload import READBACK_PAUSE_SECONDS, READBACK_TRIES, _folder_rows, _readback, _version_clashes
from .upload import _files_in, _prove, _send_missing, _write_readback


def file(
    sr: StageRun, doc: Path, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES
) -> tuple[list[dict[str, Any]] | None, str]:
    """(the proven delivery rows, "") or (None, why it was not filed)."""
    p = plan(sr)
    if not p:
        return None, "no update plan"
    out = sr.slug_dir / "out" / sr.unit.unit
    data = doc.read_bytes()
    manifest = [
        {
            "name": doc.name,
            "folder": p["folder_name"],
            "local_path": str(doc),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    ]
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    matter_id, folder_id = sr.job.matter_id, str(p["folder_id"])
    dpath = sr.slug_dir / "runs" / sr.unit.unit / "delivery.json"
    delivery: dict[str, Any] = json.loads(dpath.read_text(encoding="utf-8")) if dpath.is_file() else {}
    delivery.update({"folder": p["folder_name"], "folder_id": folder_id, "update": True})
    delivery.setdefault("files", [])
    seat = sr.seat
    ours = {str(f.get("name")) for f in delivery["files"] if f.get("sent") or f.get("pending") or f.get("confirmed")}
    manifest = _version_clashes(sr, out, _folder_rows(seat, matter_id, folder_id), manifest, ours)
    dpath.parent.mkdir(parents=True, exist_ok=True)
    dpath.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
    counts, why = _send_missing(
        sr, seat, matter_id, folder_id, manifest, _files_in(seat, matter_id, folder_id), delivery, dpath
    )
    if counts is None:
        return None, f"the send did not complete ({why})"
    short = _readback(sr, seat, matter_id, folder_id, manifest, tries, pause)
    _write_readback(delivery, manifest, _files_in(seat, matter_id, folder_id), dpath)
    if short:
        return None, f"the folder read back short after {tries} tries"
    if _prove(sr, seat, matter_id, folder_id, manifest, delivery, dpath, pause=pause, tries=tries) != 0:
        return None, "the filed review was not proven by hash"
    rows = json.loads(dpath.read_text(encoding="utf-8")).get("files") or []
    if not rows or not all(f.get("proven") for f in rows):
        return None, "the filed review was not proven by hash"
    return rows, ""
