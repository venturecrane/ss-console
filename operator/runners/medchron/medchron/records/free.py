"""Files whose page index is already complete: a free candidate for EVERY record.

WHY (2026-11-03, ``--measure`` on the A&P seat after the release). Record 5's
answer on 11-02 was the whole 35-page file 9830b227, whose index on the seat is
complete, so a match on it costs cents and reads no page. Its name is one word,
so by word score it never reached the top 3 (a 2,313-page set at $36 and two
3-page files did). A file the seat has already read whole is the cheapest
place to look for anything on its matter.

So after a record's scored candidates, every PDF on the matter whose page
index is COMPLETE (every page read or recorded unreadable) and whose index
names this matter is added as a free candidate, at most :data:`FREE_EXTRA` a
record, most pages first (a whole document wins over a 2-page scrap). They are
tried after the scored free files and before any priced one; each costs one
match; a not-found match on one is not saved, so it never shows in the reply.

Which matter file IS a given index: an index the records lane touched carries
the matter's file ids (``index["files"]``, written by :func:`note_file` when a
file is downloaded). An older index carries none, so the matter's other PDFs
are downloaded, smallest first, at most :data:`LEGACY_SCAN`, only while an
index naming this matter is still unmatched; each one found is noted, so the
next job needs no download for it.
"""

from __future__ import annotations

import json
from typing import Any

from smokeball_connector import page_finder as pf

#: Free candidates added to one record, at most.
FREE_EXTRA = 6
#: Matter PDFs downloaded to identify older indexes that name no file id.
LEGACY_SCAN = 25


def note_file(sha: str, file_id: str) -> None:
    """Record ``file_id`` in the index of ``sha``, when one with pages exists
    (an index of another model or view loads empty and is never touched)."""
    index = pf.load_index(sha)
    if not index["pages"]:
        return
    files = [str(f) for f in index.get("files") or []]
    if file_id not in files:
        index["files"] = [*files, file_id]
        pf.save_index(index)


def complete_indexes(matter_id: str) -> dict[str, dict[str, Any]]:
    """sha256 -> index, for every complete index in the index dir that names this matter."""
    out: dict[str, dict[str, Any]] = {}
    base = pf.index_dir()
    for path in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or matter_id.lower() not in [str(m).lower() for m in raw.get("matters") or []]:
            continue
        index = pf.load_index(str(raw.get("sha256") or path.stem))
        if index["pages"] and pf._covered(index, retry_refused=False):
            out[index["sha256"]] = index
    return out


def by_fit(rows: list[dict[str, Any]], indexes: list[dict[str, Any]], files: dict[str, Any]) -> list[dict[str, Any]]:
    """The legacy scan's fetch order. Smallest first would never reach the
    file: on the A&P matter the 35-page target is 6.6 MB among dozens of PDFs
    under 1 MB (2026-11-03). So the bytes per page of the files this job has
    already downloaded bound where an unmatched index's file can be: a PDF
    whose size lies in ``[pages * min * 0.5, pages * max * 2]`` for one of
    them is fetched first, closest to ``pages * median`` first; the rest after,
    smallest first."""
    bpp = sorted(int(m["size"]) / int(m["page_count"]) for m in files.values() if m.get("size") and m.get("page_count"))
    pages = [int(i.get("page_count") or 0) for i in indexes if int(i.get("page_count") or 0) > 0]

    def size(r: dict[str, Any]) -> int:
        return int(r.get("sizeBytes") or 0)

    def miss(r: dict[str, Any]) -> float | None:
        fits = [abs(size(r) - n * bpp[len(bpp) // 2]) for n in pages if n * bpp[0] * 0.5 <= size(r) <= n * bpp[-1] * 2]
        return min(fits) if fits else None

    if not bpp or not pages:
        return sorted(rows, key=size)
    fit = sorted((r for r in rows if miss(r) is not None), key=lambda r: (miss(r), size(r)))
    return fit + sorted((r for r in rows if miss(r) is None), key=size)


def tag(score: int) -> str:
    """``--measure``'s mark on a free extra (the only candidate scored 0)."""
    return " free (complete index)" if score == 0 else ""


def extras(run: Any, mid: str, rows: list[dict[str, Any]], files: dict[str, Any], taken: Any) -> list[tuple[str, int]]:
    """The record's free extras as ``(file id, 0)``: not already a candidate
    (``taken``), nothing left unread, most pages first, at most :data:`FREE_EXTRA`."""
    from . import search

    want = complete_indexes(mid)
    if not want:
        return []
    listed = [r for r in rows if str(r.get("fileExtension") or "").strip().lower().lstrip(".") == "pdf" and r.get("id")]
    ids = {str(r["id"]) for r in listed}
    found: dict[str, str] = {}
    for sha, index in want.items():
        for fid in index.get("files") or []:
            if str(fid) in ids and run._fetch(mid, str(fid), files) and files[str(fid)]["sha256"] == sha:
                found[sha] = str(fid)
    for fid, meta in files.items():
        if meta.get("sha256") in want:
            found.setdefault(meta["sha256"], fid)
    missing = set(want) - set(found)
    scan = by_fit([r for r in listed if str(r["id"]) not in files], [want[sha] for sha in missing], files)
    # Once a job: a later record must not download another LEGACY_SCAN files
    # for an index whose file is gone from the matter.
    if missing and not getattr(run, "_free_scanned", False):
        run._free_scanned = True
    else:
        scan = []
    for row in scan[:LEGACY_SCAN]:
        fid = str(row["id"])
        if run._fetch(mid, fid, files) and files[fid]["sha256"] in missing:
            found[files[fid]["sha256"]] = fid
            missing.discard(files[fid]["sha256"])
        if not missing:
            break
    picked = [fid for fid in found.values() if fid not in taken and search.unread(files[fid]) == 0]
    picked.sort(key=lambda fid: (-int(files[fid]["page_count"]), fid))
    return [(fid, 0) for fid in picked[:FREE_EXTRA]]


__all__ = ["FREE_EXTRA", "LEGACY_SCAN", "by_fit", "complete_indexes", "extras", "note_file", "tag"]
