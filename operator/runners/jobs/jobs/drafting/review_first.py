"""SMD reads the first ledger-path briefs before they file (``evidence.review_first``).

Until SMD has released ``review_first`` ledger briefs for the firm, a brief
runs everything a filing needs (render, notes, the independent review, the
Word files, the manifest) and stops BEFORE the upload: ``held_for_smd``,
nothing filed, no reply sent (the job ends ``failed``, which the broker never
lets reach the client and which raises SMD's alert). What SMD then does is one
of two acts, both by ``python -m jobs.drafting.release`` (root, on the seat):

* **release**: the job is resumed with ``RELEASE_MARKER`` in its job dir; it
  re-enters at the filing step (its manifest is done, so nothing is rebuilt),
  checks that every file is byte for byte what was held (sha256 against
  ``HELD``, written when it stopped, and the manifest), files exactly those
  bytes, proves the read-back, and replies as any delivered job does. Nothing
  is written into the attorney notes: the release is SMD's reading, not a
  finding (unlike a review release, ``proof/review.py``).
* **discard**: it stays unfiled, with SMD's reason on the job's record.

The count of released briefs comes from the lane's release record
(``drafting_lane.RELEASES``), stamped into ``job.json`` before every run;
never from memory, and an unstamped job counts as none released.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..resume import HELD_RELEASE_MARKER
from .outcome import DraftingFailed

#: The job-dir marker a release writes (the daemon, ``resume.start_run``).
RELEASE_MARKER = HELD_RELEASE_MARKER
#: The held files' sha256s, written in the job's data dir when it stopped.
HELD = "held-for-smd.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def gate(review_first: int, released: int | None, job_dir: Path, data: Path, manifest: list[dict[str, Any]]) -> bool:
    """True when this filing is an SMD release (the caller marks its verdict);
    False when no hold applies. Raises ``held_for_smd`` to stop before upload,
    or when a released job's files are not the bytes that were held."""
    if review_first <= 0 or (released or 0) >= review_first:
        return False
    held = data / HELD
    if (job_dir / RELEASE_MARKER).is_file():
        _same_bytes(held, manifest)
        return True
    if not held.is_file():
        body = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": [{"name": m["name"], "role": m.get("role"), "sha256": m["sha256"]} for m in manifest],
        }
        (data / f".{HELD}.tmp").write_text(json.dumps(body, indent=1), encoding="utf-8")
        (data / f".{HELD}.tmp").replace(held)
    raise DraftingFailed(
        f"held_for_smd: review_first: SMD reads this evidence-path brief before it files ({released or 0} of "
        f"{review_first} released so far); nothing was filed and no reply was sent"
    )


def _same_bytes(held: Path, manifest: list[dict[str, Any]]) -> None:
    if not held.is_file():
        raise DraftingFailed("held_for_smd: a release arrived for a job that never stopped for SMD; nothing was filed")
    want = {f["name"]: f["sha256"] for f in json.loads(held.read_text(encoding="utf-8"))["files"]}
    for m in manifest:
        on_disk = _sha(Path(m["local_path"])) if Path(m["local_path"]).is_file() else ""
        if want.get(m["name"]) != m["sha256"] or on_disk != m["sha256"]:
            raise DraftingFailed(
                f"held_for_smd: the {m.get('role') or 'file'} is not the bytes SMD read; nothing was filed"
            )
    if set(want) != {m["name"] for m in manifest}:
        raise DraftingFailed("held_for_smd: the files to file are not the files SMD read; nothing was filed")
