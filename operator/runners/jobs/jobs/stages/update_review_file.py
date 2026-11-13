"""File an already-current update's records review beside the chronology.

An update that adds nothing to the delivered chronology still answers the
firm's question, file by file (``update/review_list``). That one document is
filed into the delivered chronology's folder by the same filing as every
update (``jobs/filing.file_into_folder``, through ``update_upload.prepare``): a
name the folder already carries and this run did not send is versioned, the
send is written through so a resume never sends twice, the folder is read back
at size, then the file is downloaded by id and hashed. Nothing is retired: the
delivered chronology stays exactly as it is.

**The review is built once per job (wave 3).** The driver renders it again on
every ending, and a ``.docx`` is not byte-stable (the save time is stamped in
it) and its name carries the run's date. So once a send of the review is
recorded (a sent or pending row), every later attempt files THAT review: the
same local file, name and sha, restored from the copy kept when it was first
sent, and the freshly rendered document is ignored. Otherwise a short read-back
could never be filed again (the bytes "changed after they were sent"), and a
re-run on a later day would file a second review under a second name.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import filing
from ..filing import SendRecord
from ..proof.readback import READBACK_PAUSE_SECONDS, READBACK_TRIES
from .base import StageRun
from .update_base import plan
from .update_upload import _folder_rows, prepare, record_of


#: Where each review's bytes are kept, by sha256, before its first send.
SENT_COPIES = "review-sent"


def _sent_review(sr: StageRun, out: Path) -> dict[str, Any] | None:
    """The review an earlier attempt of this job sent or started: its row in
    the persisted manifest, when the send record names it."""
    from ..medchron.update import locate, review_list

    mpath = out / "upload_manifest.json"
    if not mpath.is_file():
        return None
    ours = SendRecord.load(sr.slug_dir / "runs" / sr.unit.unit / "delivery.json").recorded()
    rx = locate.name_pattern(review_list.TEMPLATE, sr.unit.client_name)
    for m in json.loads(mpath.read_text(encoding="utf-8")):
        if str(m.get("name")) in ours and rx.fullmatch(str(m.get("intended_name") or m.get("name") or "")):
            return m
    return None


def _restore(sr: StageRun, m: dict[str, Any]) -> bool:
    """The exact bytes that were sent, back at their own path; False when
    neither that file nor the kept copy holds them."""
    local = Path(m["local_path"])
    if local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == m["sha256"]:
        return True
    copy = sr.slug_dir / "runs" / sr.unit.unit / SENT_COPIES / f"{m['sha256']}.docx"
    if not copy.is_file():
        return False
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(copy.read_bytes())
    return True


def _manifest(sr: StageRun, out: Path, doc: Path, folder_name: str) -> list[dict[str, Any]]:
    """A review never sent: its manifest, written, and its bytes kept by sha."""
    data = doc.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    manifest = [{"name": doc.name, "folder": folder_name, "local_path": str(doc), "sha256": sha, "bytes": len(data)}]
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    copy = sr.slug_dir / "runs" / sr.unit.unit / SENT_COPIES / f"{sha}.docx"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(data)
    return manifest


def file(
    sr: StageRun, doc: Path, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES
) -> tuple[list[dict[str, Any]] | None, str]:
    """(the proven delivery rows, "") or (None, why it was not filed). ``doc``
    is filed only when no earlier attempt of this job sent or started a review."""
    p = plan(sr)
    if not p:
        return None, "no update plan"
    out = sr.slug_dir / "out" / sr.unit.unit
    earlier = _sent_review(sr, out)
    if earlier is None:
        manifest = _manifest(sr, out, doc, p["folder_name"])
    elif not _restore(sr, earlier):
        return (
            None,
            "the records review this job sent earlier is not on disk at the bytes it sent; a person reads the folder",
        )
    else:
        sr.log(f"filing the records review this job already sent ({earlier['name']}); it is never rebuilt or renamed")
        manifest = [earlier]
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
