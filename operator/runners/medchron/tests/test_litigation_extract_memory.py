"""Extract's memory is bounded by ONE document, never by how many were read.

2026-10-13, A&P litigation job 01M4V2G6 (228 matters, 4808 documents) was
OOM-killed in its 1 GiB cgroup at document 2525. MuPDF's process-wide store
of decoded images outlived every ``doc.close()`` and pymupdf's warning list
was never trimmed, so the footprint climbed with documents read. These tests
fail on that code: the RSS one measured +120 MB of peak growth over 24
documents before the fix and +0 MB after, on the same input (macOS, pymupdf
1.28.2). A 2000-document synthetic run: +269 MB before, +13 MB after.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pymupdf

from medchron.litigation import extract

# A scanned page whose image DECODES big (2400 x 3000 RGB is about 21 MB) but
# deflates to almost nothing on disk, so the fixture is small and fast while
# every render puts a large decoded object in MuPDF's store.
SIDE_W, SIDE_H = 2400, 3000


def _scan_pdf(path: Path, shade: int) -> None:
    doc = pymupdf.open()
    pg = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, SIDE_W, SIDE_H), False)
    pix.set_rect(pix.irect, (shade, 255 - shade, 128))
    pg.insert_image(pg.rect, pixmap=pix)
    doc.save(path, deflate=True)
    doc.close()


CHILD = textwrap.dedent(
    """
    import json, resource, sys
    from pathlib import Path
    from medchron.litigation import extract

    src, work = Path(sys.argv[1]), Path(sys.argv[2])
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss: bytes on macOS, KiB on Linux
    peak = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    rows = lambda lo, hi: [{"id": f"d{i}", "ok": True, "path": str(src), "ext": ".pdf"} for i in range(lo, hi)]
    ocr = lambda png: "transcribed page text " * 20
    extract.extract_matter(work / "m0", rows(0, 3), ocr, concurrency=3)  # warm: imports, first decode
    warm = peak()
    for m in range(1, 9):  # every row reopens the file: a new document each time, as on the seat
        extract.extract_matter(work / f"m{m}", rows(m * 3, m * 3 + 3), ocr, concurrency=3)
    print(json.dumps({"warm": warm, "end": peak()}))
    """
)


def test_extract_memory_does_not_grow_with_documents_read(tmp_path):
    src = tmp_path / "scan.pdf"
    _scan_pdf(src, 40)
    out = subprocess.run(  # noqa: S603 - this interpreter, a script we wrote
        [sys.executable, "-c", CHILD, str(src), str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path(extract.__file__).parents[2])},  # the code under test
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    r = json.loads(out.stdout.strip().splitlines()[-1])
    growth = (r["end"] - r["warm"]) / 2**20
    # 24 more documents of ~21 MB decoded each: retained, that is hundreds of MB
    # (MuPDF's store caps near 256 MB). Bounded by one document, it is ~0.
    assert growth < 60, f"extract peak grew {growth:.0f} MB over 24 documents; memory must not scale with documents"


def test_no_mupdf_state_survives_a_document(tmp_path):
    """The structural half: after a document, pymupdf's warning list is empty
    (it was appended to forever) and a broken PDF leaves nothing behind."""
    bad = tmp_path / "bad.pdf"
    good = tmp_path / "good.pdf"
    _scan_pdf(good, 10)
    raw = good.read_bytes()
    bad.write_bytes(raw.replace(b"xref", b"xrex", 1))  # MuPDF repairs it, and warns while it does
    for i, p in enumerate([bad, good, bad]):
        rec = extract.extract_file(p, ".pdf", tmp_path / f"{i}.txt", lambda png: "page text " * 10)
        assert rec["ok"], rec
        assert pymupdf.TOOLS.mupdf_warnings() == "", "pymupdf's warning list must be emptied after every document"


def test_no_pdf_is_open_while_vision_reads(tmp_path, monkeypatch):
    """The page images go to vision after the document is closed, so a slow
    vision call never pins an open PDF (times concurrency)."""
    src = tmp_path / "s.pdf"
    _scan_pdf(src, 90)
    opened, closed = [], []
    real_open = extract._open_pdf

    def spy(srcp):
        doc = real_open(srcp)
        opened.append(doc)
        orig_close = doc.close
        doc.close = lambda: (closed.append(doc), orig_close())[1]
        return doc

    monkeypatch.setattr(extract, "_open_pdf", spy)

    def ocr(png: bytes) -> str:
        assert len(closed) == len(opened), "a PDF was still open during a vision call"
        assert not extract._MUPDF.locked(), "the MuPDF lock was held across a vision call"
        return "page text " * 10

    assert extract.extract_file(src, ".pdf", tmp_path / "s.txt", ocr)["ok"]
    assert opened and len(closed) == len(opened)


def test_in_flight_documents_are_bounded_by_concurrency(tmp_path, monkeypatch):
    """pool.map submitted every document at once; now at most ``concurrency``
    are in flight, and each record is written the moment its document ends."""
    import threading
    import time

    lock, live, peak = threading.Lock(), [0], [0]

    def fake(path, ext, out, ocr):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.01)
        with lock:
            live[0] -= 1
        return {"ok": True, "chars": 1, "scanned_pages": 0, "text_path": str(out)}

    submitted = []
    real_submit = __import__("concurrent.futures").futures.ThreadPoolExecutor.submit

    def counting_submit(self, fn, *a, **k):
        submitted.append(1)
        return real_submit(self, fn, *a, **k)

    monkeypatch.setattr(extract, "extract_file", fake)
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor.submit", counting_submit)
    seen_before_all_submitted = []
    real_append = extract.append_jsonl

    def append(path, rec):
        seen_before_all_submitted.append(len(submitted) < 40)
        real_append(path, rec)

    monkeypatch.setattr(extract, "append_jsonl", append)
    rows = [{"id": f"d{i}", "ok": True, "path": str(tmp_path / "x"), "ext": ".pdf"} for i in range(40)]
    out = extract.extract_matter(tmp_path, rows, None, concurrency=3)
    assert [r["file_id"] for r in out] == [f"d{i}" for i in range(40)]
    assert peak[0] <= 3
    assert any(seen_before_all_submitted), "records must be written as documents finish, not after all are queued"
