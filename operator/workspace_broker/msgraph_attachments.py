"""Outbound attachment validation for the msgraph send verbs.

ONE SHAPE, CHECKED HERE, NOT TRUSTED FROM THE CALLER. The overlay hands the
broker the same pinned descriptors on both transports (``hermes-smd-overlay``
``shared/outbound_attachment.py``: the statute-watch workbook, the SMD-staged
PDFs of ``shared/staged_send.py``) inside the send payload, either inline::

    {"name": str, "content_type": "application/pdf" | <the .xlsx type>,
     "content_b64": <standard base64>, "sha256": <hex of the decoded bytes>}

or, for a file too large to ride the broker's request frame, spooled::

    {"name": str, "content_type": ..., "handle": <32 hex>, "sha256": ...}

where the handle names a file in the broker's own spool
(``attachment_intake.read_spooled``), a directory the agent cannot write. The
broker reads it and hashes it itself; a mismatch refuses.

The broker is the last hop before the wire and the only process outside the
agent's address space, so it re-checks every property rather than relaying
whatever arrived. The checks are ``agentmail_attachments.validated``, the same
function the AgentMail verb runs: at most three entries and one workbook,
exactly the pinned keys, a ``.pdf`` or ``.xlsx`` name from a narrow alphabet
matching its content type, strict base64, at most 512 KiB per workbook, a
sha256 that matches the bytes, distinct names, and a PDF that starts as one, is
not encrypted and names no active content. Graph's caps differ from AgentMail's
in size only: :data:`MAX_PDF_BYTES` and :data:`MAX_TOTAL_BYTES` (25 MiB each,
Captain's lead 2026-11-02), because Graph has an upload-session path and
AgentMail does not. Any violation raises ``AttachmentRefused``, which the send
verb re-raises as ``MsGraphRefused`` with the ``attachment refused:`` prefix the
overlay keys on, so the whole send is refused and audited as such. A send NEVER
goes out with an attachment silently dropped.

TWO WIRE PATHS, CHOSEN BY SIZE. Microsoft documents a single-request file
attachment as "under 3 MB", sends 3 MB to 150 MB through an upload session
(learn.microsoft.com/graph/outlook-large-attachments and
/graph/api/message-post-attachments, fetched 2026-11-02), and a sendMail request
over about 4 MB is answered 413. Files ride sendMail as base64, a third larger
than the file. So a set within :data:`MAX_INLINE_TOTAL_BYTES` (2.5 MiB decoded,
about 3.33 MiB of base64) whose whole serialized request fits
:data:`MAX_SEND_REQUEST_BYTES` goes inline in ``sendMail``, exactly as before;
anything larger goes through ``msgraph_upload.send_via_draft``. The inline shape
(``#microsoft.graph.fileAttachment`` with ``name``, ``contentType``,
``contentBytes``) is the v1.0 ``sendMail`` reference's (Example 3, fetched
2026-11-02; first verified vfy_01M3TKFJRYTVF6P4CMG10JDYZ0).

The base64 never reaches the audit ledger: the row carries
``attachment_sha256`` (the broker's hash of each file it attached, comma-joined
in order) and the payload digest, never the payload itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from .agentmail_attachments import (
    MAX_ATTACHMENTS,
    MAX_XLSX_ATTACHMENTS,
    PDF_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    AttachmentRefused,
    validated,
)
from .agentmail_attachments import MAX_XLSX_BYTES as MAX_ATTACHMENT_BYTES

#: The decoded bound on one PDF on a Graph send, and on every file together.
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024

#: The decoded total that may go inline in ``sendMail``. 2.5 MiB is 3,495,256
#: characters of base64, which leaves room under :data:`MAX_SEND_REQUEST_BYTES`
#: for the message body and headers.
MAX_INLINE_TOTAL_BYTES = 5 * 512 * 1024

#: The serialized ``sendMail`` request a send carrying files may be: Graph's
#: ~4 MB request ceiling, taken as decimal megabytes (the smaller reading).
MAX_SEND_REQUEST_BYTES = 4_000_000

FILE_ATTACHMENT_TYPE = "#microsoft.graph.fileAttachment"

#: One validated file: (name, content type, bytes).
GraphFile = tuple[str, str, bytes]


def _read_spooled(handle: str) -> bytes:
    """The broker spool's reader, imported when a handle first arrives, so this
    module works on a broker built before ``attachment_intake`` existed (where a
    handle refuses by name)."""
    try:
        from . import attachment_intake  # pyright: ignore[reportAttributeAccessIssue]
    except ImportError as exc:
        raise AttachmentRefused("no spool intake on this broker") from exc
    return attachment_intake.read_spooled(handle)


def graph_files(payload: dict[str, Any]) -> list[GraphFile]:
    """The validated files ``payload`` carries, or ``[]`` when it carries none.

    Absent and ``None`` mean no attachment. Anything else present must be a list
    of one to three valid entries under the Graph caps; an empty list is refused
    too, because a caller that meant to attach something and produced nothing
    should hear about it.
    """
    built = validated(payload, max_total=MAX_TOTAL_BYTES, max_pdf=MAX_PDF_BYTES, read_spooled=_read_spooled)
    return [(part["filename"], part["content_type"], raw) for part, raw in built or []]


def inline_parts(files: list[GraphFile]) -> list[dict[str, Any]]:
    """``files`` as Graph ``fileAttachment`` objects, re-encoded from the bytes
    that were hashed, so what reaches Graph is exactly that, in canonical form."""
    return [
        {
            "@odata.type": FILE_ATTACHMENT_TYPE,
            "name": name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(raw).decode("ascii"),
        }
        for name, content_type, raw in files
    ]


def fits_inline(files: list[GraphFile]) -> bool:
    """Whether ``files`` may go inline in one ``sendMail`` (the request size is
    checked separately, once the body is built: :func:`request_fits`)."""
    return sum(len(raw) for _, _, raw in files) <= MAX_INLINE_TOTAL_BYTES


def request_fits(body: dict[str, Any]) -> bool:
    """Whether a ``sendMail`` body is within Graph's inline request bound.
    Measured exactly as ``MsGraphOps._request`` serializes it."""
    return len(json.dumps(body).encode()) <= MAX_SEND_REQUEST_BYTES


def attachment_sha256(files: list[GraphFile]) -> dict[str, str]:
    """``attachment_sha256`` of the files sent, for the row: the broker's own
    hash of the bytes, so no caller stamp can stand in for it."""
    if not files:
        return {}
    return {"attachment_sha256": ",".join(hashlib.sha256(raw).hexdigest() for _, _, raw in files)}


def carries_attachments(payload: dict[str, Any]) -> bool:
    """True when ``payload`` names attachments in any form, for a transport that refuses them."""
    return payload.get("attachments") is not None


__all__ = [
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_INLINE_TOTAL_BYTES",
    "MAX_PDF_BYTES",
    "MAX_SEND_REQUEST_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_XLSX_ATTACHMENTS",
    "PDF_CONTENT_TYPE",
    "XLSX_CONTENT_TYPE",
    "AttachmentRefused",
    "GraphFile",
    "attachment_sha256",
    "carries_attachments",
    "fits_inline",
    "graph_files",
    "inline_parts",
    "request_fits",
]
