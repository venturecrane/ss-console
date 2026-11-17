"""An other-person finding on a scan's OCR text layer is proven on the page.

WHY (2026-11-17). The identity rule withholds a whole record when its text
names someone other than the client. On two updates in one day it withheld a
record of the client's own because the hidden OCR layer of a scanned page
misread one digit of the client's date of birth: the page images print the
right date. ``update_select._page_findings`` already re-reads a text-layer hit
on the page's vision transcript, but only when vision transcribed that page,
and a scan whose OCR layer is readable sends vision none of its pages.

THE RULE. Before the vision pass (``update_pages`` calls ``queue``), every new
document's text-layer pages are checked with the rule's own test
(``foreign_identity.findings``). A page that has a finding AND whose text is
an OCR layer over an image (the page carries an image, and its text is
invisible or set in an OCR engine's font: ``audit.page_text.page_traits``) is
added to that document's ``vision_pages``. The finding is then judged on the
page's own transcription, as any transcribed page is, and withholds only if
it survives. A born-digital text page is never sent: its text is the page.

BOUND. Only hit pages, never a whole file: at the lane's transcription rate
per page. Nothing is read less than before; a page already queued or
pixel-equal to one already read (``vision_skip.json``) is not added.

RECORD. ``update/identity_page_reads.json`` lists each page sent; after
``update_select`` judges them, a document cleared by its page read is named in
``update/identity_cleared.json`` and the log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..medchron.gates import foreign_identity as FI
from .base import StageRun, read_json, read_jsonl
from .transcript import split_pages

READS = "identity_page_reads.json"
CLEARED = "identity_cleared.json"


def ocr_over_image(path: Path, pages: list[int]) -> set[int]:
    """Which of `pages` (1-based) carry an image under an OCR text layer."""
    import pymupdf

    from ..medchron.audit.page_text import page_traits

    out: set[int] = set()
    with pymupdf.open(str(path)) as doc:
        for n in pages:
            if 1 <= n <= len(doc) and doc[n - 1].get_images():
                t = page_traits(doc[n - 1])
                if t["invisible"] or t["ocr_font"]:
                    out.add(n)
    return out


def hit_pages(sr: StageRun, rid: str) -> list[int]:
    """The text-layer pages of `rid` on which the identity rule finds another person."""
    tp = sr.slug_dir / "text" / f"{rid}.txt"
    if not tp.is_file():
        return []
    doc = tp.read_text(encoding="utf-8", errors="replace")
    surnames, dobs = FI.identities(sr.job.units)
    firsts = FI.first_names(sr.job.units)
    return sorted(
        n
        for n, (suffix, text) in split_pages(doc).items()
        if "machine transcription" not in suffix and FI.findings(text, surnames, dobs, firsts, doc)
    )


def queue(sr: StageRun, rows: list[dict[str, Any]], ud: Path) -> list[dict[str, Any]]:
    """Add each OCR-layer page with an identity finding to the vision queue.
    Returns the pages sent ({id, page}); written to ``update/identity_page_reads.json``."""
    qpath = sr.slug_dir / "scan_queue.json"
    q: list[dict[str, Any]] = read_json(qpath, []) or []
    by_id = {str(r.get("id")): r for r in q}
    skips = read_json(sr.slug_dir / "vision_skip.json", {}) or {}
    free = FI.released(sr.slug_dir)
    extracted = {str(x.get("id")): x for x in read_jsonl(sr.slug_dir / "extracted.jsonl") if x.get("id")}
    sent: list[dict[str, Any]] = []
    for r in rows:
        rid = str(r.get("id"))
        if FI.matches(rid, free) or not str(r.get("path") or "").lower().endswith(".pdf"):
            continue
        rec = by_id.get(rid)
        if rec is not None and (rec.get("scan") or rec.get("image")):
            continue  # vision reads every page of it already
        hits = hit_pages(sr, rid)
        if not hits:
            continue
        have = set(int(p) for p in (rec or {}).get("vision_pages") or [])
        skip = {int(p) for p in skips.get(rid, [])}
        add = sorted(ocr_over_image(Path(str(r["path"])), [p for p in hits if p not in have | skip]))
        if not add:
            continue
        if rec is None:  # the document's own extract row, as extract queues an image page
            rec = dict(extracted.get(rid) or {k: r[k] for k in ("id", "name", "ext") if k in r})
            q.append(rec)
            by_id[rid] = rec
        rec["vision_pages"] = sorted(have | set(add))
        sent += [{"id": rid, "page": p} for p in add]
    if sent:
        qpath.write_text(json.dumps(q, indent=1), encoding="utf-8")
        sr.log(
            f"update_pages: {len(sent)} OCR-layer page(s) with an other-person finding sent to vision "
            "to be read on the page before the rule judges them"
        )
    (ud / READS).write_text(json.dumps(sent, indent=1), encoding="utf-8")
    return sent


def record_cleared(sr: StageRun, ud: Path, held_ids: set[str]) -> list[str]:
    """The documents a page read cleared: a page was sent for its finding and
    the rule, judging the transcription, does not withhold the document."""
    sent = read_json(ud / READS, []) or []
    cleared = sorted({str(x["id"]) for x in sent if isinstance(x, dict) and str(x.get("id")) not in held_ids})
    (ud / CLEARED).write_text(json.dumps(cleared, indent=1), encoding="utf-8")
    if cleared:
        sr.log(
            f"{len(cleared)} document(s) cleared by the page read: the OCR layer's other-person finding "
            f"did not survive the page's transcription (document ids: {cleared[:5]})"
        )
    return cleared


__all__ = ["CLEARED", "READS", "hit_pages", "ocr_over_image", "queue", "record_cleared"]
