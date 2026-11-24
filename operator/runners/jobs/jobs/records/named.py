"""A records request that NAMES a file gets the whole file, exactly as the
matter holds it (2026-11-24): a request naming one 17-page PDF went out as the
4 pages the page finder matched. Code decides (``rank.names_whole``), never a
model."""

from __future__ import annotations

import re
from typing import Any

from . import rank, search
from .outcome import RecordsHold

#: An active PDF key outside stream data (the broker's wall,
#: workspace_broker/agentmail_attachments.py). A named file carrying one cannot
#: go exactly as the matter holds it, so it holds for SMD.
_ACTIVE = re.compile(
    rb"/(?:JS|JavaScript|AA|Launch|EmbeddedFile|EmbeddedFiles|RichMedia|XFA|SubmitForm|ImportData|GoToR|GoToE)"
    rb"(?=[\s()<>\[\]{}/%]|$)"
)
_STREAM = re.compile(rb"stream\r?\n.*?endstream", re.DOTALL)


def as_stored(raw: bytes) -> bool:
    """May a PDF go exactly as the matter holds it (its sha256 the matter
    file's): a PDF, not encrypted, no active key outside stream data."""
    return raw.startswith(b"%PDF-") and b"/Encrypt" not in raw and not _ACTIVE.search(_STREAM.sub(b"", raw))


def named_file(run: Any, mid: str, rec: dict[str, Any], files: dict[str, Any]) -> str | None:
    """The one PDF the record NAMES, fetched, or None. Code decides, never a
    model (``rank.names_whole``): every word of the file's name, or of its
    folder's name when that folder holds this one PDF, is in the record's
    words, and the record asks for nothing narrower (no date, no page
    range). 2026-11-24: a request naming one 17-page PDF went out as the 4
    pages the finder matched, its sha256 not the matter file's, and SMD's
    review rejected the reply."""
    from smokeball_connector import matter_files as mf

    if rec.get("every"):
        return None  # every document of a kind is never one named file
    try:
        rows, _complete = mf._all_files(run.client, mid)
    except Exception as exc:  # could not look is never "no file is named for it"
        raise RecordsHold("finder_failed: the matter's files could not be listed") from exc
    pdfs = [r for r in rows if rank.is_pdf(r) and r.get("id")]
    hits = {str(r["id"]) for r in pdfs if rank.names_whole(rec, r.get("name"))}
    if not hits:
        try:
            folders = mf._folder_paths(run.client, mid)
        except Exception:  # noqa: BLE001 - unreadable folders name no file; the finder path runs
            folders = {}
        by_folder: dict[str, list[str]] = {}
        for r in pdfs:
            path = folders.get(mf._folder_id(r) or "", "")
            by_folder.setdefault(path.rsplit("/", 1)[-1], []).append(str(r["id"]))
        hits = {ids[0] for label, ids in by_folder.items() if len(ids) == 1 and rank.names_whole(rec, label)}
    if len(hits) != 1:
        return None
    (fid,) = hits
    return fid if run._fetch(mid, fid, files) else None


def keep_whole(run: Any, n: int, fid: str, mid: str, rec: dict[str, Any], meta: dict[str, Any]) -> None:
    """The named file's result in the finder's shape: found, every page, so
    the cut takes the whole file and the facts say "the whole file"."""
    total = int(meta["page_count"])
    result = {
        "schemaVersion": 2,
        "pageCount": total,
        "counts": {"pages": total, "read": 0, "unreadable": 0, "notReached": 0},
        "namedFile": True,
        "items": [
            {
                "asked": rec["asked"],
                "found": True,
                "established": True,
                "ranges": [{"pages": f"1-{total}" if total > 1 else "1"}],
            }
        ],
    }
    if not run.locate_path(n, fid).is_file():
        search._keep(run, n, fid, mid, meta, result, save=True)


__all__ = ["as_stored", "keep_whole", "named_file"]
