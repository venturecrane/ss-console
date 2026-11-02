"""The sections of a PDF and the pages each spans, decided by code from the page index.

Split out of page_finder.py (size ceiling); no model call lives here. Every page
boundary a staff reply rests on is computed in this module from what the index
says each page shows, never taken from a model's answer.
"""

from __future__ import annotations

from typing import Any

from .page_finder_prompts import _SECTION_FIELDS

#: A page the model could not read: kept in the section around it, never a start.
_UNREAD: dict[str, Any] = {"continues_previous": True, "sections": [], "dates": [], "legible": False}


def sections(pages: dict[str, dict[str, Any]], unreadable: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The file's sections and the PDF pages each spans, decided by CODE from the index.

    A section runs from the page it starts on through every following page until
    the next section starts. The page where the next one starts still belongs to
    the earlier section when its top continues it (``continues_previous``): a new
    visit beginning mid-page shares that page with the note it follows. Several
    sections starting on one page all include it. A continuation page with no
    known start (an unindexed gap before it) opens an untitled section, so no
    page is ever silently attached to a section it may not belong to.

    A page the model could not read (``unreadable``: declined, too large) is
    kept INSIDE the section it falls in, so a range never silently skips it,
    and is listed on that section as ``unreadablePages`` with its reason."""
    unreadable = unreadable or {}
    merged = {**{k: _UNREAD for k in unreadable}, **pages}
    out: list[dict[str, Any]] = []
    last_page = 0
    for key in sorted(merged, key=int):
        n, row = int(key), merged[key]
        starts = row.get("sections") or []
        adjacent = bool(out) and last_page == n - 1
        if adjacent and (not starts or row.get("continues_previous")):
            for sec in out:
                if sec["last"] == n - 1 and not sec.get("closed"):
                    sec["last"] = n
        elif not starts:
            out.append(
                {
                    "first": n,
                    "last": n,
                    "heading": "(untitled: starts before the indexed pages)",
                    "document_type": "",
                    "date": "",
                    "provider": "",
                }
            )
        for start in starts:
            # A section that started earlier on this same page ends here.
            out.append({"first": n, "last": n, **{k: start.get(k, "") for k in _SECTION_FIELDS}})
        if starts:
            # Only the LAST section starting on a page can run on past it: an
            # earlier one that started or continued onto this page ends here.
            for sec in out[:-1]:
                sec["closed"] = True
        last_page = n
    for index, sec in enumerate(out, start=1):
        sec["id"] = f"S{index}"
        seen: list[str] = []
        for page in range(sec["first"], sec["last"] + 1):
            for d in pages.get(str(page), {}).get("dates") or []:
                if d not in seen:
                    seen.append(d)
        sec["dates"] = seen[:8]
        sec.pop("closed", None)
        blind = [p for p in range(sec["first"], sec["last"] + 1) if str(p) in unreadable and str(p) not in pages]
        if blind:
            sec["unreadablePages"] = {str(p): unreadable[str(p)] for p in blind}
    return out


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
    }


def documents(
    pages: dict[str, dict[str, Any]], unreadable: dict[str, str] | None = None, limit: int = 120
) -> list[dict[str, Any]]:
    """The file's sections, as the outline a reply can cite."""
    return [_view(sec) for sec in sections(pages, unreadable)[:limit]]


__all__ = ["documents", "section_lines", "sections"]
