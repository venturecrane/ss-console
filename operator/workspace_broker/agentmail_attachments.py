"""Outbound file attachments for the AgentMail send verb.

WHY THIS EXISTS. Until now an AgentMail seat carried no file (``agentmail_ops``
refused every ``attachments`` list), so the one way to get SMD-made files to a
hosted-agent seat's client was to send them from outside the seat, with no gate
and no row. The overlay now hands the broker the same pinned descriptors it
hands the Graph verb (``hermes-smd-overlay`` ``shared/outbound_attachment.py``,
the SMD-staged send in ``shared/staged_send.py``)::

    "attachments": [{"name": str,
                     "content_type": "application/pdf" | <the .xlsx type>,
                     "content_b64": <standard base64>,
                     "sha256": <hex of the decoded bytes>}]

ONE SHAPE, CHECKED HERE, NOT TRUSTED FROM THE CALLER. The broker is the last hop
before the wire and the only process outside the agent's address space, so it
re-checks every property the overlay checked rather than relaying what arrived,
exactly as ``msgraph_attachments`` does for the Graph verb: at most three
entries and one workbook, exactly the four keys, a ``.pdf`` or ``.xlsx`` name
from a narrow alphabet matching its content type, strict base64, a decoded size
of at most 2 MiB per PDF and 512 KiB per workbook and 3 MiB together, a sha256
that matches the decoded bytes, distinct names, and a PDF that starts as one
and does not name the active-content keys anywhere in its bytes. The overlay's
reader parses the PDF fully (it must, to give the gate its text); this byte scan
is the second wall, cheap and blind to compressed object streams by design,
which is why it is a second wall and not the first. Any violation raises
``AttachmentRefused``, which the send verb re-raises as ``AgentMailRefused``
with the ``attachment refused:`` prefix the overlay keys on, so the whole send
is refused and audited as such. A send NEVER goes with an attachment silently
dropped.

THE WIRE SHAPE is AgentMail's ``SendAttachment`` (``filename``,
``content_type``, ``content_disposition``, ``content`` as base64), the shape the
send endpoint documents (docs.agentmail.to, Send Message, fetched 2026-10-20)
and the one ``agentmail_calendar`` already sends. AgentMail bounds a whole send
request at 6 MB, which is why the decoded total stops at 3 MiB (base64 adds a
third) and why ``server.MAX_REQUEST_BYTES`` admits a frame that size.

The base64 never reaches the audit ledger: the row carries ``attachment_sha256``
(the sha256s of the bytes sent, comma-joined in order) and the payload digest.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from typing import Any

PDF_CONTENT_TYPE = "application/pdf"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MAX_ATTACHMENTS = 3
MAX_XLSX_ATTACHMENTS = 1
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_XLSX_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 3 * 1024 * 1024

_ATTACHMENT_KEYS = frozenset({"name", "content_type", "content_b64", "sha256"})
_KINDS: dict[str, tuple[re.Pattern[str], int]] = {
    PDF_CONTENT_TYPE: (re.compile(r"^[\w .()-]{1,120}\.pdf$"), MAX_PDF_BYTES),
    XLSX_CONTENT_TYPE: (re.compile(r"^[\w .()-]{1,120}\.xlsx$"), MAX_XLSX_BYTES),
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: PDF names that make a document act rather than be read. A name in a PDF is a
#: ``/`` and the name's characters, so ``/JS`` must be followed by a delimiter
#: to avoid matching ``/JSomething``; PDF delimiters are whitespace and ()<>[]{}/%.
_ACTIVE_NAMES = (
    b"JS",
    b"JavaScript",
    b"AA",
    b"Launch",
    b"EmbeddedFile",
    b"EmbeddedFiles",
    b"RichMedia",
    b"XFA",
    b"SubmitForm",
    b"ImportData",
    b"GoToR",
    b"GoToE",
)
_ACTIVE_RE = re.compile(rb"/(?:" + b"|".join(_ACTIVE_NAMES) + rb")(?=[\s()<>\[\]{}/%]|$)")


class AttachmentRefused(ValueError):
    """The attachments are not the pinned shape. The send verb re-raises it."""


@dataclass(frozen=True)
class Attachments:
    """The parts to send, and the hashes the audit row records."""

    parts: tuple[dict[str, str], ...]
    sha256s: tuple[str, ...]

    @property
    def audit_sha256(self) -> str:
        return ",".join(self.sha256s)


def _one(entry: Any) -> tuple[dict[str, str], bytes]:
    if not isinstance(entry, dict) or set(entry) != _ATTACHMENT_KEYS:
        raise AttachmentRefused("attachment must carry exactly name, content_type, content_b64, sha256")
    name, content_type, content_b64, sha = (entry[k] for k in ("name", "content_type", "content_b64", "sha256"))
    if not all(isinstance(v, str) for v in (name, content_type, content_b64, sha)):
        raise AttachmentRefused("attachment fields must be strings")
    kind = _KINDS.get(content_type)
    if kind is None:
        raise AttachmentRefused(f"attachment content_type not allowed: {content_type!r}")
    name_re, bound = kind
    if not name_re.fullmatch(name):
        raise AttachmentRefused(f"attachment name does not fit its type: {name!r}")
    if len(content_b64) > 4 * ((bound + 2) // 3):
        raise AttachmentRefused("attachment exceeds its size bound")
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentRefused("attachment content_b64 is not valid standard base64") from exc
    if not raw:
        raise AttachmentRefused("attachment is empty")
    if len(raw) > bound:
        raise AttachmentRefused("attachment exceeds its size bound")
    if not _SHA256_RE.fullmatch(sha) or hashlib.sha256(raw).hexdigest() != sha:
        raise AttachmentRefused("attachment sha256 does not match its content")
    if content_type == PDF_CONTENT_TYPE:
        if not raw.startswith(b"%PDF-"):
            raise AttachmentRefused("attachment is not a PDF file")
        if b"/Encrypt" in raw:
            raise AttachmentRefused("the PDF is encrypted")
        if _ACTIVE_RE.search(raw):
            raise AttachmentRefused("the PDF names scripts, actions or embedded files")
    part = {
        "filename": name,
        "content_type": content_type,
        "content_disposition": "attachment",
        # Re-encoded from the decoded bytes, so what reaches AgentMail is
        # exactly what was hashed, in canonical form.
        "content": base64.b64encode(raw).decode("ascii"),
    }
    return part, raw


def build(payload: dict[str, Any]) -> Attachments | None:
    """The AgentMail parts for ``payload``, or ``None`` when it carries none.

    Absent and ``None`` mean no attachment. Anything else present must be a list
    of one to three valid entries; an empty list is refused too, because a caller
    that meant to attach something and produced nothing should hear about it.
    """
    entries = payload.get("attachments")
    if entries is None:
        return None
    if not isinstance(entries, list) or not entries:
        raise AttachmentRefused("attachments must be a non-empty list")
    if len(entries) > MAX_ATTACHMENTS:
        raise AttachmentRefused(f"at most {MAX_ATTACHMENTS} attachments per send")
    built = [_one(entry) for entry in entries]
    if sum(1 for part, _ in built if part["content_type"] == XLSX_CONTENT_TYPE) > MAX_XLSX_ATTACHMENTS:
        raise AttachmentRefused(f"at most {MAX_XLSX_ATTACHMENTS} workbook per send")
    if sum(len(raw) for _, raw in built) > MAX_TOTAL_BYTES:
        raise AttachmentRefused("the attachments together exceed the 3 MiB bound")
    names = [part["filename"].lower() for part, _ in built]
    if len(set(names)) != len(names):
        raise AttachmentRefused("two attachments share a name")
    return Attachments(
        parts=tuple(part for part, _ in built),
        sha256s=tuple(hashlib.sha256(raw).hexdigest() for _, raw in built),
    )
