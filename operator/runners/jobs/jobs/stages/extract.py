"""`extract`: local text extraction over the pulled `raw/` set. $0.

Per file: pypdf text with `[p.N]` page markers (pleading paper's margin line
numbers removed: ``pleading_margin``), docx via python-docx. A PDF
whose text layer is absent, is glyph indices, or is a constant-offset cipher
goes to the scan queue for the vision pass, and so does a text-layer PDF
holding image pages its text layer does not carry (``vision_pages``: those
pages only, decided per page); the two detectors and their
thresholds are calibrated on delivered runs (see the docstrings), and both
failure shapes produced a chronology that looked complete while omitting or
understating a record, which is worse than omitting the file.

Output (the shapes every later stage reads):
  extracted.jsonl   one record per file: {id, name, folder, ext, pages, chars,
                    text_path | scan: true}
  text/<id>.txt     the text with [p.N] markers
  scan_queue.json   files needing the vision pass
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..matter import pdf as matter_pdf, readable, word
from ..matter.readable import (  # this stage's rules (``matter/readable``), under the names it always used
    DRAWN_CURVES as DRAWN_CURVES,
    DRAWN_TEXT_CHARS as DRAWN_TEXT_CHARS,
    ENGLISH_FLOOR as ENGLISH_FLOOR,
    FAR_YEAR as FAR_YEAR,
    GARBLE_MIXED as GARBLE_MIXED,
    GARBLE_NONWORD as GARBLE_NONWORD,
    GLYPH_RUN as GLYPH_RUN,
    GLYPH_THRESHOLD as GLYPH_THRESHOLD,
    ICD10_FROM_YEAR as ICD10_FROM_YEAR,
    THIN_CHARS as THIN_CHARS,
    drawn_text as drawn_text,
    garbled as garbled,
    glyph_curves as glyph_curves,
    glyph_junk as glyph_junk,
    not_english as not_english,
    thin as thin,
    vision_pages as vision_pages,
)
from ..medchron.update import medlegal as ML
from .base import StageRun, read_jsonl
from .pleading_margin import strip_page

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
WORD_EXTS = {".docx", ".doc"}

_text_layer_absent = readable.text_layer_absent
_ocr_tells = readable.ocr_tells


def pdf_text(path: Path) -> list[tuple[int, str]]:
    """The chronology's text layer (pypdf, ``matter/pdf``), pleading paper's
    margin line numbers removed: they are not its words."""
    return [(i, strip_page(t)) for i, t in matter_pdf.pypdf_layers(path)]


#: A Word file's text in this stage's declared form (``matter/word``).
docx_text = word.record_docx_text


def live_rows(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    """Last write wins (retries append); a byte-duplicate row retires its id."""
    ok: dict[str, dict[str, Any]] = {}
    dupes = 0
    for r in rows:
        if r.get("ok"):
            if r.get("duplicate_of"):
                dupes += 1
                ok.pop(r["id"], None)
                continue
            ok[r["id"]] = r
    return ok, dupes


def _extract_one(d: Path, r: dict[str, Any], scans: list[dict[str, Any]]) -> dict[str, Any]:
    ext = (r.get("ext") or "").lower()
    path = Path(r["path"])
    rec: dict[str, Any] = {"id": r["id"], "name": r["name"], "folder": r["folder"], "ext": ext}
    text_path = d / "text" / f"{r['id']}.txt"
    try:
        if ext == ".pdf":
            pages = pdf_text(path)
            total = sum(len(t.strip()) for _, t in pages)
            rec["pages"] = len(pages)
            txt = "\n".join(f"[p.{n}]\n{t}" for n, t in pages)
            junk = glyph_junk(txt) or not_english(txt)
            if _text_layer_absent(total, len(pages)) or junk:
                rec["scan"] = True
                if junk:
                    rec["glyph_junk"] = True
                    # A stale text file from a previous extract satisfies the
                    # vision pass's resume check, so routing to vision means
                    # removing the text that routed it there.
                    text_path.unlink(missing_ok=True)
                scans.append(rec)
            else:
                text_path.write_text(txt, encoding="utf-8")
                rec["chars"] = len(txt)
                rec["text_path"] = str(text_path)
                imaged = vision_pages(path, pages)
                empty = [n for n, t in pages if not t.strip() and n not in imaged]
                if empty:
                    rec["empty_pages"] = empty
                if imaged:
                    # The vision pass transcribes these pages only and merges
                    # them into this text file (``stages/vision.py``).
                    rec["vision_pages"] = imaged
                    scans.append(rec)
        elif ext in WORD_EXTS:
            try:
                txt = docx_text(path)
            except Exception as exc:  # noqa: BLE001 - docx extraction failing is recorded as that document's error with empty text; the stage continues
                rec["error"] = f"docx: {exc}"[:120]
                txt = ""
            if txt:
                text_path.write_text(txt, encoding="utf-8")
                rec["chars"] = len(txt)
                rec["text_path"] = str(text_path)
            else:
                rec.setdefault("error", "no text")
        elif ext in IMAGE_EXTS:
            rec["scan"] = True
            rec["image"] = True
            scans.append(rec)
        else:
            rec["error"] = "unhandled ext"
    except Exception as exc:  # noqa: BLE001 - one broken file is one row
        rec["error"] = str(exc)[:200]
    return rec


def first_page_only(rec: dict[str, Any]) -> bool:
    """On an update, a medical-legal report named as one (``update/
    medlegal``) is reviewed and never itemized, so vision reads its first page
    only: enough to classify it, and no spend on pages nothing will compose.
    False when nothing of it is left for vision (a text-layer file whose
    first page already has text)."""
    if rec.get("image") or not ML.by_name(str(rec.get("name") or "")):
        return True
    if rec.get("scan"):
        rec["vision_pages"] = [1]
        return True
    rec["vision_pages"] = [p for p in rec.get("vision_pages") or [] if p == 1]
    return bool(rec["vision_pages"])


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    (d / "text").mkdir(parents=True, exist_ok=True)
    ok, dupes = live_rows(read_jsonl(d / "raw_manifest.jsonl"))
    scans: list[dict[str, Any]] = []
    with (d / "extracted.jsonl").open("w", encoding="utf-8") as out:
        for i, r in enumerate(ok.values(), 1):
            out.write(json.dumps(_extract_one(d, r, scans)) + "\n")
            if i % 40 == 0:
                sr.log(f"{i}/{len(ok)}")
    if sr.job.update is not None:
        scans = [rec for rec in scans if first_page_only(rec)]
    (d / "scan_queue.json").write_text(json.dumps(scans, indent=1), encoding="utf-8")
    # A new scan queue is never read against an older picture record: only an
    # update's `update_pages`, which runs after this, writes the skip list.
    (d / "vision_skip.json").unlink(missing_ok=True)
    sr.log(f"DONE {len(ok)} files, {len(scans)} to scan queue, {dupes} byte-duplicates skipped")
    return 0
