"""The match step's answer, turned into facts by code (page_finder.py, step 3).

Split out of page_finder.py (size ceiling). The model names section ids only;
everything a staff reply prints about a record (its pages, heading, date, and
the counts of pages read and not read) is composed here from the page index.

WHY THE FACTS ARE CODE'S (2026-11-02, an A&P records request). The Operator found the
right files for an attorney, but both replies it drafted were wrong: the model
retyped the finder's ranges and counts and dropped pp 48-49, wrote 59 for 57,
and the match step's "closest" sentence was thrown away whenever a page was
unread. So the result now carries every fact a reply needs as fields code
computed (``counts``, ``closest``, ``relatedCandidates``), versioned by
:data:`SCHEMA_VERSION`, and the overlay renders the reply's facts from them
(``shared/matter_file_facts.py``). The model's own sentences (``reason``) are
for the Operator to read, never for the reply.

THE MATCH CACHE. The match call is not deterministic: the same sections and
request gave a visit as 1-4 on one call and 1-5 on the next. A match is cached
in the file's index under :func:`match_key`, a hash of the model, the section
lines, the unreadable pages, the normalised request and
``page_finder_prompts.MATCH_VERSION``, and only once every page is indexed, so
the same question about the same pages gets the same answer, and a changed
index or prompt asks again.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .page_finder_prompts import MATCH_VERSION
from .page_finder_sections import _ranges, _view, section_lines

#: The result shape a reply's facts are built from. The overlay renders the
#: facts only from a result of exactly this version and holds the reply
#: otherwise, so a seat whose connector and overlay disagree never sends a
#: partial block.
SCHEMA_VERSION = 2
MAX_CACHED_MATCHES = 32
_MAX_POINTERS = 3
_ZERO = {"input_tokens": 0, "output_tokens": 0}


def zero_tokens() -> dict[str, int]:
    return dict(_ZERO)


def normalize_request(what: str) -> str:
    """The request as the cache sees it: case and spacing do not change the question."""
    return " ".join(str(what or "").lower().split())[:4000]


def match_key(model: str, secs: list[dict[str, Any]], unreadable: dict[str, str], what: str) -> str:
    canon = {
        "model": model,
        "sections": section_lines(secs),
        "unreadable": sorted(unreadable, key=int),
        "what": normalize_request(what),
        "matchVersion": MATCH_VERSION,
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode("utf-8")).hexdigest()


def cached_match(index: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    entry = (index.get("matches") or {}).get(key)
    if isinstance(entry, dict) and isinstance(entry.get("items"), list):
        return [i for i in entry["items"] if isinstance(i, dict)]
    return None


def remember_match(index: dict[str, Any], key: str, raw_items: list[dict[str, Any]]) -> None:
    matches = index.setdefault("matches", {})
    matches[key] = {"items": raw_items, "at": time.time()}
    by_age = sorted(matches, key=lambda k: float((matches[k] or {}).get("at") or 0))
    for old in by_age[:-MAX_CACHED_MATCHES]:
        matches.pop(old, None)


def _pointer(sec: dict[str, Any]) -> dict[str, Any]:
    """A section as a reply may name it: pages, heading and date, from the index only."""
    return {
        "section": sec["id"],
        "pages": f"{sec['first']}-{sec['last']}",
        "heading": sec.get("heading", ""),
        "date": sec.get("date", ""),
    }


def _ids(value: Any, by_id: dict[str, dict[str, Any]], skip: set[str]) -> list[str]:
    out: list[str] = []
    for raw in value if isinstance(value, list) else []:
        sid = str(raw).strip()
        if sid in by_id and sid not in skip and sid not in out:
            out.append(sid)
    return out[:_MAX_POINTERS]


def _covered(sec: dict[str, Any], spans: list[tuple[int, int]]) -> bool:
    return any(a <= sec["first"] and sec["last"] <= b for a, b in spans)


def compose(raw_items: list[dict[str, Any]], secs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per record asked for: found, PDF page ranges with code-composed evidence,
    and the closest and related sections as pointers. An id the index does not
    hold is dropped and counted; a related section already inside a found range
    is not repeated (it is attached)."""
    by_id = {sec["id"]: sec for sec in secs}
    items = []
    for item in raw_items:
        ids = [str(i).strip() for i in item.get("sections") or []]
        chosen = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        dropped = len([i for i in ids if i not in by_id])
        found = bool(chosen) and bool(item.get("found"))
        spans = _ranges(chosen) if found else []
        taken = {s["id"] for s in chosen} if found else set()
        closest = [] if found else _ids(item.get("closest"), by_id, set())
        related = [i for i in _ids(item.get("relatedCandidates"), by_id, taken) if not _covered(by_id[i], spans)]
        items.append(
            {
                "asked": str(item.get("asked") or "")[:300],
                "found": found,
                "ranges": [
                    {
                        "pages": f"{a}-{b}",
                        "evidence": [_view(s) for s in chosen if s["first"] <= b and s["last"] >= a],
                    }
                    for a, b in spans
                ],
                "reason": str(item.get("reason") or "")[:500],
                "closest": [_pointer(by_id[i]) for i in closest],
                "relatedCandidates": [_pointer(by_id[i]) for i in related],
                **({"droppedSections": dropped} if dropped else {}),
            }
        )
    return items


def counts(total: int, read: int, unreadable: int) -> dict[str, int]:
    """Every page of the file is exactly one of read, unreadable, or not reached."""
    return {"pages": total, "read": read, "unreadable": unreadable, "notReached": total - read - unreadable}


def qualify(items: list[dict[str, Any]], unreadable: dict[str, str], remaining: int) -> None:
    """Say per item whether its answer is ESTABLISHED.

    A "not found" read over pages that were never read (declined, too large,
    or not reached yet) is not a finding: the record could be on those pages.
    So it is ``established: false`` with the pages named, and the reason says
    so first; it is never a flat "none". A found range that has unreadable
    pages inside it is sent whole (they stay in their section) and is flagged.

    The wording is lowercase: copied into a staff reply, capitals read as
    shouting and the reply checklist holds them (2026-11-02)."""
    blind = ", ".join(sorted(unreadable, key=int))
    for item in items:
        inside = sorted(
            {p for r in item["ranges"] for e in r["evidence"] for p in e.get("unreadablePages") or {}}, key=int
        )
        if item["found"]:
            item["established"] = True
            if inside:
                item["unreadableInside"] = inside
            continue
        gaps = []
        if unreadable:
            gaps.append(f"pages {blind} could not be read")
        if remaining:
            gaps.append(f"{remaining} pages are not read yet")
        item["established"] = not gaps
        if gaps:
            # The model's own sentence is dropped from ``reason``, not prefixed:
            # written from the pages it saw, it can read "there is no X" when
            # the pages it did not see may hold X. It is kept under an internal
            # key the tool never returns (page_finder_tools.find_pages).
            item["_modelReason"] = item.get("reason", "")
            item["notEstablishedBecause"] = "; ".join(gaps)
            item["reason"] = (
                "not established: not found on the pages read, but "
                + "; ".join(gaps)
                + ". It may be on those pages; this is not a finding that it is absent."
            )


__all__ = [
    "MAX_CACHED_MATCHES",
    "SCHEMA_VERSION",
    "cached_match",
    "compose",
    "counts",
    "match_key",
    "normalize_request",
    "qualify",
    "remember_match",
]
