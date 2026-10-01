"""Outbound attachment validation for the msgraph send verbs.

ONE SHAPE, CHECKED HERE, NOT TRUSTED FROM THE CALLER. The overlay computes the
attachment (today: the statute-watch workbook) and hands it to the broker inside
the send payload as::

    "attachments": [{"name": str,
                     "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     "content_b64": <standard base64>,
                     "sha256": <hex of the decoded bytes>}]

The broker is the last hop before the wire and the only process outside the
agent's address space, so it re-checks every property rather than relaying
whatever arrived: at most one entry, exactly those four keys, an ``.xlsx`` name
from a narrow alphabet, the one content type, strict base64, a decoded size of at
most 512 KiB, and a sha256 that matches the decoded bytes. Any violation raises
``AttachmentRefused``, which the send verb re-raises as ``MsGraphRefused``, so the
whole send is refused and audited as such. A send NEVER goes out with its
attachment silently dropped: the recipient would read a message that refers to a
workbook that is not there, and nothing would say so.

The Graph shape emitted (``#microsoft.graph.fileAttachment`` with ``name``,
``contentType``, ``contentBytes``) is the one the v1.0 ``sendMail`` reference
documents (vendor docs fetched via Context7, vfy_01M3TKFJRYTVF6P4CMG10JDYZ0).
Inline attachments on sendMail are bounded by Graph at roughly 3 MB per request;
512 KiB keeps the broker frame (``server.MAX_REQUEST_BYTES``, 1 MiB) comfortable
with the base64 expansion on top.

The base64 never reaches the audit ledger: the row carries the caller's
``attachment_sha256`` (``transmit_verbs._CALLER_AUDIT_KEYS``) and the payload
digest, never the payload itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

#: The one content type the broker will attach. The only caller is the workbook.
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Decoded-size bound, shared with the overlay (plan revision 10).
MAX_ATTACHMENT_BYTES = 512 * 1024

MAX_ATTACHMENTS = 1

_ATTACHMENT_KEYS = frozenset({"name", "content_type", "content_b64", "sha256"})
_NAME_RE = re.compile(r"^[\w .()-]{1,120}\.xlsx$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Upper bound on the encoded length for MAX_ATTACHMENT_BYTES, checked before
#: decoding so an oversized frame is refused without being allocated twice.
_MAX_B64_LEN = 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3)


class AttachmentRefused(ValueError):
    """The attachment is not the pinned shape. Callers re-raise as their refusal type."""


def _one(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _ATTACHMENT_KEYS:
        raise AttachmentRefused("attachment must carry exactly name, content_type, content_b64, sha256")
    name = entry["name"]
    content_type = entry["content_type"]
    content_b64 = entry["content_b64"]
    sha = entry["sha256"]
    if not all(isinstance(v, str) for v in (name, content_type, content_b64, sha)):
        raise AttachmentRefused("attachment fields must be strings")
    if not _NAME_RE.fullmatch(name):
        raise AttachmentRefused(f"attachment name is not an allowed .xlsx name: {name!r}")
    if content_type != XLSX_CONTENT_TYPE:
        raise AttachmentRefused(f"attachment content_type not allowed: {content_type!r}")
    if len(content_b64) > _MAX_B64_LEN:
        raise AttachmentRefused("attachment exceeds the 512 KiB bound")
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentRefused("attachment content_b64 is not valid standard base64") from exc
    if not raw:
        raise AttachmentRefused("attachment is empty")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AttachmentRefused("attachment exceeds the 512 KiB bound")
    if not _SHA256_RE.fullmatch(sha) or hashlib.sha256(raw).hexdigest() != sha:
        raise AttachmentRefused("attachment sha256 does not match its content")
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        # Re-encoded from the decoded bytes, so what reaches Graph is exactly
        # what was hashed, in canonical form.
        "contentBytes": base64.b64encode(raw).decode("ascii"),
    }


def graph_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Graph ``fileAttachment`` objects for ``payload``, or ``[]`` when it carries none.

    Absent and ``None`` mean no attachment. Anything else present must be a list
    of one valid entry; an empty list is refused too, because a caller that meant
    to attach something and produced nothing should hear about it.
    """
    entries = payload.get("attachments")
    if entries is None:
        return []
    if not isinstance(entries, list) or not entries:
        raise AttachmentRefused("attachments must be a non-empty list")
    if len(entries) > MAX_ATTACHMENTS:
        raise AttachmentRefused(f"at most {MAX_ATTACHMENTS} attachment per send")
    return [_one(entry) for entry in entries]


def carries_attachments(payload: dict[str, Any]) -> bool:
    """True when ``payload`` names attachments in any form, for a transport that refuses them."""
    return payload.get("attachments") is not None
