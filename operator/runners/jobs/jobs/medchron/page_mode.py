"""Which pages a checker reads as TEXT and which as a PICTURE. $0, no model.

WHY (2026-11-18). A real 509-page update's review cost $46.48, nearly all of
it page pictures: a picture costs about three times its text, and 493 of the
509 pages carried a usable text layer. The per-sentence audit already reads a
native text layer in text mode (``audit/run.py``); this is the ONE rule both
the update review and that audit use to decide a page, so a page one checker
reads as text the other never reads as a picture for a different reason.

A page is read as TEXT only when ALL of these hold, and as a picture
otherwise (when in doubt, picture):

* the page index calls it ``native`` (``audit/page_text.PageIndex.classify``)
  at ``TEXT_MIN_CHARS``: its source file passed the extract gates, the text
  is the file's own layer and not a machine transcription (the vision
  pass's pages, handwriting among them), the PDF page has no form-field
  widgets and no checkbox glyphs (a FORM), no invisible or OCR-font text, the
  text file and the PDF page agree, and it carries at least 200 characters;
* its text is not a flowsheet TIME GRID (``flowsheet_grid.is_grid``): which
  row a value belongs to is read off the layout, and a text layer can print
  the rows in the other order;
* its text does not open with the vision pass's ``[handwritten]`` mark
  (``update/handwritten.is_handwritten``; a native page never should, so this
  only guards a mislabelled transcript);
* its text states no affirmative result ("positive", "reactive",
  "detected": ``audit/negation.states_positive``): a negation across a page
  break once turned a negative serology positive. ``negation.flag`` decides
  this per CLAIM; with no claim to hand, the page rule is the strict form.

Nothing here reads a page as text that the index cannot open: no page map,
no units file or an unreadable PDF and every page is a picture, exactly as
before this module existed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from . import flowsheet_grid as FG
from .audit import negation as NG
from .audit.page_text import PageIndex
from .update import handwritten as H

TEXT, IMAGE = "text", "image"
#: Characters a native text layer must carry for the page to be read as text.
TEXT_MIN_CHARS = 200


@dataclass(frozen=True)
class Mode:
    kind: str  # TEXT | IMAGE
    text: str | None  # the native text, for a TEXT page
    reason: str  # "native" for TEXT, else why the page is a picture


def decide(index: PageIndex | None, exhibit: int, page: int) -> Mode:
    """The one rule (module docstring) for one page of a current exhibit."""
    if index is None:
        return Mode(IMAGE, None, "no-index")
    try:
        cls, text = index.classify(exhibit, page, TEXT_MIN_CHARS)
    except Exception:  # noqa: BLE001 - a page the index cannot read is a picture, never a failed check
        return Mode(IMAGE, None, "unreadable")
    if cls != "native" or text is None:
        return Mode(IMAGE, None, cls)
    if FG.is_grid(FG.rows(text)):
        return Mode(IMAGE, None, "grid")
    if H.is_handwritten(text):
        return Mode(IMAGE, None, "handwritten")
    if NG.states_positive(text):
        return Mode(IMAGE, None, "result")
    return Mode(TEXT, text, "native")


class Modes:
    """``decide`` over one index, remembered per page (the review and the
    audit ask about one page many times). Thread-safe: the audit's workers
    share it."""

    def __init__(self, index: PageIndex | None) -> None:
        self.index = index
        self._seen: dict[tuple[int, int], Mode] = {}
        self._lock = threading.Lock()

    @classmethod
    def open(cls, slug_dir: Path, unit: str) -> "Modes":
        """Over the unit's page index; every page a picture when it cannot open."""
        try:
            return cls(PageIndex(slug_dir, unit, min_chars=TEXT_MIN_CHARS))
        except Exception:  # noqa: BLE001 - no page map, units file or text: every page is read as a picture
            return cls(None)

    def __call__(self, exhibit: int, page: int) -> Mode:
        k = (exhibit, page)
        with self._lock:
            got = self._seen.get(k)
        if got is None:
            got = decide(self.index, exhibit, page)
            with self._lock:
                self._seen[k] = got
        return got

    def close(self) -> None:
        if self.index is not None:
            self.index.close()
