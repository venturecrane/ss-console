"""`list_matter`: the matter's document manifest and folder tree, from the
seat, written as `manifest.json` and `folders.json`.

The frozen pipeline had no stage for this. A person ran the two seat scripts
by hand before a run, and `decide_selection` simply assumed `folders.json`
was there. That is the kind of prose step the driver exists to replace: a run
that starts from a matter id must produce its own listing.

It is also where a RUN on a matter that already holds a delivered chronology
stops (2026-10-13), before any paid stage. The broker refuses that RUN at
submit from its own ledger; a chronology delivered before the ledger held it
(the laptop pipeline, never backfilled) is visible only here, in the matter's
own files, found by the firm's file name for it with the same locator the
update reads it with (`update/locate.py`). Exit 3, refused, with the update
shape in the reason (`dag.py`). A listing that cannot be read raises, so the
stage fails and nothing downstream runs: "could not look" is never "none".
"""

from __future__ import annotations

import json
from typing import Any

from ..update import locate as L
from .base import StageRun

#: "This RUN is an update": `dag.py` maps it to REFUSED with the update shape.
DELIVERED_EXIT = 3
DEFAULT_TEMPLATE = "{CLIENT} - Medical Chronology {MM-DD-YY}.docx"


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
    found = delivered_chronologies(sr, docs)
    if found:
        sr.log(f"a delivered chronology is already on the matter ({'; '.join(found[:3])}); this request is an update")
        return DELIVERED_EXIT
    return 0


def delivered_chronologies(sr: StageRun, docs: list[dict[str, Any]]) -> list[str]:
    """The live documents named as one of this job's clients' delivered
    chronology, when this job is a RUN (an update reads them on purpose).

    Empty once this job has itself begun delivering (`runs/<unit>/delivery.json`
    exists), so a resume that re-lists the matter never refuses on its own
    upload."""
    if sr.job.update is not None:
        return []
    if any((sr.slug_dir / "runs" / u.unit / "delivery.json").exists() for u in sr.job.units):
        return []
    template = str(sr.cfg.get("delivery", "chronology_name_template") or DEFAULT_TEMPLATE)
    patterns = [L.name_pattern(template, u.client_name) for u in sr.job.units]
    return [
        L.vendor_name(d) for d in docs if not d.get("deleted") and any(rx.match(L.vendor_name(d)) for rx in patterns)
    ]
