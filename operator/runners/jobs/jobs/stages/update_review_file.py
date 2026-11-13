"""File an already-current update's records review beside the chronology.

An update that adds nothing to the delivered chronology still answers the
firm's question, file by file (``update/review_list``). That one document is
filed into the delivered chronology's folder by the same filing as every
update (``jobs/filing.file_into_folder``, through ``update_upload.prepare``): a
name the folder already carries and this run did not send is versioned, the
send is written through so a resume never sends twice, the folder is read back
at size, then the file is downloaded by id and hashed. Nothing is retired: the
delivered chronology stays exactly as it is.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import filing
from ..proof.readback import READBACK_PAUSE_SECONDS, READBACK_TRIES
from .base import StageRun
from .update_base import plan
from .update_upload import _folder_rows, prepare, record_of


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
    rec = record_of(sr, p)
    seat = sr.seat
    manifest = prepare(sr, out, rec, _folder_rows(seat, matter_id, folder_id), manifest)
    scratch = sr.slug_dir / "runs" / sr.unit.unit / "readback"
    filed = filing.file_into_folder(
        seat, matter_id, folder_id, manifest, rec, sr.log, scratch, tries=tries, pause=pause
    )
    if filed.status in ("held", "refused"):
        return None, f"the send did not complete ({filed.status})"
    if filed.status == "short":
        return None, f"the folder read back short after {tries} tries"
    rows = rec.files()
    if filed.status != "ok" or not rows or not all(f.get("proven") for f in rows):
        return None, "the filed review was not proven by hash"
    return rows, ""
