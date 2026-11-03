"""The sections of a PDF and the pages each spans, decided by code from the page index.

Split out of page_finder.py (size ceiling); no model call lives here. Every page
boundary a staff reply rests on is computed in this module from what the index
says each page shows, never taken from a model's answer.
"""

from __future__ import annotations

import re
from typing import Any

from .page_finder_prompts import _SECTION_FIELDS

#: A running header that says the page continues the note it names.
_CONTINUED_RE = re.compile(r"\bcont(?:inued|inuation|'?d|\.)?(?:\b|$)", re.IGNORECASE)


def sections(pages: dict[str, dict[str, Any]], unreadable: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The file's sections and the PDF pages each spans, decided by CODE from the index.

    A section runs from the page it starts on through every following page until
    the next section starts. The page where the next one starts still belongs to
    the earlier section when its top continues it (``continues_previous``): a new
    visit beginning mid-page shares that page with the note it follows. Several
    sections starting on one page all include it. A continuation page with no
    known start (an unindexed gap before it) opens an untitled section, so no
    page is ever silently attached to a section it may not belong to.

    A page the model could not read (``unreadable``: declined, too large) never
    extends a section by itself: nothing is known about it. When the next READ
    page continues the section (its top says so), the section bridges back over
    the unread pages, and they are listed on it as ``unreadablePages`` with
    their reason, so a range never silently skips them. When the next read page
    does not continue it (a new start), the section closes before the unread
    pages, which then belong to no section (2026-11-02: a visit whose third page
    was unread came back 1-4 on one call and 1-5 on the next). A page that
    continues the note bridges the unread run before it even when it also
    starts a section mid-page, and then shares that page with the new section
    (the seat replay: refusing the bridge there sent a visit as 1-2 with its
    unread third page left out, unflagged). A page that only starts, and does
    not continue, never bridges.

    A BLANK page (:func:`_blank`) belongs to no section and never extends one,
    and nothing bridges over it (2026-11-02 replay: a speech note ran 48-51
    through a blank page 50 and onto a growth chart; the key is 48-49)."""
    unreadable = unreadable or {}
    out: list[dict[str, Any]] = []
    last_read = 0
    for key in sorted(pages, key=int):
        n = int(key)
        row = pages[key]
        if _blank(row):
            continue
        gap = range(last_read + 1, n)
        bridged = (
            bool(out)
            and len(gap) > 0
            and all(str(p) in unreadable for p in gap)
            and bool(row.get("continues_previous"))
        )
        adjacent = bool(out) and (last_read == n - 1 or bridged)
        _step(out, n, row, adjacent=adjacent, prev=last_read)
        last_read = n
    for index, sec in enumerate(out, start=1):
        _finish(sec, index, out, pages, unreadable)
    return out


#: Why an indexed page whose words the model could not read is listed inside its section.
ILLEGIBLE = "the page was read but nothing on it could be made out"

_UNTITLED = {"heading": "(untitled: starts before the indexed pages)", "document_type": "", "date": "", "provider": ""}


def _step(out: list[dict[str, Any]], n: int, row: dict[str, Any], *, adjacent: bool, prev: int) -> None:
    """Apply one page of the index to the sections built so far. ``prev`` is the
    last READ page before ``n``: ``n - 1``, or the page before an unread run
    that ``n`` bridges."""
    starts = row.get("sections") or []
    if adjacent and (not starts or row.get("continues_previous")):
        for sec in out:
            if sec["last"] == prev and not sec.get("closed"):
                sec["last"] = n
    elif not starts:
        out.append({"first": n, "last": n, **_UNTITLED})
    # The open notes this page's own "(continued)" label NAMES: only those stay
    # open past what starts here. A record-level label ("<record> Clinical Notes
    # (continued)") names no note, so a new start there closes the note before it
    # (2026-11-02 replay: a 5/15 note ran 2-5 over a 3/20 entry starting on p4).
    named = (
        [sec for sec in out if sec["last"] == n and not sec.get("closed") and _label_names(row, sec)]
        if adjacent and starts and _continues_running_note(row)
        else []
    )
    nested = bool(named)
    for start in starts:
        # A section that started earlier on this same page ends here.
        out.append({"first": n, "last": n, "embedded": nested, **{k: start.get(k, "") for k in _SECTION_FIELDS}})
    if not starts:
        return
    # Only the LAST section starting on a page can run on past it: an earlier
    # one that started or continued onto this page ends here. EXCEPT on a page
    # whose header carries the running note's label "(continued)": what starts
    # there (a flowsheet, an embedded report) sits INSIDE that note, so the note
    # stays open and runs on (2026-11-02: a 35-page discharge summary came back
    # as its first 4 pages).
    keep = {id(sec) for sec in named if not sec.get("embedded")}
    for sec in out[:-1]:
        if id(sec) not in keep:
            sec["closed"] = True


def _finish(
    sec: dict[str, Any], index: int, out: list[dict[str, Any]], pages: dict[str, Any], unreadable: dict[str, str]
) -> None:
    """Id, the dates printed in it, its unreadable pages, the note it sits in."""
    sec["id"] = f"S{index}"
    seen: list[str] = []
    for page in range(sec["first"], sec["last"] + 1):
        for d in pages.get(str(page), {}).get("dates") or []:
            if d not in seen:
                seen.append(d)
    sec["dates"] = seen[:8]
    sec.pop("closed", None)
    blind: dict[str, str] = {}
    for p in range(sec["first"], sec["last"] + 1):
        if str(p) in unreadable and str(p) not in pages:
            blind[str(p)] = unreadable[str(p)]
        elif pages.get(str(p), {}).get("legible") is False:
            # Indexed, but the model could read nothing on it (handwriting, a
            # faint scan): inside the section, and said so.
            blind[str(p)] = ILLEGIBLE
    if blind:
        sec["unreadablePages"] = blind
    if sec.pop("embedded", False):
        outer = [o["id"] for o in out[: index - 1] if o["first"] < sec["first"] and o["last"] >= sec["last"]]
        if outer:
            sec["insideOf"] = outer[-1]


def section_lines(secs: list[dict[str, Any]]) -> str:
    out = []
    for sec in secs:
        cells = [sec.get(k) or "-" for k in _SECTION_FIELDS]
        pages_txt = f"pp. {sec['first']}-{sec['last']}"
        dates = "; ".join(sec.get("dates") or []) or "-"
        blind = ", ".join(sec.get("unreadablePages") or {})
        extra = [f"unreadable pages: {blind}"] if blind else []
        out.append(" | ".join([sec["id"], pages_txt, *cells, f"dates printed: {dates}", *extra]))
    return "\n".join(out)


def _ranges(chosen: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """The pages of the chosen sections, merged into contiguous PDF ranges."""
    pages = sorted({n for sec in chosen for n in range(sec["first"], sec["last"] + 1)})
    spans: list[list[int]] = []
    for n in pages:
        if spans and spans[-1][1] >= n - 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return [(a, b) for a, b in spans]


def _view(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "section": sec["id"],
        "pages": f"{sec['first']}-{sec['last']}",
        **{k: sec.get(k, "") for k in _SECTION_FIELDS},
        "dates": sec.get("dates") or [],
        **({"unreadablePages": sec["unreadablePages"]} if sec.get("unreadablePages") else {}),
        **({"insideOf": sec["insideOf"]} if sec.get("insideOf") else {}),
    }


def _blank(row: dict[str, Any]) -> bool:
    """Nothing is on the page: no section starts, no date, no banner, label or
    page marker, and it does not continue anything. Only such a page breaks a
    section. A page the model could not read (``legible: false``) that
    continues the note is NOT blank: it stays inside the note and is listed as
    unreadable there, because ending a note at a handwritten page would send a
    short record (Captain's call via the coordinator, 2026-11-02)."""
    if row.get("sections") or row.get("dates") or row.get("continues_previous"):
        return False
    return not any(str(row.get(k) or "").strip() for k in ("running_header", "note_label", "page_of"))


def _words(text: str) -> str:
    """Lowercase words only, the continuation marker taken out."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _CONTINUED_RE.sub(" ", str(text or "")).lower()).split())


def _label_names(row: dict[str, Any], sec: dict[str, Any]) -> bool:
    """Does the page's continuation label name THIS note (its heading or type)?"""
    core = _words(str(row.get("note_label") or "") or str(row.get("running_header") or ""))
    if not core:
        return False
    for field in ("heading", "document_type"):
        name = _words(sec.get(field) or "")
        if name and (name in core or core in name):
            return True
    return False


def _continues_running_note(row: dict[str, Any]) -> bool:
    """The page continues an earlier note: its OWN label line says "(continued)".

    The note's label (``note_label``), not the encounter banner above it: a
    banner repeats on every page of a stay and never says which note runs on
    (2026-11-02: a summary inside an encounter was cut at its first embedded
    report). Only an index without the field falls back to the banner."""
    if not row.get("continues_previous"):
        return False
    label = str(row.get("note_label") or "")
    return bool(_CONTINUED_RE.search(label or str(row.get("running_header") or "")))


def documents(
    pages: dict[str, dict[str, Any]], unreadable: dict[str, str] | None = None, limit: int = 120
) -> list[dict[str, Any]]:
    """The file's sections, as the outline a reply can cite."""
    return [_view(sec) for sec in sections(pages, unreadable)[:limit]]


__all__ = ["documents", "section_lines", "sections"]
