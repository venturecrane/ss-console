"""Outlook .msg: the one reader of a saved email, for every caller.

An email a firm saves into a matter is an Outlook message: an OLE compound
file holding the subject, the parties, the date, the body and the attachments.
Three places read them, and they read them here: ``read_document`` and the
drafting record check (``extract.py``, the agent's in-turn reading), the job
lanes' attachment index (``jobs/stages/msg.py``), and the job lanes' email text
(``jobs/demand/pull.py``, which the litigation, negotiation and case file lanes
reach through). Before 2026-11-24 the two job readers each opened the message
themselves and ``read_document`` refused every .msg as "a legacy Office file
... needs manual review"; on one A&P matter that was 261 saved emails the
Operator could not read during a conversation.

What this module does is open the message and hand back its fields as text and
bytes. It decides nothing about them: which attachments are records, which
emails are walled, how an email is rendered for a model, all stay with the
caller.

``extract_msg`` is imported inside the functions, never at module level: the
job lanes' tests put a stand-in for it in ``sys.modules`` and the import must
find that stand-in, and a caller that never opens an email never pays for the
import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from .xlsx_io import OLE_MAGIC

#: The code page a message is retried in when its strings do not decode as
#: declared. Found twice live: 2026-10-06 (cp1252 curly quotes in a body, a
#: trial matter) and 2026-10-23 (a cp1252 en dash in a subject, A&P matter
#: 202098, which killed a chronology job at index_msg).
FALLBACK_ENCODING = "cp1252"

#: An image attached to an email smaller than this is an inline signature glyph
#: or a logo, never a record: the job lanes' attachment index (``jobs/stages/
#: msg.py``) skips it as tiny, and the connector's email read names it and does
#: not count it. A photographed bill page measured 0.20 MB.
INLINE_IMAGE_MIN_BYTES = 50_000

#: An Exchange-internal (X500) address, ``/O=EXCHANGELABS/OU=.../CN=...``. It has
#: no domain, so an address search finds nothing in it.
_X500 = re.compile(r"/o=[^<>;,]*", re.I)
INTERNAL_ADDRESS = "internal Exchange address"


def party_text(raw: Any) -> str:
    """A From/To/Cc value as a person reading the email sees it: display names
    kept, an X500 address named as internal. For the TEXT a reader sees only;
    the job lanes' wall decides on addresses, never on this.

    WHY (2026-10-21): the email head rendered only the SMTP addresses a regex
    found, so every display name was dropped and a sender stored as an X500
    address became a blank ``From:``. The negotiation review's first check is
    who sent the email to whom; on A&P it held seven ``.msg`` offers on that
    check in one run and passed four of them on a re-read of the same text."""
    return " ".join(_X500.sub(INTERNAL_ADDRESS, str(raw or "")).split())[:500]


@dataclass(frozen=True)
class MsgAttachment:
    """One attachment. ``data`` is the attachment's bytes, or None for an
    attached email (the reader hands back a message object, not bytes) or a
    payload that could not be read; ``error`` then says why for the latter."""

    name: str
    data: bytes | None
    error: str | None = None


@dataclass(frozen=True)
class MsgFields:
    """A message's fields as stored. The party fields are the raw header values
    (display names and addresses together); ``date`` is the sent time as text,
    empty when the message carries none."""

    subject: str
    sender: str
    to: str
    cc: str
    bcc: str
    date: str
    body: str
    attachments: tuple[MsgAttachment, ...]


def is_msg(blob: bytes) -> bool:
    """True when the bytes are an Outlook message: an OLE compound file with
    the MAPI property stream at its root. A legacy .doc or .xls is the same
    container without that stream, so the bytes decide, never the extension."""
    if not blob.startswith(OLE_MAGIC):
        return False
    import olefile

    try:
        with olefile.OleFileIO(blob) as ole:
            return ["__properties_version1.0"] in ole.listdir()
    except Exception:  # noqa: BLE001 - a container olefile cannot parse is not a readable message; the caller refuses it by name
        return False


def _field(m: Any, name: str) -> str:
    return str(getattr(m, name, None) or "")


def _attachment(att: Any) -> MsgAttachment:
    name = str(getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or "unnamed")
    try:
        data = att.data
    except Exception as exc:  # noqa: BLE001 - an attachment whose payload cannot be read is reported as that attachment's error; the rest are still read
        return MsgAttachment(name, None, str(exc)[:120])
    return MsgAttachment(name, data if isinstance(data, bytes) else None)


def read_msg(source: bytes | str, encoding: str | None = None) -> MsgFields:
    """One message read whole, in one encoding (None: as it declares). Raises
    whatever ``extract_msg`` raises; :func:`open_msg` is the retrying door.
    ``source`` is the file's bytes or its path."""
    import extract_msg

    m = extract_msg.Message(source, overrideEncoding=encoding) if encoding else extract_msg.Message(source)
    try:
        return MsgFields(
            subject=_field(m, "subject"),
            sender=_field(m, "sender"),
            to=_field(m, "to"),
            cc=_field(m, "cc"),
            bcc=_field(m, "bcc"),
            date=_field(m, "date"),
            body=_field(m, "body"),
            attachments=tuple(_attachment(a) for a in (getattr(m, "attachments", None) or [])),
        )
    finally:
        try:
            m.close()
        except Exception:  # noqa: BLE001 - closing must not mask what was read, or the error that is already propagating
            pass


def open_msg(source: bytes | str) -> MsgFields:
    """The message's fields, read as it declares and, failing that, once more
    in :data:`FALLBACK_ENCODING`. Reading it whole each time means a retry
    never hands back half an email. Raises the retry's error when both fail."""
    try:
        return read_msg(source)
    except Exception:  # noqa: BLE001 - any failure on the declared encoding gets the one code-page retry below, whose own error is the one raised
        return read_msg(source, FALLBACK_ENCODING)


def email_text(fields: MsgFields, attachment_texts: Sequence[str]) -> str:
    """The email as a person reading it sees it: the head (subject, parties,
    date, the attachments by name), the body, then each attachment's text
    under its name. ``attachment_texts`` holds one text per attachment, in
    order, or a bracketed note saying why there is none; the caller reads
    them, so an attachment is read by exactly the roads any other document is."""
    names = "; ".join(a.name for a in fields.attachments) or "none"
    head = [
        f"Email: {fields.subject}",
        f"From: {party_text(fields.sender)}",
        f"To: {party_text(fields.to)}",
        *([f"Cc: {party_text(fields.cc)}"] if fields.cc else []),
        f"Date: {fields.date}",
        f"Attachments: {names}",
    ]
    parts = ["\n".join(head), fields.body.strip()]
    total = len(fields.attachments)
    for n, (att, text) in enumerate(zip(fields.attachments, attachment_texts, strict=True), start=1):
        parts.append(f"[Attachment {n} of {total}: {att.name}]\n{text}".rstrip())
    return "\n\n".join(p for p in parts if p)
