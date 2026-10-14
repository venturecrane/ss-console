"""What a drafting job ends with: the coded reason, the two exceptions that
carry it (a hold for the file or the request, a failure for our machinery),
the verdict the lane reads, and the markers the reply lists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

WALL_SENTENCE = (
    "some documents in the file are held back from drafting by the privilege wall and reached the draft; "
    "the attorney should check them before this is drafted again"
)
MARKERS = re.compile(r"\{\{\s*(ATTORNEY|NOT IN RECORD|CLIENT)\s*(?::\s*([^{}]*))?\}\}")


#: Every reason the runner records starts with a short code and ": " (SMD's
#: request card shows the leading code; DELIVER relays the sentence after it
#: for a held job). Never an exception's own text first: it can start with a
#: document name.
REASON_CODES = (
    "request_incomplete",
    "gate_refused",
    "format_check",
    "record_unreadable",
    "audit_unsettled",
    # A served paper's audit finding survived its repairs (compose.STRICT_CLASSES): failed, nothing filed.
    "audit_unrepaired",
    # The attorney notes still carry the system's words, a hedge or an empty line after one rewrite (wording.py).
    "notes_unclean",
    "destination_mismatch",
    "destination_unauthored",
    "no_readable_documents",
    "filing_refused",
    "stage_unfinished",
    "limit",
    "no_verdict",
    "unexpected",
    "config_missing",
    "caption_restore_incomplete",
    # The pre-delivery review found a MUST-FIX (review.py): failed, nothing filed, SMD alerted.
    "review_held",
    # The read-back downloaded a filed file and its bytes are not ours (2026-10-13).
    "readback_mismatch",
)
REASON = re.compile(r"^(" + "|".join(REASON_CODES) + r"): ")


class DraftingHold(RuntimeError):
    """The file or the request needs a person: held, final, the firm is told."""


class DraftingFailed(RuntimeError):
    """Our machinery: failed, resumable, no client message, SMD alerted."""


_WHERE = re.compile(
    r"(?im)^\W*((?:SPECIAL INTERROGATORY|REQUEST FOR ADMISSION|REQUEST FOR PRODUCTION(?: OF DOCUMENTS)?)\s+NO\.?\s*\d+"
    r"|#{1,6}\s+[^\n]+)"
)


def markers_located(md: str) -> list[dict[str, str]]:
    """``markers`` with where each sits: the request label or the heading
    above it (2026-10-14: notes lines like "signature" or "compound under
    section 2030.060(f)?" named no document or request)."""
    seen, out = set(), []
    for m in MARKERS.finditer(md):
        prev = [w for w in _WHERE.finditer(md, 0, m.start())]
        where = " ".join(prev[-1].group(1).lstrip("#").split()).strip("*`: ") if prev else ""
        item = (m.group(1), " ".join((m.group(2) or "").split()), where)
        if item not in seen:
            seen.add(item)
            out.append({"kind": item[0], "text": item[1], "where": where})
    return out


def markers(md: str) -> list[dict[str, str]]:
    seen, out = set(), []
    for m in MARKERS.finditer(md):
        item = (m.group(1), " ".join((m.group(2) or "").split()))
        if item not in seen:
            seen.add(item)
            out.append({"kind": item[0], "text": item[1]})
    return out


@dataclass
class Verdict:
    outcome: str
    stage: str | None = None
    reason: str | None = None
    dollars: float = 0.0
    documents: int = 0
    pages: int = 0
    document_class: str | None = None
    folder_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    caption_discrepancies: list[dict[str, str]] = field(default_factory=list)
    #: Matter-record fields the job corrected from the court's own paper.
    caption_corrections: list[dict[str, str]] = field(default_factory=list)
    #: A correction that could not be fully put back: SMD's alarm.
    caption_restore_incomplete: list[str] = field(default_factory=list)
    markers: list[dict[str, str]] = field(default_factory=list)
    #: The daemon's proof block (``proof.gate``), on a delivered verdict only.
    proof: dict[str, Any] | None = None

    def to_list(self) -> list[dict[str, Any]]:
        d = {k: v for k, v in self.__dict__.items() if not (k == "proof" and v is None)}
        return [{**d, "unit": "drafting", "kind": "drafting"}]
