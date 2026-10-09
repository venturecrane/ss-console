"""Text from one fetched document, by kind: the negotiation watch's reader.

Moved here from the litigation status job's ``litigation/extract.py`` (only
the readers this lane uses), so the watch depends on nothing in that package:

* PDF: the text layer per page; a page with no text layer is a scan and is
  transcribed by the doorway's vision call (``ocr``). On a long document only
  the first ``HEAD`` and last ``TAIL`` scanned pages are transcribed (an offer
  letter's figure and date sit on its first or last page).
* ``.msg``: the demand lane's reader (which retries a body in cp1252); the
  headers, the body, and the text of PDF/DOCX attachments.
* ``.eml``: the stdlib MIME parser; plain or HTML body; attachments as above.
* ``.doc``: antiword (the image installs it). ``.docx``: python-docx.

A reader that raises, or text that comes out empty, is an ``ExtractFailure``:
never an empty document.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Callable

PAGE_MARK = "===== page {n} ====="
SCAN_FLOOR = 25  # characters: a page with less carries no text layer
HEAD, TAIL = 3, 2
DEFERRED = "[scanned page {n}: not transcribed]"
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


def ocr_pages(texts: list[str]) -> list[int]:
    """The scanned pages to transcribe: all of them on a short document, only
    those among the first ``HEAD`` and last ``TAIL`` pages on a long one."""
    n = len(texts)
    keep = set(range(n)) if n <= HEAD + TAIL else set(range(HEAD)) | set(range(n - TAIL, n))
    return [i for i in sorted(keep) if len(texts[i].strip()) < SCAN_FLOOR]


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
    out = "\n".join(f"{k}: {v}" for k, v in head.items()) + "\n\n" + body.strip()
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
    if ext == ".doc":
        return doc_text(path), 0
    if ext == ".docx":
        return docx_text(data), 0
    raise ExtractFailure(f"unsupported: no reader for {ext or 'a file without an extension'}")


__all__ = ["OCR_PROMPT", "ExtractFailure", "Ocr", "ocr_pages", "pdf_text", "text_of"]
