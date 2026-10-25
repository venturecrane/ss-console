"""What an update into the delivered chronology COVERED, in document ids. $0.

The same two sets `covered.py` reports for a run, from the update's own
artifacts (an update has no coverage gate: it never builds a whole-matter
unit, and reading the run's gate artifacts would call every new document
uncovered, so the next update would read them all again):

    covered    cited as a new exhibit; read in full and found to record no
               visit the chronology does not already list (its pages billing,
               repeats, or a medical-legal report); byte-identical to
               a delivered exhibit or to another pulled document; or left out
               by one of the firm's authored rules or at the firm's direction
               (an identity decision).
    uncovered  retrieval failed; not a PDF or an image (never read); an image
               the page transcription could not read; or read
               and found to carry no text at all (an unreadable record is read
               again by the next update, as a run's contentless scan is).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..gates.foreign_identity import WITHHELD_REASONS
from ..stages.base import read_json, read_jsonl
from .outcome import IMAGE_UNREAD
from .select import BLANK


def covered_sets(slug_dir: Path, unit: str, cfg: Any = None) -> dict[str, Any]:
    ud = slug_dir / "runs" / unit / "update"
    sel = read_json(ud / "selection.json", {})
    covered: set[str] = set()
    uncovered: set[str] = set()
    for r in read_jsonl(slug_dir / "raw_manifest.jsonl"):
        rid = str(r.get("id") or "")
        if not rid:
            continue
        if not r.get("ok"):
            uncovered.add(rid)
        elif r.get("duplicate_of"):
            covered.add(rid)
    skipped = sel.get("skipped") or {}
    covered |= {str(x) for x in skipped.get("byte-identical to a delivered exhibit") or []}
    uncovered |= {str(x) for x in skipped.get("not a PDF or an image") or []}
    # An image the transcription could not read (``update_select._set_aside``): read again next time.
    uncovered |= {str(x) for x in skipped.get(IMAGE_UNREAD) or []}
    covered |= {str(x) for why in WITHHELD_REASONS for x in skipped.get(why) or []}
    covered |= {str(x.get("id")) for x in sel.get("excluded_named") or [] if x.get("id")}
    for x in sel.get("exhibits") or []:
        # Only a document with NO text is read again: one whose pages are all
        # billing, repeats, or a medical-legal report was reviewed in full.
        blank_only = not x.get("read") and set(x.get("excluded") or {}) <= {BLANK}
        (uncovered if blank_only else covered).add(str(x["id"]))
    covered -= uncovered
    return {"covered": sorted(covered), "uncovered": sorted(uncovered), "pulled": len(covered) + len(uncovered)}
