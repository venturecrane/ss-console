"""The matter's record that ``render_docx_draft`` checks a draft against.

Moved out of ``server.py`` (2026-11-24) with two changes, both for the render
that ran into the 300 s tool timeout twice (``draft_budget``):

* **It stops at its deadline.** Before each document it checks the stage
  deadline and raises ``OutOfTime``, so a matter too large to read in one call
  ends the call with a refusal instead of running past the tool timeout.
* **What it read is kept for the next call, in memory.** A drafting turn calls
  the render more than once (a refusal, a redraft, a retry), and every call
  re-downloaded and re-extracted every document on the matter. A document read
  once is kept, keyed by matter, file id and the vendor's ``versionId``, so a
  new version of the file is a new key and is read again. A file whose listing
  carries no ``versionId`` is never kept. A result that refuses the draft (a
  scan not yet read, a file that would not extract) is never kept either: it
  must clear the moment ``read_document`` reads the scan, and that does not
  change the version. A machine transcription (``vision_cached``) is never
  kept: its source is the on-disk vision cache, which can be purged, and a
  purge must be reflected on the next render, not at the next restart. What
  is kept is mechanically extracted matter text, in process memory only,
  bounded by ``MEMO_MAX_CHARS`` (a running total); it goes when the connector
  process restarts, is written nowhere, and is listed in CLAUDE.md "Gone means
  gone".
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from . import draft_budget

#: Total extracted characters kept across matters; least recently used first out.
MEMO_MAX_CHARS = 24_000_000

SOURCE = "source"
VISION = "vision"
SKIP = "skip"

Key = tuple[str, str, str]


class _Memo:
    """Least-recently-used readings, bounded by a running character total."""

    def __init__(self) -> None:
        self.rows: OrderedDict[Key, tuple[str, str]] = OrderedDict()
        self.chars = 0
        self.lock = threading.Lock()

    def get(self, key: Key | None) -> tuple[str, str] | None:
        if key is None:
            return None
        with self.lock:
            hit = self.rows.get(key)
            if hit is not None:
                self.rows.move_to_end(key)
            return hit

    def put(self, key: Key | None, kind: str, text: str) -> None:
        if key is None or len(text) > MEMO_MAX_CHARS:
            return
        with self.lock:
            old = self.rows.pop(key, None)
            self.chars -= len(old[1]) if old else 0
            self.rows[key] = (kind, text)
            self.chars += len(text)
            while self.chars > MEMO_MAX_CHARS:
                _k, (_kind, dropped) = self.rows.popitem(last=False)
                self.chars -= len(dropped)

    def clear(self) -> None:
        with self.lock:
            self.rows.clear()
            self.chars = 0


_MEMO = _Memo()


def _memo_key(matter_id: str, entry: dict[str, Any]) -> Key | None:
    version = str(entry.get("versionId") or "")
    file_id = str(entry.get("id") or "")
    return (matter_id, file_id, version) if version and file_id else None


def forget() -> None:
    """Drop every kept reading (tests)."""
    _MEMO.clear()


def _read_one(client: Any, matter_id: str, entry: dict[str, Any], name: str) -> tuple[str, str]:
    """``(kind, text)`` for one document, or ``("unextractable", label)``."""
    from .extract import MECHANICAL_METHODS, METHOD_MIXED, METHOD_VISION_CACHED, METHOD_XLSX_OPERATOR, extract_text_ex

    key = _memo_key(matter_id, entry)
    kept = _MEMO.get(key)
    if kept is not None:
        return kept
    try:
        _meta, blob = client.download_file(matter_id, entry["id"])
        result = extract_text_ex(
            blob,
            file_name=name,
            file_extension=str(entry.get("fileExtension") or ""),
            allow_vision=False,  # cache-read-only; see collect()
        )
    except draft_budget.OutOfTime:
        raise
    except Exception:  # noqa: BLE001 — one unreadable document refuses the whole check
        return "unextractable", name
    # Branch on the METHOD, never on whether the text is truthy: which road
    # the text came from is what decides how it may be used.
    if result.method == METHOD_XLSX_OPERATOR:
        outcome = (SKIP, "")  # the Operator's own workbook is not the firm's record
    elif result.method == METHOD_MIXED:
        # Typed pages read, scanned pages not (none stored, and this path never
        # transcribes): partial in exactly the way an uncached scan is, so it
        # is reported the same way, with the pages named, and the draft
        # refuses. Once ``read_document`` has read those pages they are stored,
        # and the file comes back ``vision_cached``, a transcription source.
        return "unextractable", f"{name} ({result.unread_note()})"
    elif result.method == METHOD_VISION_CACHED:
        return VISION, result.text  # never kept: the vision cache can be purged under it
    elif result.method in MECHANICAL_METHODS and result.text.strip():
        outcome = (SOURCE, result.text)
    else:
        return "unextractable", name
    _MEMO.put(key, *outcome)
    return outcome


def collect(client: Any, matter_id: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Every readable document on the matter as ``(name, extracted_text)``, the
    ones whose text is a MACHINE TRANSCRIPTION of a scan, and the names of the
    ones that would not extract.

    The third list is not diagnostics. A source that did not extract makes a
    correctly quoted passage look fabricated to gate 2a, so the caller REFUSES
    on it rather than checking against a partial record.

    THIS PATH NEVER INITIATES A TRANSCRIPTION (ss#2464). It reads the vision
    cache, a transcription somebody already paid for through ``read_document``,
    and nothing more. Billing: this runs over every document on the matter on
    every render. Discipline: a scanned matter's draft path opens only after a
    person deliberately read each scan, so an uncached scan still lands in
    ``unextractable`` and still hard-refuses the draft."""
    from .library import find_folder_id, is_library_file, load_library_config

    listing = client.get(f"/matters/{matter_id}/documents/files", Limit=500, Offset=0)
    entries = listing.get("value") if isinstance(listing, dict) else listing
    # Templates are not record. A firm's letterhead template (header-only, so
    # it extracts to nothing) or a .dotx filed in the Document Library would
    # otherwise land in ``unextractable`` and refuse every draft on the matter.
    lib_cfg = load_library_config()
    lib_folder = find_folder_id(client, matter_id, lib_cfg.folder_name) if lib_cfg.folder_name else None
    sources: list[tuple[str, str]] = []
    vision_sources: list[tuple[str, str]] = []
    unextractable: list[str] = []
    for entry in entries or []:
        if is_library_file(entry, lib_cfg, lib_folder):
            continue
        draft_budget.check("reading the matter's documents")
        name = str(entry.get("name") or entry.get("id") or "document")
        kind, text = _read_one(client, matter_id, entry, name)
        if kind == SOURCE:
            sources.append((name, text))
        elif kind == VISION:
            vision_sources.append((name, text))
        elif kind != SKIP:
            unextractable.append(text)
    return sources, vision_sources, unextractable


__all__ = ["MEMO_MAX_CHARS", "collect", "forget"]
