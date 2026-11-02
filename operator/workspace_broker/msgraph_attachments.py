"""Outbound attachment validation for the msgraph send verbs.

ONE SHAPE, CHECKED HERE, NOT TRUSTED FROM THE CALLER. The overlay hands the
broker the same pinned descriptors on both transports (``hermes-smd-overlay``
``shared/outbound_attachment.py``: the statute-watch workbook, the SMD-staged
PDFs of ``shared/staged_send.py``) inside the send payload as::

    "attachments": [{"name": str,
                     "content_type": "application/pdf" | <the .xlsx type>,
                     "content_b64": <standard base64>,
                     "sha256": <hex of the decoded bytes>}]

The broker is the last hop before the wire and the only process outside the
agent's address space, so it re-checks every property rather than relaying
whatever arrived. The checks are ``agentmail_attachments.validated``, the same
function the AgentMail verb runs, so the two transports refuse exactly the same
files: at most three entries and one workbook, exactly those four keys, a
``.pdf`` or ``.xlsx`` name from a narrow alphabet matching its content type,
strict base64, at most 2 MiB per PDF and 512 KiB per workbook, a sha256 that
matches the decoded bytes, distinct names, and a PDF that starts as one, is not
encrypted and names no active content. Any violation raises
``AttachmentRefused``, which the send verb re-raises as ``MsGraphRefused`` with
the ``attachment refused:`` prefix the overlay keys on, so the whole send is
refused and audited as such. A send NEVER goes out with an attachment silently
dropped: the recipient would read a message that refers to a file that is not
there, and nothing would say so.

THE ONE DIFFERENCE FROM AGENTMAIL IS THE TOTAL, because Graph's inline request
is smaller. Microsoft documents a single-request file attachment as "under 3
MB" and sends anything from 3 MB to 150 MB through an upload session instead
(learn.microsoft.com/graph/outlook-large-attachments and
/graph/api/message-post-attachments, fetched 2026-11-02); a sendMail request
over about 4 MB is answered 413. The attachment rides inside the JSON as
base64, a third larger than the file, so AgentMail's 3 MiB decoded total is 4
MiB on the wire before the body, which Graph would refuse. Graph therefore
gets :data:`MAX_TOTAL_BYTES` (2.5 MiB decoded, about 3.33 MiB of base64), and
:func:`check_request_size` bounds the serialized ``sendMail`` body itself at
:data:`MAX_SEND_REQUEST_BYTES`, so a large html body beside the files is
refused here, by name, rather than by Graph after the request is on the wire.
No upload session is built: every file a seat may send is under 3 MB.

The Graph shape emitted (``#microsoft.graph.fileAttachment`` with ``name``,
``contentType``, ``contentBytes``) is the one the v1.0 ``sendMail`` reference
documents (Example 3, fetched 2026-11-02; first verified
vfy_01M3TKFJRYTVF6P4CMG10JDYZ0).

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
    MAX_PDF_BYTES,
    MAX_XLSX_ATTACHMENTS,
    PDF_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    AttachmentRefused,
    validated,
)
from .agentmail_attachments import MAX_XLSX_BYTES as MAX_ATTACHMENT_BYTES

#: The decoded bound on every attachment of one Graph send together. 2.5 MiB
#: is 3,495,256 characters of base64, which leaves room under
#: :data:`MAX_SEND_REQUEST_BYTES` for the message body and headers.
MAX_TOTAL_BYTES = 5 * 512 * 1024

#: The serialized ``sendMail`` request a send carrying files may be: Graph's
#: ~4 MB request ceiling, taken as decimal megabytes (the smaller reading).
MAX_SEND_REQUEST_BYTES = 4_000_000

FILE_ATTACHMENT_TYPE = "#microsoft.graph.fileAttachment"


def _graph_part(part: dict[str, str]) -> dict[str, Any]:
    """One AgentMail-shaped validated part as a Graph ``fileAttachment``. The
    content was re-encoded from the decoded bytes by the validator, so what
    reaches Graph is exactly what was hashed, in canonical form."""
    return {
        "@odata.type": FILE_ATTACHMENT_TYPE,
        "name": part["filename"],
        "contentType": part["content_type"],
        "contentBytes": part["content"],
    }


def graph_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Graph ``fileAttachment`` objects for ``payload``, or ``[]`` when it carries none.

    Absent and ``None`` mean no attachment. Anything else present must be a list
    of one to three valid entries under the Graph total; an empty list is
    refused too, because a caller that meant to attach something and produced
    nothing should hear about it.
    """
    built = validated(payload, max_total=MAX_TOTAL_BYTES)
    return [_graph_part(part) for part, _ in built or []]


def attachment_sha256(message: dict[str, Any]) -> dict[str, str]:
    """``attachment_sha256`` of the files on the built ``message``, for the row.

    Computed from the ``contentBytes`` that went on the wire, as the AgentMail
    verb hashes the bytes it sent, so the row describes what Graph received and
    no caller stamp can stand in for it. No attachments yields nothing.
    """
    parts = message.get("attachments")
    if not isinstance(parts, list) or not parts:
        return {}
    digests = [hashlib.sha256(base64.b64decode(part["contentBytes"])).hexdigest() for part in parts]
    return {"attachment_sha256": ",".join(digests)}


def check_request_size(body: dict[str, Any]) -> None:
    """Refuse a ``sendMail`` body carrying files that Graph's inline request
    could not take. Measured exactly as ``MsGraphOps._request`` serializes it."""
    message = body.get("message")
    if not isinstance(message, dict) or not message.get("attachments"):
        return
    size = len(json.dumps(body).encode())
    if size > MAX_SEND_REQUEST_BYTES:
        raise AttachmentRefused(
            f"the message with its attachments is {size} bytes, over Graph's "
            f"{MAX_SEND_REQUEST_BYTES}-byte inline request bound"
        )


def carries_attachments(payload: dict[str, Any]) -> bool:
    """True when ``payload`` names attachments in any form, for a transport that refuses them."""
    return payload.get("attachments") is not None


__all__ = [
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_PDF_BYTES",
    "MAX_SEND_REQUEST_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_XLSX_ATTACHMENTS",
    "PDF_CONTENT_TYPE",
    "XLSX_CONTENT_TYPE",
    "AttachmentRefused",
    "attachment_sha256",
    "carries_attachments",
    "check_request_size",
    "graph_attachments",
]
