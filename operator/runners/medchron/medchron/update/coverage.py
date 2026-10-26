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

Each uncovered id also carries a reason token in ``why`` (``uncovered_why``),
so a follow-up about an unread file is answered from the record, not guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..gates.foreign_identity import WITHHELD_REASONS
from .. import uncovered_why as W
from ..stages.base import read_json, read_jsonl
from ..stages.prior_reading import PREFIX as PRIOR_PREFIX
from ..unopened import REASON as PASSWORD_REASON
from .outcome import IMAGE_UNREAD
from .select import BLANK


def covered_sets(slug_dir: Path, unit: str, cfg: Any = None) -> dict[str, Any]:
    ud = slug_dir / "runs" / unit / "update"
    sel = read_json(ud / "selection.json", {})
    covered: set[str] = set()
    why: dict[str, str] = {}

    def unread(ids: Any, token: str) -> None:
        for x in ids or []:
            why.setdefault(str(x), token)

    for r in read_jsonl(slug_dir / "raw_manifest.jsonl"):
        rid = str(r.get("id") or "")
        if not rid:
            continue
        if not r.get("ok"):
            unread([rid], W.RETRIEVAL_FAILED)
        elif r.get("duplicate_of"):
            covered.add(rid)
    skipped = sel.get("skipped") or {}
    covered |= {str(x) for x in skipped.get("byte-identical to a delivered exhibit") or []}
    unread(skipped.get("not a PDF or an image"), W.NOT_PDF_OR_IMAGE)
    # An image the transcription could not read (``update_select._set_aside``): read again next time.
    unread(skipped.get(IMAGE_UNREAD), W.IMAGE_UNREADABLE)
    covered |= {str(x) for why_ in WITHHELD_REASONS for x in skipped.get(why_) or []}
    # The delivered run's own finding settles it (``prior_reading``); a locked PDF is read once it opens.
    covered |= {str(x) for why_, ids in skipped.items() if why_.startswith(PRIOR_PREFIX) for x in ids or []}
    unread(skipped.get(PASSWORD_REASON), W.PASSWORD_PROTECTED)
    covered |= {str(x.get("id")) for x in sel.get("excluded_named") or [] if x.get("id")}
    for x in sel.get("exhibits") or []:
        # Only a document with NO text is read again: one whose pages are all
        # billing, repeats, or a medical-legal report was reviewed in full.
        if not x.get("read") and set(x.get("excluded") or {}) <= {BLANK}:
            unread([x["id"]], W.NO_TEXT)
        else:
            covered.add(str(x["id"]))
    uncovered = set(why)
    covered -= uncovered
    out: dict[str, Any] = {
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "pulled": len(covered) + len(uncovered),
    }
    if why:
        out["why"] = W.restricted(why, uncovered)
    return out
