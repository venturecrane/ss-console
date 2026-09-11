"""`manifest`: the upload manifest for a unit's deliverable. $0.

Names the chronology document, the billing worksheet when the unit has one,
and the exhibit PDFs exactly as they will appear in the matter, with sizes
and local sha256, so the delivery plan can be read as data before anything
is written to the firm's system (the delivery itself is slice 6/7). The
worksheet is optional (built only when the unit had billing documents); the
chronology and the exhibits are required, and a missing one is exit 1.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .base import StageRun


def deliverable_names(out: Path, doc: str, worksheet_glob: str) -> list[str]:
    names = sorted(p.name for p in out.iterdir())
    worksheets = [f for f in names if fnmatch.fnmatch(f, worksheet_glob)]
    exhibits = sorted(
        (f for f in names if f.startswith("Exhibit ") and f.endswith(".pdf")),
        key=lambda n: int(re.match(r"Exhibit (\d+)", n).group(1)),
    )
    return [doc] + worksheets + exhibits


def run(sr: StageRun) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    stamp = sr.date_stamp or date.today().strftime("%m-%d-%y")
    name = str(sr.cfg.get("delivery", "chronology_name_template") or "{CLIENT} - Medical Chronology {MM-DD-YY}.docx")
    doc = name.replace("{CLIENT}", sr.unit.client_name).replace("{MM-DD-YY}", stamp)
    glob = str(sr.cfg.get("delivery", "worksheet_glob") or "* - Medical Billing Worksheet *.docx")
    folder = str(sr.cfg.get("delivery", "folder_template") or "MEDICAL CHRONOLOGY - {CLIENT} {MM-DD-YY}")
    folder = folder.replace("{CLIENT}", sr.unit.client_name).replace("{MM-DD-YY}", stamp)
    manifest = []
    for fname in deliverable_names(out, doc, glob):
        p = out / fname
        if not p.is_file():
            sr.log(f"missing deliverable: {fname}")
            return 1
        data = p.read_bytes()
        manifest.append(
            {
                "name": fname,
                "folder": folder,
                "local_path": str(p),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(m["bytes"] for m in manifest)
    sr.log(f"{len(manifest)} file(s), {total / 1048576:.1f} MB total, into '{folder}'")
    for m in manifest:
        sr.log(f"  {m['bytes'] / 1048576:7.1f} MB  {m['name']}  sha256 {m['sha256'][:16]}")
    return 0
