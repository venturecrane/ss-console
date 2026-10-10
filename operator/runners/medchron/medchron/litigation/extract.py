"""`extract`: text from every fetched file, by kind, page by page.

The 2026-10-07 hand run lost facts at this layer four different ways, so each
kind has its own reader and nothing falls through silently:

* PDF: the text layer per page; a page with no text layer is a scan and goes
  to vision (``ocr``, the doorway's read model). Checkbox facts are never
  taken from this text; pass 3 looks at the page image (``page_image``).
* ``.msg``: demand's reader, which retries a body in cp1252 (the encoding that
  silently dropped a settlement email on 2026-10-07); headers, body, and the
  text of PDF/DOCX attachments.
* ``.eml``: the stdlib MIME parser; plain or HTML body; attachments as above.
* ``.htm``/``.html``: BeautifulSoup text. ``.rtf``: RTFDE for encapsulated
  RTF, else striprtf when installed, else a small control-word stripper.
  ``.doc``: antiword when it is on PATH. ``.docx``: python-docx. ``.txt``.
* Anything else, a reader that raises, or text that comes out EMPTY is an
  extraction FAILURE record. A failure is never absence: the gates refuse a
  negative finding on a matter until the unreadable file is named on it.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable

from ..stages.base import append_jsonl, read_jsonl

PAGE_MARK = "===== page {n} ====="
SCAN_FLOOR = 25  # characters: a page with less carries no text layer
HEAD, TAIL = 3, 2
DEFERRED = "[scanned page {n}: not transcribed; call view_page with this doc and page {n} to see it]"
MIN_TEXT = 1
OCR_PROMPT = (
    "Transcribe every word on this page exactly as printed, top to bottom, including stamps, handwriting you can "
    "read, and form labels. For a checkbox write [X] when it is marked and [ ] when it is empty. Output the text only."
)
Ocr = Callable[[bytes], str]


class ExtractFailure(RuntimeError):
    def __init__(self, problem: str) -> None:
        self.problem = problem
        super().__init__(problem)


def _pages(texts: list[str]) -> str:
    return "\n".join(f"{PAGE_MARK.format(n=i)}\n{t.strip()}" for i, t in enumerate(texts, 1))


def render_png(pdf_bytes: bytes, page: int, long_side: int = 1600) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pg = doc[page - 1]
        zoom = long_side / (max(pg.rect.width, pg.rect.height) or 1.0)
        return pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    finally:
        doc.close()


def pdf_text(data: bytes, ocr: Ocr | None) -> tuple[str, int]:
    """(paged text, scanned pages sent to vision)."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - a PDF that does not open is one failure record
        raise ExtractFailure(f"pdf_unreadable: {type(exc).__name__}") from None
    try:
        texts = [pg.get_text() or "" for pg in doc]
    finally:
        doc.close()
    scanned, picks = 0, ocr_pages(texts)
    for i in picks:
        if ocr is None:
            raise ExtractFailure("scan_without_vision: a page has no text layer and no vision reader was given")
        texts[i] = ocr(render_png(data, i + 1))
        scanned += 1
    for i, t in enumerate(texts):
        if len(t.strip()) < SCAN_FLOOR and i not in picks:
            texts[i] = DEFERRED.format(n=i + 1)
    return _pages(texts), scanned


def ocr_pages(texts: list[str]) -> list[int]:
    """The scanned pages transcribed at extract: every one on a short
    document; on a long one only those among its first ``HEAD`` and last
    ``TAIL`` pages (the caption, the filing stamp, the signature and the
    proof of service). The rest are read on demand: ``view_page``."""
    n = len(texts)
    keep = set(range(n)) if n <= HEAD + TAIL else set(range(HEAD)) | set(range(n - TAIL, n))
    return [i for i in sorted(keep) if len(texts[i].strip()) < SCAN_FLOOR and not texts[i].startswith("[scanned page")]


def docx_text(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def _attachment_text(name: str, blob: bytes, ocr: Ocr | None) -> str:
    low = name.lower()
    try:
        if low.endswith(".pdf"):
            return pdf_text(blob, ocr)[0]
        if low.endswith(".docx"):
            return docx_text(blob)
    except ExtractFailure as exc:
        return f"[attachment could not be read: {exc.problem.split(':')[0]}]"
    return ""


def _email(head: dict[str, str], body: str, atts: list[tuple[str, bytes]], ocr: Ocr | None) -> str:
    lines = [f"{k}: {v}" for k, v in head.items()]
    out = "\n".join(lines) + "\n\n" + body.strip()
    for name, blob in atts:
        out += f"\n\n[ATTACHMENT] {name}"
        text = _attachment_text(name, blob, ocr)
        if text.strip():
            out += "\n" + text
    return out


def msg_text(path: Path, ocr: Ocr | None) -> str:
    from ..demand.pull import _open_msg

    m = _open_msg(path)
    if m is None:
        raise ExtractFailure("msg_unreadable: the Outlook container did not open in either encoding")
    head = {"From": m["sender"], "To": ", ".join(m["recipients"]), "Date": m["date"], "Subject": m["subject"]}
    return _email(head, m["body"], m["attachments"], ocr)


def html_text(raw: bytes | str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def eml_text(data: bytes, ocr: Ocr | None) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = html_text(content) if body_part.get_content_type() == "text/html" else str(content)
    atts = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            atts.append((part.get_filename() or "attachment", payload))
    head = {k: str(msg.get(k) or "") for k in ("From", "To", "Date", "Subject")}
    return _email(head, body, atts, ocr)


_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "listtable", "listoverridetable"}
_RTF_TOKEN = re.compile(
    r"\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|([^\\{}\r\n]+)", re.I
)


def strip_rtf(text: str) -> str:
    """Plain text from RTF control words: groups, \\par, \\'hh, \\uN, and the
    destinations that carry no body text (font and colour tables, pictures)."""
    out: list[str] = []
    stack: list[bool] = []
    skip = False
    for m in _RTF_TOKEN.finditer(text):
        word, arg, hexc, sym, brace, plain = m.groups()
        if brace == "{":
            stack.append(skip)
        elif brace == "}":
            skip = stack.pop() if stack else False
        elif skip:
            continue
        elif word:
            w = word.lower()
            if w in _RTF_SKIP:
                skip = True
            elif w in ("par", "line", "row"):
                out.append("\n")
            elif w == "tab" or w == "cell":
                out.append("\t")
            elif w == "u" and arg:
                out.append(chr(int(arg) % 65536))
        elif hexc:
            out.append(bytes([int(hexc, 16)]).decode("cp1252", errors="replace"))
        elif sym == "*":
            skip = True
        elif sym in ("\\", "{", "}"):
            out.append(sym)
        elif plain:
            out.append(plain)
    return "".join(out)


def rtf_text(data: bytes) -> str:
    raw = data.decode("latin-1")
    try:
        from RTFDE.deencapsulate import DeEncapsulator

        d = DeEncapsulator(data)
        d.deencapsulate()
        if d.content_type == "html":
            return html_text(d.html)
        return str(d.text or "")
    except Exception:  # noqa: BLE001 - not encapsulated RTF (the common case): the plain readers below
        pass
    try:
        from striprtf.striprtf import rtf_to_text  # type: ignore[import-not-found]

        return str(rtf_to_text(raw))
    except ImportError:
        return strip_rtf(raw)


def doc_text(path: Path) -> str:
    exe = shutil.which("antiword")
    if not exe:
        raise ExtractFailure("doc_unreadable: antiword is not installed on this seat")
    r = subprocess.run([exe, str(path)], capture_output=True, text=True, timeout=120, check=False)  # noqa: S603 - fixed binary, a path we wrote
    if r.returncode != 0:
        raise ExtractFailure("doc_unreadable: antiword could not read the file")
    return r.stdout


def text_of(path: Path, ext: str, ocr: Ocr | None) -> tuple[str, int]:
    """(text, scanned pages); raises ExtractFailure."""
    ext = ext.lower()
    data = path.read_bytes()
    if not data:
        raise ExtractFailure("empty_file: the file has no bytes")
    if ext == ".pdf":
        return pdf_text(data, ocr)
    if ext == ".msg":
        return msg_text(path, ocr), 0
    if ext == ".eml":
        return eml_text(data, ocr), 0
    if ext in (".htm", ".html"):
        return html_text(data), 0
    if ext == ".rtf":
        return rtf_text(data), 0
    if ext == ".doc":
        return doc_text(path), 0
    if ext == ".docx":
        return docx_text(data), 0
    if ext == ".txt":
        return data.decode("utf-8", errors="replace"), 0
    if ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff") and ocr is not None:
        return _pages([ocr(data)]), 1
    raise ExtractFailure(f"unsupported: no reader for {ext or 'a file without an extension'}")


def extract_file(path: Path, ext: str, out: Path, ocr: Ocr | None) -> dict[str, Any]:
    """One record; on success the text is at ``out``."""
    try:
        text, scanned = text_of(path, ext, ocr)
    except ExtractFailure as exc:
        return {"ok": False, "problem": exc.problem}
    except Exception as exc:  # noqa: BLE001 - a reader crash is a failure record, never an absence
        return {"ok": False, "problem": f"reader_error: {type(exc).__name__}"}
    body = re.sub(r"===== page \d+ =====", "", text)
    if len(body.strip()) < MIN_TEXT:
        return {"ok": False, "problem": "empty_text: the reader produced no text"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return {"ok": True, "chars": len(text), "scanned_pages": scanned, "text_path": str(out)}


def extract_matter(
    mdir: Path, rows: list[dict[str, Any]], ocr: Ocr | None, progress: Any = None, concurrency: int = 1
) -> list[dict[str, Any]]:
    """Every ok pulled row of one matter; resumable through extracted.jsonl.
    Documents are read ``concurrency`` at a time (each one's vision calls in
    turn), records written by this thread only. ``progress`` (a
    ``progress.Progress``) is stepped once per document."""
    from concurrent.futures import ThreadPoolExecutor

    log = mdir / "extracted.jsonl"
    done = {r["file_id"]: r for r in read_jsonl(log)}
    todo = [r for r in rows if str(r["id"]) not in done and r.get("ok") and r.get("path")]
    for r in rows:
        if str(r["id"]) in done and progress is not None:
            progress.step()

    def one(r: dict[str, Any]) -> dict[str, Any]:
        fid = str(r["id"])
        rec = extract_file(Path(r["path"]), r.get("ext") or "", mdir / "txt" / f"{fid}.txt", ocr)
        return {"file_id": fid, "name": r.get("name"), **rec}

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for rec in pool.map(one, todo):
            append_jsonl(log, rec)
            done[rec["file_id"]] = rec
            if progress is not None:
                if not rec.get("ok"):
                    progress.add("failures")
                progress.step()
    return [done[str(r["id"])] for r in rows if str(r["id"]) in done]


def failures(mdir: Path) -> list[dict[str, Any]]:
    return [r for r in read_jsonl(mdir / "extracted.jsonl") if not r.get("ok")]


def write_summary(data: Path, summary: dict[str, Any]) -> None:
    (data / "extract.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
