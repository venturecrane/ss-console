"""`list_matter`: the matter's document manifest and folder tree, from the
seat, written as `manifest.json` and `folders.json`.

The frozen pipeline had no stage for this. A person ran the two seat scripts
by hand before a run, and `decide_selection` simply assumed `folders.json`
was there. That is the kind of prose step the driver exists to replace: a run
that starts from a matter id must produce its own listing.
"""

from __future__ import annotations

import json

from .base import StageRun


def run(sr: StageRun) -> int:
    matter_id = sr.job.matter_id
    docs = sr.seat.list_files(matter_id)
    folders = sr.seat.folder_tree(matter_id)
    sr.slug_dir.mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "manifest.json").write_text(
        json.dumps({"mode": "list", "count": len(docs), "documents": docs}, indent=1), encoding="utf-8"
    )
    (sr.slug_dir / "folders.json").write_text(json.dumps(folders, indent=1), encoding="utf-8")
    live = sum(1 for d in docs if not d.get("deleted"))
    sr.log(f"{sr.slug}: {len(docs)} documents listed ({live} live), {len(folders)} folders")
    return 0
