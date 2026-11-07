"""An update charges only the pages the firm does not already hold read (2026-11-06).

An update charged every page it extracted, so pages the delivered chronology
had already read were charged again, the reply quoted them, and SMD credited
them back by hand. ``update/charged`` takes them off the charge, from the
update's own selection record. Invented ids and counts."""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron.update import charged
from jobs.stages.prior_reading import PREFIX


def _seed(d: Path) -> None:
    ud = d / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    rows = [("new-a", 40), ("new-b", 10), ("copy", 30), ("other", 20), ("same", 12), ("decided", 3), ("copy-of-new", 5)]
    (d / "extracted.jsonl").write_text("".join(json.dumps({"id": i, "pages": n}) + "\n" for i, n in rows))
    sel = {
        "exhibits": [
            {"id": "new-a", "pages": 40, "excluded": {"already read in a delivered exhibit": 35, "billing page": 2}},
            {"id": "new-b", "pages": 10, "excluded": {"repeats an earlier page of the new documents": 4}},
        ],
        "subsets": [
            {"id": "copy", "pages": 30, "matched": 30, "subset_of": {"id": "", "exhibit": 2, "title": "Exhibit 2"}},
            {"id": "copy-of-new", "pages": 5, "matched": 5, "subset_of": {"id": "new-a", "exhibit": 7, "title": "x"}},
        ],
        "skipped": {
            "byte-identical to a delivered exhibit": ["same"],
            PREFIX + "composer: a photograph, no record text": ["decided"],
            "not a PDF or an image": ["other"],
        },
    }
    (ud / "selection.json").write_text(json.dumps(sel))


def test_pages_the_delivered_chronology_read_are_not_charged(tmp_path):
    _seed(tmp_path)
    # 35 already read + 30 re-filed copy of a delivered exhibit + 12 byte-identical + 3 decided.
    assert charged.delivered_pages(tmp_path, "alpha") == 80
    # 120 extracted: billing, repeats of NEW pages, a copy of a NEW document and a file never read stay charged.
    assert charged.charged_pages(tmp_path, "alpha", 120) == 40


def test_with_no_selection_record_nothing_is_taken_off(tmp_path):
    assert charged.charged_pages(tmp_path, "alpha", 120) == 120
    assert charged.charged_pages(tmp_path, "alpha", 0) == 0
