"""Exhibit page -> the native text layer behind it, when there is one.

The audit rasterises every cited page and sends the picture. For a page whose
PDF text layer was written by the system that produced the record the text
says the same thing at a fraction of the image tokens, shared across claims
through the prompt cache. For every other page the text layer is a guess
somebody else made about the picture, and the audit must keep looking at the
picture. So the join is deliberately narrow: a page is TEXT-ELIGIBLE only when
its source file passed the extract gates, the page is not in empty_pages,
pymupdf sees no invisible-text spans and no OCR-marker font, no form-field
widgets and no checkbox glyphs, the text-file page and the PDF page agree on
content (guards the join itself), and the page carries at least min_chars.

Page numbers are the ones a citation uses: the CURRENT exhibit PDF on disk.
The strip stage may have removed pages after page_map.json was written, so
the index inverts the recorded drops before it walks the map.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_MARK = re.compile(r"^\[p\.(\d+)\]([^\n]*)$", re.M)
_CHECKBOX = re.compile(r"[☐☑☒✓✔✗✘□■▢▣]")
_OCR_FONT = re.compile(r"glyphless|tesseract|ocr|abbyy|invisible", re.I)
_DINGBAT_FONT = re.compile(r"wingdings|dingbat|webdings", re.I)
EXHIBIT_PDF = re.compile(r"Exhibit (\d+) - .*\.pdf$")
CLASSES = ("native", "vision", "form", "ocr", "empty", "short", "join-mismatch", "unmapped", "no-text")


def parse_segments(text: str) -> dict[int, tuple[str, bool]]:
    """{page: (text, machine_transcribed)} from a text/<id>.txt body."""
    out = {}
    marks = list(_MARK.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[int(m.group(1))] = (text[m.end() : end].strip("\n"), "machine transcription" in m.group(2))
    return out


def _shingles(text: str, n: int = 8) -> set[str]:
    s = re.sub(r"\s+", "", (text or "").lower())
    return {s[i : i + n] for i in range(0, max(len(s) - n + 1, 0))}


def content_agrees(a: str, b: str, floor: float = 0.5) -> bool:
    """Character shingles with whitespace removed, not words: one extractor
    glues words across line breaks where the other keeps the spaces."""
    x, y = _shingles(a), _shingles(b)
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= floor


def exhibit_paths(out_dir: Path) -> dict[int, Path]:
    paths = {}
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            m = EXHIBIT_PDF.match(p.name)
            if m:
                paths[int(m.group(1))] = p
    return paths


def page_traits(page: Any) -> dict[str, Any]:
    """Per-page facts from pymupdf; spans carry their own render mode and
    font, which is what makes this per-page on a merged exhibit."""
    invisible = ocr_font = checkbox = False
    for span in page.get_texttrace():
        if span.get("type") == 3:
            invisible = True
        font = span.get("font") or ""
        if _OCR_FONT.search(font):
            ocr_font = True
        if _DINGBAT_FONT.search(font):
            checkbox = True
    widgets = sum(1 for _ in page.widgets())
    text = page.get_text()
    if _CHECKBOX.search(text):
        checkbox = True
    return {
        "invisible": invisible,
        "ocr_font": ocr_font,
        "widgets": widgets,
        "checkbox": checkbox,
        "chars": len(text),
        "text": text,
    }


class PageIndex:
    def __init__(self, slug_dir: Path, unit: str, min_chars: int = 80) -> None:
        self.slug_dir, self.unit, self.min_chars = slug_dir, unit, min_chars
        self.out = slug_dir / "out" / unit
        self.page_map = json.loads((self.out / "page_map.json").read_text(encoding="utf-8"))
        unit_files = json.loads((slug_dir / "units" / f"{unit}.json").read_text(encoding="utf-8"))
        self.by_name = {f["name"]: f for f in unit_files}
        self.by_id = {f["id"]: f for f in unit_files}
        self.extracted: dict[str, dict[str, Any]] = {}
        ep = slug_dir / "extracted.jsonl"
        if ep.is_file():
            for line in ep.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.extracted[r["id"]] = r
        self.drops = self._load_drops()
        self.pdf_paths = exhibit_paths(self.out)
        self.text_dir = slug_dir / "text"
        self._segments: dict[str, dict[int, tuple[str, bool]]] = {}
        self._docs: dict[int, Any] = {}
        self._traits: dict[tuple[int, int], dict[str, Any] | None] = {}
        self._lock = threading.RLock()

    def _load_drops(self) -> dict[int, set[int]]:
        for name in (f"strip_result-{self.unit}.json", "strip_result.json"):
            p = self.slug_dir / name
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8")).get("drops", {})
                return {int(k): set(v) for k, v in raw.items()}
        return {}

    def _stripped(self, exhibit: int) -> bool:
        p = self.pdf_paths.get(exhibit)
        return bool(self.drops.get(exhibit)) and p is not None and Path(str(p) + ".orig").exists()

    def _map_total(self, exhibit: int) -> int:
        return next((e["total_pages"] for e in self.page_map if e["exhibit"] == exhibit), 0)

    def original_page(self, exhibit: int, page: int) -> int | None:
        if not self._stripped(exhibit):
            return page
        dropped = self.drops[exhibit]
        n = 0
        for orig in range(1, self._map_total(exhibit) + 1):
            if orig in dropped:
                continue
            n += 1
            if n == page:
                return orig
        return None

    def current_page(self, exhibit: int, orig: int) -> int | None:
        if not self._stripped(exhibit):
            return orig
        dropped = self.drops[exhibit]
        if orig in dropped:
            return None
        return orig - sum(1 for p in dropped if p < orig)

    def npages(self, exhibit: int) -> int:
        t = self._map_total(exhibit)
        return t - len(self.drops[exhibit]) if self._stripped(exhibit) else t

    def resolve(self, exhibit: int, page: int) -> tuple[str, int] | None:
        """(file_id, source_page) for a current exhibit page, or None."""
        orig = self.original_page(exhibit, page)
        if orig is None:
            return None
        for e in self.page_map:
            if e["exhibit"] != exhibit:
                continue
            for f in e["files"]:
                if "start_page" not in f:
                    continue
                s, n = f["start_page"], f["pages"]
                if s <= orig < s + n:
                    rec = self.by_name.get(f["file"]) or self.by_name.get(Path(f["file"]).stem)
                    return (rec["id"], orig - s + 1) if rec else None
        return None

    def segments(self, file_id: str) -> dict[int, tuple[str, bool]]:
        with self._lock:
            if file_id in self._segments:
                return self._segments[file_id]
            rec = self.by_id.get(file_id) or self.extracted.get(file_id) or {}
            path = self.text_dir / f"{file_id}.txt"
            if not path.is_file() and rec.get("text_path"):
                path = Path(rec["text_path"])
            segs = parse_segments(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else {}
            self._segments[file_id] = segs
            return segs

    def _doc(self, exhibit: int) -> Any:
        import pymupdf

        if exhibit not in self._docs:
            self._docs[exhibit] = pymupdf.open(str(self.pdf_paths[exhibit]))
        return self._docs[exhibit]

    def pdf_traits(self, exhibit: int, page: int) -> dict[str, Any] | None:
        key = (exhibit, page)
        with self._lock:
            if key in self._traits:
                return self._traits[key]
            doc = self._doc(exhibit)
            t = page_traits(doc[page - 1]) if 1 <= page <= len(doc) else None
            self._traits[key] = t
            return t

    def classify(self, exhibit: int, page: int, min_chars: int | None = None) -> tuple[str, str | None]:
        """(page_class, text|None). 'native' is the only text-eligible class."""
        min_chars = self.min_chars if min_chars is None else min_chars
        hit = self.resolve(exhibit, page)
        if hit is None:
            return "unmapped", None
        file_id, src_page = hit
        rec = self.extracted.get(file_id) or {}
        if rec.get("scan") or rec.get("glyph_junk") or rec.get("not_english"):
            return "vision", None
        if src_page in set(rec.get("empty_pages") or []):
            return "empty", None
        seg = self.segments(file_id).get(src_page)
        if seg is None:
            return "no-text", None
        text, machine = seg
        if machine:
            return "vision", None
        traits = self.pdf_traits(exhibit, page)
        if traits is None:
            return "unmapped", None
        if traits["widgets"] or traits["checkbox"]:
            return "form", None
        if traits["invisible"] or traits["ocr_font"]:
            return "ocr", None
        if len(text.strip()) < min_chars:
            return "short", None
        if not content_agrees(text, traits["text"]):
            return "join-mismatch", None
        return "native", text

    def page_text(self, exhibit: int, page: int, min_chars: int | None = None) -> str | None:
        return self.classify(exhibit, page, min_chars)[1]

    def eligible(self, exhibit: int, page: int) -> bool:
        return self.classify(exhibit, page)[0] == "native"

    def window_text(self, exhibit: int, pages: list[int]) -> str:
        parts = []
        for p in sorted(set(pages)):
            t = self.page_text(exhibit, p)
            if t is not None:
                parts.append(f"===== Exhibit {exhibit} p.{p} =====\n{t.strip()}\n")
        return "\n".join(parts)

    def close(self) -> None:
        for d in self._docs.values():
            try:
                d.close()
            except Exception:  # noqa: BLE001 - closing one document handle failing must not stop the other handles from closing
                pass
        self._docs.clear()
