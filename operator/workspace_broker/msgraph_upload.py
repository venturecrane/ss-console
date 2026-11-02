"""Large outbound files over Microsoft Graph: a draft, upload sessions, then send.

WHY THIS EXISTS. ``sendMail`` carries files inline as base64 in one JSON request,
and Graph bounds that request at about 4 MB and a single-request file at "under
3 MB" (learn.microsoft.com/graph/outlook-large-attachments, fetched 2026-11-02).
The records a firm asks for are scans: one request on 2026-11-02 carried a 6.67
MB, 35-page file. Graph's answer for 3 MB to 150 MB is an upload session on a
DRAFT: create the message, ``createUploadSession`` per file, PUT the bytes in
ranges, then ``POST /messages/{id}/send``. This module is that path, and
:func:`upload_large_attachment` is the shared helper the reply path calls too.

THE CREDENTIAL SPLIT, AND THE RESIDUAL RACE IT LEAVES. Creating a draft and an
upload session needs ``Mail.ReadWrite``. The broker's send app holds
``Mail.Send`` only (overlay#280, ``msgraph_auth``); the read app holds
``Mail.ReadWrite``, and so does the agent, which uses the same registration for
its own mail tools (``msgraph_drafts``). So the draft is built on the READ
credential and sent on the SEND credential, and between those two calls anything
holding the agent's read credential could PATCH the draft: change its
recipients, its body, its files. That would carry a message past the recipient
fence with the broker's own row behind it.

What closes most of it (Captain's lead, 2026-11-02, Option A): the broker builds
the draft, adds the files and sends it inside ONE broker call with nothing else
awaited between, and immediately before ``/send`` it re-reads the draft on the
read credential and checks recipients (to, cc, bcc), subject, body hash
(``html_canon`` for an HTML body, the calibrated Exchange rewrite) and every
attachment's name, byte length and sha256 of its ``$value`` bytes against what
it built. Any difference, an extra file included, refuses the send, deletes the
draft, and is audited as a refusal. The draft id is on the row.

What remains, stated rather than hidden: the window between that verifying read
and Graph acting on ``/send``. It is one round trip, and a writer would have to
know the draft id (minted seconds earlier, never returned to the agent) and race
it. Option B closes it: the firm's Microsoft 365 administrator grants the send
app ``Mail.ReadWrite``, so the credential that touches the draft is one the
agent never holds. That is a client tenant act and is on the A&P ask list; this
path does not wait for it. ADR 0101 records the decision and the residual.

FAILURE SEMANTICS. Everything before ``/send`` is a definite "not sent": the
draft is deleted and the failure raises :class:`MsGraphRefused` with the
``attachment refused:`` prefix the overlay keys on. A 4xx from ``/send`` itself
(Exchange refusing the size, for one) is Graph saying no, raised as a plain
refusal carrying Graph's status and error code, with the draft deleted; the
tenant's size limit is not guessed here. A 5xx, timeout or reset on ``/send`` is
an unknown outcome and raises :class:`MsGraphTransportError`; the draft is left
for the reconciler, because deleting a message Exchange may be sending is not a
safe guess either way.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .html_canon import html_body_sha256
from .msgraph_attachments import (
    AttachmentRefused,
    GraphFile,
    attachment_sha256,
    fits_inline,
    graph_files,
    inline_parts,
    request_fits,
)
from .msgraph_ops import TIMEOUT_S, MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import normalize_address

#: Graph refuses an upload session for a file smaller than 3 MB
#: (ErrorAttachmentSizeShouldNotBeLessThanMinimumSize), so smaller files are
#: posted to the draft's ``attachments`` collection in one request. 3 MiB is at
#: least 3 MB on either reading of "MB".
UPLOAD_SESSION_MIN_BYTES = 3 * 1024 * 1024

#: One PUT's byte range. Microsoft asks for under 4 MB per PUT; a multiple of
#: 320 KiB, the range granularity Microsoft's other upload sessions require.
CHUNK_BYTES = 12 * 320 * 1024

#: Where Graph's pre-authenticated ``uploadUrl`` may point. It carries its own
#: token and is used WITHOUT an Authorization header, so a url naming any other
#: host is refused rather than handed the file.
_UPLOAD_HOSTS = frozenset({"outlook.office.com", "outlook.office365.com", "graph.microsoft.com"})

_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.]{1,80}$")


class _HttpFailure(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"HTTP {status}{f' {code}' if code else ''}")
        self.status = status
        self.code = code


def _read_bearer(ops: MsGraphOps) -> str:
    """A token on the READ app (Mail.ReadWrite). Refuses when the seat has none:
    the draft path needs it and must never fall back to the send app."""
    if ops._read_credential_path is None:
        raise MsGraphTransportError("no msgraph read credential in the broker store; refusing to build a draft")
    return ops._bearer(ops._read_credential_path, role="read")


def _files(payload: dict[str, Any]) -> list[GraphFile]:
    """The payload's files, validated HERE, in-broker (``msgraph_attachments``).
    A malformed one refuses the whole send, with the ``attachment refused:``
    prefix the overlay's body-without-workbook rung keys on."""
    try:
        return graph_files(payload)
    except AttachmentRefused as exc:
        raise MsGraphRefused(f"attachment refused: {exc}") from exc


def transmit(
    ops: MsGraphOps,
    payload: dict[str, Any],
    message: dict[str, Any],
    *,
    save_to_sent_items: bool = True,
    allow_draft: bool = True,
) -> dict[str, str]:
    """Send ``message`` with the payload's files, inline or by draft; return the
    row's stamps (``attachment_sha256``, and ``graph_draft_id`` when drafted).

    No files: ``sendMail`` exactly as before. Files that fit one inline request,
    body included: ``sendMail`` with ``fileAttachment`` parts, as before. Anything
    larger: :func:`send_via_draft`, refused where ``allow_draft`` is off (a
    send-as carries files inline only) or the message keeps no Sent Items copy.
    """
    files = _files(payload)
    if not files:
        ops._request(ops._mail_path("sendMail"), "POST", {"message": message, "saveToSentItems": save_to_sent_items})
        return {}
    stamps = attachment_sha256(files)
    if fits_inline(files):
        body = {"message": {**message, "attachments": inline_parts(files)}, "saveToSentItems": save_to_sent_items}
        if request_fits(body):
            ops._request(ops._mail_path("sendMail"), "POST", body)
            return stamps
    if not allow_draft:
        raise MsGraphRefused("attachment refused: this send carries files only up to the inline bound")
    if not save_to_sent_items:
        raise MsGraphRefused("attachment refused: a large-file send always keeps its Sent Items copy")
    return {**stamps, "graph_draft_id": send_via_draft(ops, message, files)}


def _graph_error_code(raw: bytes) -> str:
    """Graph's ``error.code`` from an error body, only if it is a plain token."""
    try:
        code = json.loads(raw.decode("utf-8")).get("error", {}).get("code", "")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""
    return code if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code) else ""


def _http(
    ops: MsGraphOps,
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    role: str | None = "read",
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP exchange; ``role=None`` sends no Authorization (an uploadUrl)."""
    all_headers = dict(headers or {})
    if role == "read":
        all_headers["Authorization"] = f"Bearer {_read_bearer(ops)}"
    elif role == "send":
        all_headers["Authorization"] = f"Bearer {ops._bearer()}"
    request = urllib.request.Request(url, data=body, method=method, headers=all_headers)  # noqa: S310 - every url is the https graph base or an allow-listed https uploadUrl
    try:
        opener = ops._opener or urllib.request.urlopen
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with opener(request, timeout=TIMEOUT_S) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw_headers = getattr(response, "headers", None) or {}
            return status, {str(k).lower(): str(v) for k, v in dict(raw_headers).items()}, response.read()
    except urllib.error.HTTPError as exc:
        raise _HttpFailure(exc.code, _graph_error_code(exc.read() or b"")) from exc
    except Exception as exc:
        # The text is not ours to forward (it can name a host); the class is.
        raise MsGraphTransportError(f"msgraph {method} failed: {type(exc).__name__}") from exc


def _json(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise MsGraphTransportError("msgraph returned a body that is not JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


def _checked_upload_url(ops: MsGraphOps, value: Any) -> str:
    if not isinstance(value, str):
        raise MsGraphRefused("attachment refused: Graph returned no upload url")
    parts = urllib.parse.urlsplit(value)
    allowed = _UPLOAD_HOSTS | {urllib.parse.urlsplit(ops._graph_base).hostname or ""}
    if parts.scheme != "https" or (parts.hostname or "") not in allowed:
        raise MsGraphRefused("attachment refused: Graph returned an upload url on an unexpected host")
    return value


def upload_large_attachment(ops: MsGraphOps, draft_id: str, name: str, content_type: str, raw: bytes) -> str:
    """Attach ``raw`` to the draft ``draft_id`` through a Graph upload session.

    The shared helper for both Graph paths (fresh send here, the in-thread reply
    in its own module). ``raw`` must already be validated by the caller; this
    moves bytes and nothing else. Runs on the READ credential (createUploadSession
    needs ``Mail.ReadWrite``); the PUTs carry no Authorization, because the
    ``uploadUrl`` is pre-authenticated, and that url is refused unless it is
    https on a Microsoft host. Returns the new attachment's id when Graph's final
    ``Location`` names one, else ``""``. Raises :class:`MsGraphRefused` with the
    ``attachment refused:`` prefix on any failure: nothing has been sent.
    """
    if len(raw) < UPLOAD_SESSION_MIN_BYTES:
        raise MsGraphRefused("attachment refused: a file under 3 MiB goes to the attachments collection")
    session_url = ops._graph_base + ops._mail_path("messages", draft_id, "attachments", "createUploadSession")
    item = {"AttachmentItem": {"attachmentType": "file", "name": name, "size": len(raw), "contentType": content_type}}
    try:
        _status, _h, body = _http(
            ops, "POST", session_url, body=json.dumps(item).encode(), headers={"Content-Type": "application/json"}
        )
        upload_url = _checked_upload_url(ops, _json(body).get("uploadUrl"))
        location = ""
        total = len(raw)
        for start in range(0, total, CHUNK_BYTES):
            chunk = raw[start : start + CHUNK_BYTES]
            end = start + len(chunk) - 1
            status, headers, _b = _http(
                ops,
                "PUT",
                upload_url,
                body=chunk,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total}",
                },
                role=None,
            )
            if end == total - 1:
                if status != 201:
                    raise MsGraphRefused(f"attachment refused: the upload ended with HTTP {status}, not 201")
                location = headers.get("location", "")
    except _HttpFailure as exc:
        raise MsGraphRefused(f"attachment refused: the upload of {name} failed: {exc}") from exc
    found = re.search(r"Attachments\('([^']+)'\)", location)
    return found.group(1) if found else ""


def _post_small_attachment(ops: MsGraphOps, draft_id: str, name: str, content_type: str, raw: bytes) -> None:
    url = ops._graph_base + ops._mail_path("messages", draft_id, "attachments")
    part = {
        "@odata.type": _FILE_ATTACHMENT,
        "name": name,
        "contentType": content_type,
        "contentBytes": base64.b64encode(raw).decode("ascii"),
    }
    try:
        _http(ops, "POST", url, body=json.dumps(part).encode(), headers={"Content-Type": "application/json"})
    except _HttpFailure as exc:
        raise MsGraphRefused(f"attachment refused: attaching {name} failed: {exc}") from exc


def _addresses(items: Any) -> list[str]:
    out = []
    for item in items if isinstance(items, list) else []:
        address = normalize_address(item)
        if address:
            out.append(address)
    return sorted(out)


def _body_key(body: Any) -> tuple[str, str]:
    """A comparable form of a message body: the canonical html hash, or the text
    with line endings and trailing whitespace normalized."""
    if not isinstance(body, dict):
        return ("", "")
    kind = str(body.get("contentType") or "").lower()
    content = body.get("content")
    content = content if isinstance(content, str) else ""
    if kind == "html":
        return ("html", html_body_sha256(content))
    return ("text", "\n".join(line.rstrip() for line in content.replace("\r\n", "\n").split("\n")).strip())


def _expected(message: dict[str, Any], files: list[tuple[str, str, bytes]]) -> dict[str, Any]:
    return {
        "to": _addresses(message.get("toRecipients")),
        "cc": _addresses(message.get("ccRecipients")),
        "bcc": _addresses(message.get("bccRecipients")),
        "subject": str(message.get("subject") or ""),
        "body": _body_key(message.get("body")),
        "files": sorted((name, len(raw), hashlib.sha256(raw).hexdigest()) for name, _ct, raw in files),
    }


def _attachment_ids(ops: MsGraphOps, base: str) -> list[tuple[str, str]]:
    """(id, name) of every attachment on the draft, in Graph's order."""
    _s, _h, raw = _http(ops, "GET", f"{base}/attachments?$select=id,name,size")
    entries = _json(raw).get("value")
    out = []
    for entry in entries if isinstance(entries, list) else []:
        attachment_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(attachment_id, str) or not attachment_id:
            raise MsGraphRefused("attachment refused: the draft holds an attachment with no id")
        out.append((attachment_id, str(entry.get("name") or "")))
    return out


def _observed(ops: MsGraphOps, draft_id: str, text_body: bool) -> dict[str, Any]:
    """The draft as Graph holds it now, read on the read credential, files by
    their ``$value`` bytes. Raises on any failure to look: "could not tell" must
    never read as "unchanged".

    ORDERED TO SHRINK THE WINDOW. The slow part (downloading every file) runs
    first; the message fields are read after it; and the attachment ids are
    listed once more at the end. A Graph attachment cannot be edited in place
    (only deleted and added, which mints a new id), so an unchanged id list
    after the downloads means the bytes read are the bytes on the draft. What
    remains open is the one round trip from that last cheap read to ``/send``.
    """
    base = ops._graph_base + ops._mail_path("messages", draft_id)
    ids = _attachment_ids(ops, base)
    files = []
    for attachment_id, name in ids:
        value_url = ops._graph_base + ops._mail_path("messages", draft_id, "attachments", attachment_id) + "/$value"
        _s, _h, data = _http(ops, "GET", value_url)
        files.append((name, len(data), hashlib.sha256(data).hexdigest()))
    select = "subject,body,toRecipients,ccRecipients,bccRecipients,isDraft"
    headers = {"Prefer": 'outlook.body-content-type="text"'} if text_body else {}
    _s, _h, raw = _http(ops, "GET", f"{base}?$select={select}", headers=headers)
    found = _json(raw)
    if found.get("isDraft") is not True:
        raise MsGraphRefused("attachment refused: the draft is no longer a draft")
    if _attachment_ids(ops, base) != ids:
        files.append(("(the attachment list changed while it was read)", -1, ""))
    return {
        "to": _addresses(found.get("toRecipients")),
        "cc": _addresses(found.get("ccRecipients")),
        "bcc": _addresses(found.get("bccRecipients")),
        "subject": str(found.get("subject") or ""),
        "body": _body_key(found.get("body")),
        "files": sorted(files),
    }


def _delete_draft(ops: MsGraphOps, draft_id: str) -> None:
    """Best effort: a refusal is the outcome either way, and Graph's DELETE moves
    the draft to Deleted Items, so a left-over one is visible to the firm."""
    try:
        _http(ops, "DELETE", ops._graph_base + ops._mail_path("messages", draft_id))
    except (_HttpFailure, MsGraphTransportError, OSError):
        pass


def send_via_draft(
    ops: MsGraphOps,
    message: dict[str, Any],
    files: list[tuple[str, str, bytes]],
    *,
    on_draft: Callable[[str], None] | None = None,
) -> str:
    """Create ``message`` as a draft, attach ``files``, verify, send; return the draft id.

    ``message`` is the closed-field resource ``MsGraphOps._message`` built, with
    no ``attachments``; ``files`` are the validated (name, content type, bytes).
    The recipient fence has already run on the caller's side.
    """
    create_url = ops._graph_base + ops._mail_path("messages")
    try:
        _s, _h, raw = _http(
            ops, "POST", create_url, body=json.dumps(message).encode(), headers={"Content-Type": "application/json"}
        )
    except (_HttpFailure, MsGraphTransportError) as exc:
        # Nothing exists to send yet, so even a lost answer is "not sent". A
        # draft Graph made anyway sits in Drafts, where nothing will send it.
        raise MsGraphRefused(f"attachment refused: the draft could not be created: {exc}") from exc
    draft_id = _json(raw).get("id")
    if not isinstance(draft_id, str) or not draft_id:
        raise MsGraphRefused("attachment refused: Graph created a draft with no id")
    if on_draft is not None:
        on_draft(draft_id)
    try:
        for name, content_type, data in files:
            if len(data) >= UPLOAD_SESSION_MIN_BYTES:
                upload_large_attachment(ops, draft_id, name, content_type, data)
            else:
                _post_small_attachment(ops, draft_id, name, content_type, data)
        text_body = _body_key(message.get("body"))[0] == "text"
        observed = _observed(ops, draft_id, text_body)
        expected = _expected(message, files)
        changed = sorted(k for k in expected if expected[k] != observed[k])
        if changed:
            raise MsGraphRefused(
                f"attachment refused: the draft changed before it was sent ({', '.join(changed)}); not sent"
            )
    except MsGraphRefused:
        _delete_draft(ops, draft_id)
        raise
    except (_HttpFailure, MsGraphTransportError) as exc:
        # Before /send every failure is a definite "not sent".
        _delete_draft(ops, draft_id)
        raise MsGraphRefused(f"attachment refused: the draft could not be finished or re-read: {exc}") from exc
    send_url = ops._graph_base + ops._mail_path("messages", draft_id, "send")
    try:
        _http(ops, "POST", send_url, body=b"", headers={"Content-Length": "0"}, role="send")
    except _HttpFailure as exc:
        if 400 <= exc.status < 500 and exc.status not in (408, 429):
            _delete_draft(ops, draft_id)
            raise MsGraphRefused(f"Graph refused the send: {exc}") from exc
        # A 5xx, 408 or 429 on /send: Graph may or may not have acted. Unknown
        # outcome; the draft is left for the reconciler. A non-HTTP failure
        # reaches the caller from _http as MsGraphTransportError already.
        raise MsGraphTransportError(f"msgraph POST send failed: {exc}") from exc
    return draft_id


__all__ = ["CHUNK_BYTES", "UPLOAD_SESSION_MIN_BYTES", "send_via_draft", "transmit", "upload_large_attachment"]
