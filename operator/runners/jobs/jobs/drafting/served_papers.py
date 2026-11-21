"""What a paper in the matter file shows about itself, by code (the ``find`` stage's half).

Split from ``served_find`` (2026-11-20, the second $0 replay on three real
matters). Each rule answers a question the finder asked wrongly there:

* :func:`was_served`: does a response paper show it was SERVED? Only a served
  response answers a set. SMD's own drafts awaiting attorney review and the
  firm's unserved drafts are Word response papers too (8 across the three
  matters), and counting them ended all three ``served_none``.
* :func:`draft_name` and :func:`draft_of`: a file named as a draft never
  answers, and a draft of a set still owed makes the job a revision.
* :func:`form_signature`: two copies of one served FORM set (an emailed copy
  beside the filed one) carry no text that tells them apart from two different
  forms; the $0 pixel read's checked numbers and form revision do.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .served import SET_TYPES

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = rf"(?:(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"
#: A proof of service heading, as a caption prints it.
_PROOF = re.compile(r"(?m)^[ \t|]*PROOF\s+OF\s+(?:ELECTRONIC\s+)?SERVICE\b")
#: "Dated: March 3, 2026" or "served on 3/3/2026", with a date.
_DATED = re.compile(rf"(?i)\b(?:dated|served)\b[^\n]{{0,40}}?{_DATE}")
#: A signature block after the date: "/s/", "By:", or "Attorneys for".
_SIGNED = re.compile(r"(?i)/s/|^[ \t|]*By:|\bAttorneys?\s+for\b", re.M)
_DRAFT_NAME = re.compile(r"(?i)^\s*DRAFT\b|for\s+attorney\s+review")
_DRAFT_OF = re.compile(r"(?i)\bDRAFT\b.*?\bResponses?\s+to\s+(?P<kind>.+?),\s+Set\s+(?P<n>\w+)")
_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}


def draft_name(name: str) -> bool:
    """A file NAMED as a draft ("DRAFT ...", "... for attorney review ...")."""
    return bool(_DRAFT_NAME.search(name or ""))


def was_served(name: str, text: str) -> bool:
    """A response paper that shows it was served: a proof of service, or a dated
    ("Dated"/"served" with a date) signature block. A draft by name never is."""
    if draft_name(name):
        return False
    if _PROOF.search(text or ""):
        return True
    dated = _DATED.search(text or "")
    return bool(dated and _SIGNED.search(text, dated.end()))


def _kind(words: str) -> str | None:
    """The set type a "Responses to <...>" phrase names ("Defendant's Special
    Interrogatories" reads as special interrogatories)."""
    low = " ".join(words.lower().split())
    return next((k for k, (label, _r) in SET_TYPES.items() if low.endswith(label.lower())), None)


def set_number(word: str) -> int | None:
    w = word.strip().lower()
    return int(w) if w.isdigit() else _WORDS.get(w)


def draft_of(name: str) -> tuple[str, int] | None:
    """(set type, set number) a file named "DRAFT ... Responses to <kind>, Set <n>" drafts."""
    m = _DRAFT_OF.search(name or "")
    if not m:
        return None
    kind, n = _kind(m.group("kind")), set_number(m.group("n"))
    return (kind, n) if kind and n else None


def form_signature(path: str, read: Callable[[Path], Any] | None = None) -> str:
    """The pixel read's checked numbers and form revision, as one string; ""
    when the file is not on disk or the read fails (then nothing folds)."""
    if not path or not Path(path).is_file():
        return ""
    if read is None:
        from .forms import read_stage

        read = read_stage.pixel_read
    try:
        got = read(Path(path))
    except Exception:  # noqa: BLE001 - a form the pixel read cannot read is simply not folded; the model read decides
        return ""
    checked = got.get("checked") if isinstance(got, dict) else None
    rev = got.get("form_revision") if isinstance(got, dict) else None
    if not isinstance(checked, list) or not checked or not rev:
        return ""
    return f"pixel:{rev}:" + ",".join(sorted(str(x) for x in checked))
