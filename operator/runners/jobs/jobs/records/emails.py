"""An Outlook email on the matter, as a PDF the records job can read and attach.

WHY (2026-11-24, A&P). An attorney asked for every parent statement in a
matter's file, attached. The statements were in emails: 262 ``.msg`` files in
the matter's Emails folder, which the records job never looked at, because it
read and cut PDFs only. An email is a matter file like any other, so the job
turns each email it needs into a PDF of its own and from then on treats it as
one: the page finder reads it, a found record is cut or attached whole, and the
reply names it.

THE PDF, in code, nothing a model wrote: the email's From, To, Date and
Subject as the message carries them, its body text, then each PDF attached to
the email (its pages, as sent) and each attached photo (a page each). Any other
attachment (a Word file, a spreadsheet, another email) is named at the end of
the first part by its file name, so the reader knows it was there and is not in
this copy. The email is read with the demand lane's reader
(``demand.pull._open_msg``: the cp1252 retry included), so the two lanes read
an email the same way.

:func:`search_text` is the email's subject and body for the job's $0 search
(``rank.text_matches``): an email's file name is usually its subject, and the
words a person asks with are often only in its body.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from ..demand import pull as demand_pull

#: An OLE compound file's first eight bytes: every Outlook ``.msg`` is one.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: An email bigger than this is not fetched (the size one reply carries).
MAX_EMAIL_BYTES = 25 * 1024 * 1024
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif")
PAGE_W, PAGE_H, MARGIN = 612.0, 792.0, 54.0
CSS = "body { font-family: sans-serif; font-size: 10pt; } p { margin: 0 0 6pt 0; } .h { font-size: 9pt; }"
#: Pages of body text one email is laid out on, at most (a runaway guard).
MAX_BODY_PAGES = 60


class EmailUnreadable(ValueError):
    """The file is not an email this job can read; the message says why."""


def read(path: Path) -> dict[str, Any]:
    """The email's fields (``demand.pull._open_msg``), or :class:`EmailUnreadable`."""
    with open(path, "rb") as fh:
        if fh.read(8) != OLE_MAGIC:
            raise EmailUnreadable("not an Outlook email file")
    msg = demand_pull._open_msg(path)
    if msg is None:
        raise EmailUnreadable("the email could not be read")
    return msg


def search_text(msg: dict[str, Any]) -> str:
    return f"{msg.get('subject') or ''}\n{msg.get('body') or ''}"


def _head_html(msg: dict[str, Any], left_out: list[str]) -> str:
    rows = [
        ("From", msg.get("from_display") or msg.get("sender") or ""),
        ("To", msg.get("to_display") or ""),
        ("Date", msg.get("date") or ""),
        ("Subject", msg.get("subject") or ""),
    ]
    head = "".join(f'<p class="h"><b>{k}:</b> {html.escape(str(v))}</p>' for k, v in rows)
    body = "".join(f"<p>{html.escape(line) or '&#160;'}</p>" for line in str(msg.get("body") or "").splitlines())
    tail = (
        "<p><b>Attached to this email and not in this copy:</b> " + html.escape(", ".join(left_out)) + "</p>"
        if left_out
        else ""
    )
    return f"<html><body>{head}<hr/>{body}{tail}</body></html>"


def _lay(html_: str) -> bytes:
    import io

    import pymupdf

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=html_, user_css=CSS)
    where = pymupdf.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN)
    more, n = True, 0
    while more and n < MAX_BODY_PAGES:
        dev = writer.begin_page(pymupdf.Rect(0, 0, PAGE_W, PAGE_H))
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        n += 1
    writer.close()
    return buf.getvalue()


def _part(name: str, data: Any) -> bytes | None:
    """An attachment as PDF bytes ready to append, or None when it is not a
    PDF or a photo, or will not open."""
    import pymupdf

    if not isinstance(data, bytes):
        return None
    low = name.lower()
    try:
        if low.endswith(".pdf") and data.startswith(b"%PDF-"):
            with pymupdf.open(stream=data, filetype="pdf") as part:
                return None if part.needs_pass else data
        if low.endswith(IMAGE_EXT):
            with pymupdf.open(stream=data) as img:
                return img.convert_to_pdf()
    except Exception:  # noqa: BLE001 - an attachment that will not open is named as left out, never guessed at
        return None
    return None


def to_pdf(msg: dict[str, Any], dest: Path) -> dict[str, Any]:
    """Write the email as one PDF at ``dest``; returns the attachment names
    that went in (``included``) and those named as not in this copy (``left_out``)."""
    import pymupdf

    parts: list[bytes] = []
    included: list[str] = []
    left_out: list[str] = []
    for name, data in msg.get("attachments") or []:
        got = _part(str(name), data)
        if got is None:
            left_out.append(str(name))
        else:
            parts.append(got)
            included.append(str(name))
    doc = pymupdf.open(stream=_lay(_head_html(msg, left_out)), filetype="pdf")
    try:
        for data in parts:
            with pymupdf.open(stream=data, filetype="pdf") as part:
                doc.insert_pdf(part)
        doc.set_metadata({})
        dest.write_bytes(doc.tobytes(garbage=3, deflate=True, no_new_id=True))
    finally:
        doc.close()
    return {"included": included, "left_out": left_out}


__all__ = ["MAX_EMAIL_BYTES", "OLE_MAGIC", "EmailUnreadable", "read", "search_text", "to_pdf"]
