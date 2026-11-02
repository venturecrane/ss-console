"""Matter files on a reply to the firm colleague who asked for them (ADR 0101).

WHY THIS EXISTS (2026-11-01, ashton-price). An attorney emailed the Operator
for named records on a matter, attached to a reply. The Operator could neither
find them by name nor attach them. The Smokeball connector now finds them
(``find_files_on_matter``) and stages them (``stage_matter_files``): it
fetches the bytes from the firm's system itself, cuts a page range out of a
large file if the model named one, and writes each file to the seat's outbound
file spool. This module carries the staged files from there onto the reply.

THE MODEL NEVER HANDLES A FILE. It names Smokeball file ids (and page ranges)
to the connector; code fetches, cuts, hashes and spools. What the model's
result says is attribution only: this module records the staging from the
connector's own tool result, in a hook, keyed on the session, and checks every
byte against the sha256 the connector reported.

THE FENCES, in the order they run.

At staging (``on_stage_result``, the stage tool's transform hook):

1. The seat authors ``matter_file_delivery.recipients`` (unauthored: nothing).
2. An email opened THIS turn, on a Graph seat; the files are bound to that
   email (its message id), never to a later one.
3. Its sender is firm staff: on ``matter_file_delivery.recipients``, on the
   reply roster, classifying INTERNAL (never a typed client, vendor, or anyone
   outside), and not an authored device (no stand-in gets files).
4. Each file read back from the connector spool matches its sha256, is a PDF,
   and passes the gate's structural PDF check (no scripts, triggers, actions
   other than a jump or a link, embedded files, media or XFA; not encrypted).
5. The bytes go into the BROKER's own spool (``attachment_put``), which hashes
   them itself; only its handle travels with the reply.

At the reply (``prepare``, in the relay, after every floor the reply already
meets):

6. The same email, the same recipient rule, no binding, no device redirect.
7. One matter: the matter the files came from is the only matter whose content
   this session read (ADR 0086's provenance, the content-read fence), and the
   reply body cites no other matter.
8. The files split into replies of at most :data:`MAX_FILES_PER_REPLY` and
   :data:`MAX_BYTES_PER_REPLY`; the first rides on the person's reply, the rest
   go as further replies in the same thread with code-fixed words.

THE SCANNED-PDF RULE, and why. The outbound gate refuses a PDF with no readable
text layer, because what it cannot read it cannot check. That rule stays for
every send that is not this one. A firm's own record, fetched by code from the
firm's own system and returned unaltered to the firm's own staff, is exempt
from the TEXT read and from nothing else: the text gate exists to catch words
the Operator wrote or relayed (fabrication, identifiers from another matter,
privileged facts reaching an outsider), and none of those can be in a scan the
firm itself filed, going back to a colleague who can already open it in
Smokeball. What could still go wrong is structural (an active PDF) or a
misdelivery (the wrong matter, the wrong person), and those fences run on every
file, scanned or not. Outside the firm the rule is unchanged: no recipient but
the firm's own staff can receive anything through this path.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared import outbound_attachment, result_unwrap

logger = logging.getLogger(__name__)

STAGE_TOOL = "mcp_smokeball_stage_matter_files"
CONFIG_BLOCK = "matter_file_delivery"
#: Mirrors the connector (``smokeball_connector/matter_files.py``).
SPOOL_DIR_ENV = "SMD_OUTBOUND_FILE_SPOOL_DIR"
DEFAULT_SPOOL_DIR = "/opt/data/outbound-file-spool"
#: The fleet's per-message cap.
MAX_FILES_PER_REPLY = 3
MAX_BYTES_PER_REPLY = 25 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Replies one request may take (the person's reply plus continuations).
MAX_REPLIES = 3
#: Decoded bytes per broker chunk (4 MiB base64, under the 6 MiB frame).
CHUNK_BYTES = 3 * 1024 * 1024
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SESSIONS = 128

REFUSAL_REASON = "matter_files_refused"


@dataclass(frozen=True)
class StagedFile:
    file_id: str
    name: str
    sha256: str
    size: int
    handle: str


@dataclass
class Staging:
    """What one turn staged for its reply. ``refusal`` set means: hold the reply."""

    message_id: str
    matter_id: str
    files: list[StagedFile] = field(default_factory=list)
    refusal: str = ""


@dataclass(frozen=True)
class Prepared:
    """The files a reply carries, split into the replies that carry them."""

    batches: tuple[tuple[StagedFile, ...], ...]

    @staticmethod
    def descriptors(batch: tuple[StagedFile, ...]) -> list[dict[str, str]]:
        return [
            {
                "name": f.name,
                "content_type": outbound_attachment.PDF_CONTENT_TYPE,
                "handle": f.handle,
                "sha256": f.sha256,
            }
            for f in batch
        ]


class FileRefused(ValueError):
    """A plain reason the files cannot go."""


_LOCK = threading.Lock()
_STAGED: OrderedDict[str, Staging] = OrderedDict()


def _put(session_id: str, staging: Staging) -> None:
    with _LOCK:
        _STAGED[session_id] = staging
        _STAGED.move_to_end(session_id)
        while len(_STAGED) > _MAX_SESSIONS:
            _STAGED.popitem(last=False)


def staged(session_id: str) -> Staging | None:
    with _LOCK:
        return _STAGED.get(session_id) if session_id else None


def discard(session_id: str) -> None:
    with _LOCK:
        _STAGED.pop(session_id, None)


def _reset_for_tests() -> None:
    with _LOCK:
        _STAGED.clear()


# ---------------------------------------------------------------------------
# Who may receive
# ---------------------------------------------------------------------------


def staff_list(doc: Any) -> tuple[frozenset[str], frozenset[str]]:
    """``matter_file_delivery.recipients`` as (exact addresses, domains)."""
    block = doc.get(CONFIG_BLOCK) if isinstance(doc, dict) else None
    raw = block.get("recipients") if isinstance(block, dict) else None
    exact: set[str] = set()
    domains: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        text = str(entry or "").strip().lower()
        if text.startswith("@") and "." in text:
            domains.add(text[1:])
        elif "@" in text:
            exact.add(text)
    return frozenset(exact), frozenset(domains)


def authored(doc: Any) -> bool:
    exact, domains = staff_list(doc)
    return bool(exact or domains)


def recipient_refusal(
    doc: Any, address: str, *, internal: bool, on_roster: bool, device: bool
) -> str | None:
    """Why ``address`` may not receive matter files, or ``None``."""
    if not authored(doc):
        return (
            "this seat does not send matter files by email (matter_file_delivery is not authored)"
        )
    if device:
        return "matter files never go to a device's stand-in; only to the person who asked"
    value = str(address or "").strip().lower()
    exact, domains = staff_list(doc)
    if not (value in exact or value.rsplit("@", 1)[-1] in domains) or not on_roster or not internal:
        return "matter files go only to the firm's own staff named in matter_file_delivery, never to anyone outside"
    return None


# ---------------------------------------------------------------------------
# Staging: from the connector's spool to the broker's
# ---------------------------------------------------------------------------


def spool_dir() -> Path:
    return Path(os.environ.get(SPOOL_DIR_ENV, "").strip() or DEFAULT_SPOOL_DIR)


def read_connector_spool(token: str, sha256: str, *, directory: Path | None = None) -> bytes:
    """The connector's spooled bytes for ``token``, refused unless they hash to ``sha256``."""
    if not _TOKEN_RE.fullmatch(token or "") or not _SHA_RE.fullmatch(sha256 or ""):
        raise FileRefused("the staged file's reference is malformed")
    base = (directory or spool_dir()).resolve()
    path = base / f"{token}.bin"
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise FileRefused("the staged file is no longer on the seat") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
        raise FileRefused("the staged file is not a plain file within the size bound")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise FileRefused("the staged file changed after the connector fetched it")
    return data


def check_record_pdf(data: bytes) -> None:
    """The gate's structural PDF check, without the text read (the scanned-PDF rule)."""
    if not data.startswith(b"%PDF-"):
        raise FileRefused("it is not a PDF file")
    try:
        reader = outbound_attachment._pdf_reader(data)
        if not list(reader.pages):
            raise FileRefused("the PDF has no pages")
        outbound_attachment._pdf_refuse_active_content(reader)
    except outbound_attachment.AttachmentError as exc:
        raise FileRefused(str(exc)) from exc
    except FileRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - could not look is a refusal
        raise FileRefused("the PDF could not be read") from exc


def push_to_broker(data: bytes, sha256: str, request: Any) -> str:
    """Hand ``data`` to the broker's own spool in chunks; return its handle."""
    handle: str | None = None
    offset = 0
    while True:
        chunk = data[offset : offset + CHUNK_BYTES]
        final = offset + len(chunk) >= len(data)
        payload: dict[str, Any] = {
            "offset": offset,
            "chunk_b64": base64.b64encode(chunk).decode("ascii"),
            "final": final,
        }
        if handle:
            payload["handle"] = handle
        if final:
            payload["sha256"] = sha256
        answer = request({"action": "attachment_put", "payload": payload}, timeout=20.0)
        handle = str(answer.get("handle") or "")
        if not _TOKEN_RE.fullmatch(handle):
            raise FileRefused("the broker did not take the file")
        offset += len(chunk)
        if final:
            if answer.get("sha256") != sha256:
                raise FileRefused("the broker's copy did not hash to the staged file")
            return handle


def _entries(result: Any, matter_id: str) -> list[dict[str, Any]]:
    parsed = result_unwrap.parse_tool_result(result)
    if (
        not isinstance(parsed, dict)
        or str(parsed.get("matterId") or "").lower() != matter_id.lower()
    ):
        return []
    staged_rows = parsed.get("staged")
    return [r for r in staged_rows if isinstance(r, dict)] if isinstance(staged_rows, list) else []


def stage_from_result(
    session_id: str,
    args: Any,
    result: Any,
    *,
    origin_message_id: str,
    refusal: str | None,
    request: Any,
    spool: Path | None = None,
) -> str:
    """Record one stage call's files for this turn's reply. Returns the line the
    model reads after the connector's own result (what is staged, what is not)."""
    matter_id = str((args or {}).get("matter_id") or "").strip() if isinstance(args, dict) else ""
    rows = _entries(result, matter_id) if matter_id else []
    if not rows:
        return ""
    if refusal is None and not origin_message_id:
        refusal = "no email opened this turn, so there is no reply for the files to go on"
    prior = staged(session_id)
    if prior and (
        prior.message_id != origin_message_id or prior.matter_id.lower() != matter_id.lower()
    ):
        refusal = refusal or "files from a second matter or a second email were staged in this turn"
    if refusal:
        _put(session_id, Staging(origin_message_id, matter_id, [], refusal))
        return (
            f"[matter files] NOT staged for your reply: {refusal}. Do not say any file is attached."
        )
    staging = prior or Staging(origin_message_id, matter_id)
    kept, refused = [], []
    for row in rows:
        name = str(row.get("attachmentName") or "")
        try:
            if not outbound_attachment.PDF_NAME_RE.fullmatch(name):
                raise FileRefused("its attachment name is not a plain PDF name")
            sha = str(row.get("sha256") or "")
            data = read_connector_spool(str(row.get("spoolToken") or ""), sha, directory=spool)
            check_record_pdf(data)
            if any(f.name.lower() == name.lower() for f in staging.files):
                raise FileRefused("a file of that name is already staged")
            handle = push_to_broker(data, sha, request)
        except FileRefused as exc:
            refused.append(f"{name or row.get('fileId')} ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001 - one file's fault is named, never fatal to the rest
            refused.append(
                f"{name or row.get('fileId')} (it could not be handed to the mail service: {type(exc).__name__})"
            )
            continue
        staging.files.append(StagedFile(str(row.get("fileId") or ""), name, sha, len(data), handle))
        kept.append(name)
    over = len(staging.files) > MAX_FILES_PER_REPLY * MAX_REPLIES
    if over:
        staging.refusal = (
            f"more than {MAX_FILES_PER_REPLY * MAX_REPLIES} files were staged for one request"
        )
    _put(session_id, staging)
    lines = []
    if kept and not over:
        lines.append(f"[matter files] Staged for your reply: {', '.join(kept)}.")
    if refused:
        lines.append(f"[matter files] Not staged: {'; '.join(refused)}. Tell the person.")
    if over:
        lines.append(
            f"[matter files] NOT staged: {staging.refusal}. Do not say any file is attached."
        )
    return " ".join(lines)


# ---------------------------------------------------------------------------
# At the reply
# ---------------------------------------------------------------------------


def _batches(files: list[StagedFile]) -> tuple[tuple[StagedFile, ...], ...]:
    out: list[list[StagedFile]] = []
    for f in files:
        if (
            not out
            or len(out[-1]) >= MAX_FILES_PER_REPLY
            or sum(x.size for x in out[-1]) + f.size > MAX_BYTES_PER_REPLY
        ):
            out.append([])
        out[-1].append(f)
    return tuple(tuple(b) for b in out)


def prepare(
    staging: Staging,
    *,
    doc: Any,
    message_id: str,
    recipient: str,
    internal: bool,
    on_roster: bool,
    redirected: bool,
    bound: bool,
    msgraph: bool,
    read_matters: set[str],
    cited_matters: set[str],
) -> Prepared:
    """The files for this reply, or :class:`FileRefused` with a plain reason."""
    if staging.refusal:
        raise FileRefused(staging.refusal)
    if not msgraph:
        raise FileRefused("this seat's mail transport cannot carry files on a reply")
    if bound or staging.message_id != message_id:
        raise FileRefused(
            "files go only on the reply to the email that asked for them, in the same turn"
        )
    why = recipient_refusal(
        doc, recipient, internal=internal, on_roster=on_roster, device=redirected
    )
    if why:
        raise FileRefused(why)
    matter = staging.matter_id.lower()
    if {m.lower() for m in read_matters} - {matter}:
        raise FileRefused(
            "this session read another matter's content, so no file from either goes out"
        )
    if {m.lower() for m in cited_matters} - {matter}:
        raise FileRefused("the reply names a different matter than the one the files came from")
    if not staging.files:
        raise FileRefused("none of the requested files could be staged")
    batches = _batches(staging.files)
    if len(batches) > MAX_REPLIES:
        raise FileRefused(f"the files need more than {MAX_REPLIES} replies")
    return Prepared(batches)


def continuation_text(index: int, total: int, batch: tuple[StagedFile, ...]) -> str:
    """The code-fixed words of a further reply carrying the rest of the files."""
    names = ", ".join(f.name for f in batch)
    return f"Part {index} of {total} of the files you asked for, attached: {names}."


def audit_fields(batch: tuple[StagedFile, ...]) -> dict[str, Any]:
    """What a REPLY_SENT row says about the files: ids and hashes, never names."""
    return {
        "attachment_sha256": ",".join(f.sha256 for f in batch),
        "attached_file_ids": [f.file_id for f in batch],
    }


__all__ = [
    "CONFIG_BLOCK",
    "MAX_FILES_PER_REPLY",
    "REFUSAL_REASON",
    "STAGE_TOOL",
    "FileRefused",
    "Prepared",
    "StagedFile",
    "Staging",
    "authored",
    "audit_fields",
    "check_record_pdf",
    "continuation_text",
    "discard",
    "prepare",
    "push_to_broker",
    "read_connector_spool",
    "recipient_refusal",
    "stage_from_result",
    "staged",
    "staff_list",
]
