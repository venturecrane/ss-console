"""The request email's own Word attachments, carried with a drafting job.

An attorney who drafted his own sets of discovery attaches them to the request
("put these in the caption on pleading paper"); the drafting job places each
one in the court's caption on the firm's paper (ss-console
``runners/medchron/medchron/drafting/place.py``). The overlay's
``drafting_job_submit`` reads the attachments of the request email itself
(the turn's verified origin, never a file the model names) and sends their
bytes in the envelope; this module is the broker's check of that field, and
the runner checks it again (``drafting/job.py``) before it reads a byte.

Shape, each entry exactly::

    {"filename": "<name>.docx", "sha256": "<64 hex>", "b64": "<base64 bytes>"}

At most ``MAX_ATTACHMENTS`` entries and ``MAX_TOTAL_BYTES`` of decoded bytes in
all: the broker's socket takes one request line of at most 1 MiB, and base64
grows bytes by a third. Only Word files: python-docx reads them, and an
attorney's own set arrives as one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

MAX_ATTACHMENTS = 6
MAX_TOTAL_BYTES = 700_000
_NAME = re.compile(r"^[^/\\\x00]{1,200}\.docx$", re.I)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class AttachmentError(ValueError):
    """The envelope's attachments are malformed; the submit is refused."""


def validate(raw: Any) -> list[dict[str, str]]:
    """The attachments, normalized, or ``AttachmentError``."""
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
        raise AttachmentError(f"attachments must be a list of at most {MAX_ATTACHMENTS} Word documents")
    out, total = [], 0
    for a in raw:
        if not isinstance(a, dict) or set(a) != {"filename", "sha256", "b64"}:
            raise AttachmentError("each attachment carries exactly filename, sha256 and b64")
        name, sha, b64 = a["filename"], a["sha256"], a["b64"]
        if not (isinstance(name, str) and _NAME.match(name)):
            raise AttachmentError("an attachment must be a Word document (.docx) with a plain file name")
        if not (isinstance(sha, str) and _SHA.match(sha) and isinstance(b64, str)):
            raise AttachmentError("an attachment's sha256 and bytes must be given")
        try:
            blob = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error):
            raise AttachmentError("an attachment's bytes are not base64") from None
        if hashlib.sha256(blob).hexdigest() != sha:
            raise AttachmentError("an attachment's bytes do not match its sha256")
        total += len(blob)
        out.append({"filename": name, "sha256": sha, "b64": b64})
    if total > MAX_TOTAL_BYTES:
        raise AttachmentError(
            f"the attached documents are {total:,} bytes together, over the {MAX_TOTAL_BYTES:,} a request can carry"
        )
    return out
