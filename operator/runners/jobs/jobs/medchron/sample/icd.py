"""The ICD tables a sample build reads, without fetching CMS.

``build_doc`` reads descriptors only from the vendored CMS tables, which the
``icd_tables`` stage downloads once per seat. A sample build touches no
network, so it writes a SUBSET in the same fixed-width layout: the codes the
spec uses, each with the descriptor CMS publishes for it (authored in the spec
under ``icd``). VERSION.json says it is a subset, so nobody mistakes it for the
vendored set.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..icd_tables import ICD9_FILE, ICD10_FILE, VERSION_FILE, icd_dir
from .model import Plan


def vendor(plan: Plan, install_root: Path) -> Path:
    d = icd_dir(install_root)
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (code, desc) in enumerate(sorted(plan.icd.items()), 1):
        bare = code.replace(".", "")
        rows.append(f"{i:05d} {bare:<7} 1 {desc[:60]:<60} {desc}")
    (d / ICD10_FILE).write_text("\n".join(rows) + "\n", encoding="utf-8")
    (d / ICD9_FILE).write_text("", encoding="cp1252")
    (d / VERSION_FILE).write_text(
        json.dumps({"subset": "sample build: the codes the case spec uses, not the CMS release"}), encoding="utf-8"
    )
    return d
