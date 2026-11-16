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

import functools
from email import policy
from email.parser import BytesParser
from pathlib import Path

from ..matter import paid, pdf as matter_pdf, policy as matter_policy, readable
from ..text_rules import (
    OCR_PROMPT,
    PASSWORD,
    ExtractFailure,
    Ocr,
    doc_text,
    docx_text,
    html_text,
    paged as _pages,
)

#: This lane's reading rules (``matter/policy``): the floor alone, the 3 + 2
#: scanned pages transcribed, and its own marker for one left unread.
POLICY = matter_policy.NEGOTIATION
HEAD, TAIL = int(POLICY.head or 0), int(POLICY.tail or 0)
DEFERRED = str(POLICY.deferred)


def render_png(pdf_bytes: bytes, page: int, long_side: int = matter_policy.COURT_LONG_SIDE) -> bytes:
    return matter_pdf.render_png(pdf_bytes, page, long_side)


#: The scanned pages to transcribe: all of them on a short document, only those
#: among the first ``HEAD`` and last ``TAIL`` pages on a long one.
ocr_pages = functools.partial(readable.negotiation_ocr_pages, floor=int(POLICY.floor or 0), head=HEAD, tail=TAIL)


def pdf_text(data: bytes, ocr: Ocr | None) -> tuple[str, int]:
    """(paged text, scanned pages sent to vision)."""
    texts, _picks, images = matter_pdf.court_layers(data, ocr_pages, vision=ocr is not None)
    deferred = readable.plan(texts, POLICY).deferred
    sha = paid.content_sha(data)
    scanned = 0
    for i, png in images:
        assert ocr is not None
        with paid.page(sha, i + 1, POLICY.render):
            texts[i] = ocr(png)
        scanned += 1
    for i in deferred:
        texts[i] = DEFERRED.format(n=i + 1)
    return _pages(texts), scanned


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
    from ..demand.pull import _open_msg, head_parties

    m = _open_msg(path)
    if m is None:
        raise ExtractFailure("msg_unreadable: the Outlook container did not open in either encoding")
    sender, to = head_parties(m)
    head = {"From": sender, "To": to, "Date": m["date"], "Subject": m["subject"]}
    return _email(head, m["body"], m["attachments"], ocr)


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


__all__ = ["OCR_PROMPT", "PASSWORD", "ExtractFailure", "Ocr", "ocr_pages", "pdf_text", "text_of"]
