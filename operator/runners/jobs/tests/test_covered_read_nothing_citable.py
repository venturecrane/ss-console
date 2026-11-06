"""A file the composer read and said holds nothing citable is COVERED (2026-11-06).

The delivered run read it in full, the composer wrote why nothing in it is
citable, and the coverage gate accepted that account. The record called it
uncovered and NOT EXPLAINED, so the firm was told the file was never read and
the next update asked for it again; an update already counts the same finding
covered. A file the composer passed over with no reason stays uncovered.
Invented names and ids."""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron.covered import covered_sets


class _Cfg:
    def get(self, *keys, default=None):
        return [] if keys == ("coverage", "exclusions") else default


def _seed(d: Path) -> None:
    files = [
        {"id": "doc-photo", "name": "Scene photo", "ext": ".jpg"},
        {"id": "doc-ledger", "name": "Vendor ledger", "ext": ".pdf"},
        {"id": "doc-quiet", "name": "Quiet letter", "ext": ".pdf"},
        {"id": "doc-visit", "name": "Clinic visit", "ext": ".pdf"},
    ]
    (d / "units").mkdir(parents=True)
    (d / "runs" / "u").mkdir(parents=True)
    (d / "units" / "u.json").write_text(json.dumps(files))
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps({**f, "ok": True}) + "\n" for f in files))
    (d / "runs" / "u" / "map-01.md").write_text(
        "=== FILE: Scene photo.jpg (fileId doc-photo) === | nothing extractable: a photograph, no record text\n"
        "=== FILE: Vendor ledger.pdf (fileId doc-ledger) === | entries: 0, billing-dates: 12\n"
        "=== FILE: Quiet letter.pdf (fileId doc-quiet) === | entries: 0\n"
        "=== FILE: Clinic visit.pdf (fileId doc-visit) === | entries: 1\n"
    )
    (d / "runs" / "u" / "exhibit_map.json").write_text(json.dumps({"Clinic visit.pdf": 1}))
    (d / "runs" / "u" / "entries_scoped_final.md").write_text(
        "## Medical Chronology\n\n01/02/2026 visit (Exhibit 1 - p. 1)\n"
    )


def test_a_file_read_with_nothing_citable_is_covered(tmp_path):
    _seed(tmp_path)
    rec = covered_sets(tmp_path, "u", _Cfg())
    assert rec["covered"] == ["doc-ledger", "doc-photo", "doc-visit"]
    # Falsifier: "entries: 0" with no reason explains nothing.
    assert rec["uncovered"] == ["doc-quiet"]
    assert rec["why"] == {"doc-quiet": "not_explained"}
    assert rec["pulled"] == 4
