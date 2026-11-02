"""A Graph reply that carries a firm's own matter files back to its own staff.

WHY THIS EXISTS (ADR 0101, the 2026-11-01 ashton-price miss). An attorney
asked the Operator for named records on a matter, attached to its reply. The
reply verb (``msgraph_ops.reply``) carried no files at all. This module is the
reply verb's file path, and it is narrower than a send on purpose:

* **Authored or nothing.** The seat must author
  ``matter_file_delivery.recipients`` (addresses or ``@domains``). Unauthored,
  a reply with files is refused.
* **The firm's own staff only.** The fetched sender of the source message must
  be on that list, and on ``scope.inbound_allow_from`` (the reply fence
  ``msgraph_ops`` already applies), and not typed as anything but
  ``firm_staff`` in ``scope.outbound_roster``. A device redirect is refused.
  A copied participant of the request goes on the reply only if the same three
  hold for them; anyone else is left off.
* **Bytes the broker hashed.** Each file is named by a handle into the broker's
  own spool (``attachment_intake``) and the sha256 the caller expects; the
  broker reads its copy and refuses unless its own hash is that one.
* **Read back before send.** The reply is built as a ``createReply`` draft on
  the READ credential, files attached (inline under 3 MB, a Graph upload
  session above, ``msgraph_upload.upload_large_attachment``), then the draft is
  re-read: recipients exactly the sender plus the kept copies, no bcc, the
  body's words present, and every attachment's name, type and sha256 exactly
  what was vetted (the bytes are fetched back and hashed). Any mismatch deletes
  the draft and refuses. Only then is it sent, on the SEND credential.

Scope honesty, as in ``msgraph_redirect``: the read credential is the one the
agent also holds, so between the read-back and the send an agent could in
principle PATCH the draft. The window is one request long.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import agentmail_attachments, attachment_intake
from .msgraph_redirect import _addresses, _create_draft, _discard, _read
from .recipient_policy import RecipientPolicy, normalize_address

CONFIG_BLOCK = "matter_file_delivery"
PDF_CONTENT_TYPE = "application/pdf"
MAX_FILES = 3
MAX_TOTAL_BYTES = 25 * 1024 * 1024
#: Graph's single-request attachment bound ("under 3 MB"); above it, an upload session.
INLINE_MAX_BYTES = 3 * 1000 * 1000
_KEYS = frozenset({"name", "content_type", "handle", "sha256"})
_NAME_RE = re.compile(r"^[\w .()-]{1,120}\.pdf$")
_STREAM_RE = re.compile(rb"stream\r?\n.*?endstream", re.DOTALL)


class FilesRefused(ValueError):
    """The files cannot go on this reply. ``msgraph_ops`` re-raises as a refusal."""


def _config(customer_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def staff_list(customer_path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """``matter_file_delivery.recipients`` as (exact addresses, domains)."""
    block = _config(customer_path).get(CONFIG_BLOCK)
    raw = block.get("recipients") if isinstance(block, dict) else None
    exact: set[str] = set()
    domains: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        text = str(entry or "").strip().lower()
        if text.startswith("@") and "." in text:
            domains.add(text[1:])
        elif "@" in text:
            exact.add(normalize_address(text))
    return frozenset(exact), frozenset(domains)


def _typed_non_staff(customer_path: Path) -> frozenset[str]:
    """Addresses the outbound roster types as anything but firm staff."""
    scope = _config(customer_path).get("scope") or {}
    out = set()
    for entry in scope.get("outbound_roster") or [] if isinstance(scope, dict) else []:
        if isinstance(entry, dict) and entry.get("class") != "firm_staff":
            out.add(normalize_address(entry.get("address")))
    return frozenset(out)


def may_receive(customer_path: Path, policy: RecipientPolicy, address: str) -> bool:
    """Is ``address`` firm staff this seat may send matter files to?"""
    value = normalize_address(address)
    exact, domains = staff_list(customer_path)
    on_list = value in exact or value.rsplit("@", 1)[-1] in domains
    return bool(value) and on_list and policy.allows_reply_to(value) and value not in _typed_non_staff(customer_path)


def _active_outside_streams(raw: bytes) -> bool:
    """The byte-level second wall, outside stream data only: a scanned page's
    image bytes are arbitrary, and would match ``/JS`` by chance."""
    return bool(agentmail_attachments._ACTIVE_RE.search(_STREAM_RE.sub(b"stream endstream", raw)))


def vet(customer_path: Path, spool: Path, payload: dict[str, Any]) -> list[tuple[str, bytes, str]]:
    """``[(name, bytes, sha256)]`` for the reply's files, or :class:`FilesRefused`."""
    if not staff_list(customer_path)[0] and not staff_list(customer_path)[1]:
        raise FilesRefused("this seat does not send matter files (matter_file_delivery is not authored)")
    entries = payload.get("attachments")
    if not isinstance(entries, list) or not entries:
        raise FilesRefused("attachments must be a non-empty list")
    if len(entries) > MAX_FILES:
        raise FilesRefused(f"at most {MAX_FILES} files per reply")
    out: list[tuple[str, bytes, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _KEYS:
            raise FilesRefused("each file carries exactly name, content_type, handle, sha256")
        name, ctype, handle, sha = (entry[k] for k in ("name", "content_type", "handle", "sha256"))
        if ctype != PDF_CONTENT_TYPE or not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise FilesRefused(f"only plainly named PDFs go on a reply: {name!r}")
        try:
            raw = attachment_intake.read_handle(spool, handle, sha)
        except attachment_intake.IntakeRefused as exc:
            raise FilesRefused(str(exc)) from exc
        if not raw.startswith(b"%PDF-"):
            raise FilesRefused(f"{name} is not a PDF file")
        if b"/Encrypt" in raw:
            raise FilesRefused(f"{name} is encrypted")
        if _active_outside_streams(raw):
            raise FilesRefused(f"{name} names scripts, actions or embedded files")
        out.append((name, raw, sha))
    if sum(len(raw) for _, raw, _ in out) > MAX_TOTAL_BYTES:
        raise FilesRefused(f"the files together are over {MAX_TOTAL_BYTES // 1048576} MiB")
    if len({n.lower() for n, _, _ in out}) != len(out):
        raise FilesRefused("two files share a name")
    return out


def people(customer_path: Path, policy: RecipientPolicy, sender: str, redirect: str, cc: list[str]) -> list[str]:
    """The copies a files reply keeps, after refusing a sender who is not staff."""
    if redirect:
        raise FilesRefused("matter files never go to a device's stand-in; reply to the person who asked")
    if not may_receive(customer_path, policy, sender):
        raise FilesRefused("matter files go only to the firm's own staff named in matter_file_delivery")
    return [a for a in cc if may_receive(customer_path, policy, a)]


def _attach(ops: Any, draft_id: str, name: str, raw: bytes) -> None:
    import base64

    if len(raw) < INLINE_MAX_BYTES:
        body = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": PDF_CONTENT_TYPE,
            "contentBytes": base64.b64encode(raw).decode("ascii"),
        }
        ops._request(ops._mail_path("messages", draft_id, "attachments"), "POST", body, **_read(ops))
        return
    try:
        from .msgraph_upload import upload_large_attachment
    except ImportError as exc:
        raise FilesRefused(f"{name} is over 3 MB and this broker has no Graph upload session yet") from exc
    upload_large_attachment(ops, draft_id, name, PDF_CONTENT_TYPE, raw)


def _raw_get(ops: Any, path: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - graph_base is https-checked by MsGraphOps; the path is built here
        ops._graph_base + path,
        method="GET",
        headers={"Authorization": f"Bearer {ops._bearer(ops._read_credential_path, role='read')}"},
    )
    opener = ops._opener or urllib.request.urlopen
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with opener(request, timeout=60) as response:
        return response.read()


def _words(text: str) -> str:
    """The words of a body, markup and punctuation gone: Exchange re-renders a
    stored body (entities, spacing), never its words."""
    plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or "")).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", plain).split())


def _verify(
    ops: Any, draft_id: str, sender: str, cc: list[str], words: str, files: list[tuple[str, bytes, str]]
) -> None:
    """Re-read the draft; refuse unless it is exactly what was vetted."""
    base = ops._mail_path("messages", draft_id)
    draft = ops._request(base + "?$select=toRecipients,ccRecipients,bccRecipients,body", "GET", None, **_read(ops))
    if sorted(_addresses(draft.get("toRecipients"))) != [sender]:
        raise FilesRefused("the reply draft did not read back addressed to the person who asked")
    if sorted(_addresses(draft.get("ccRecipients"))) != sorted(cc) or _addresses(draft.get("bccRecipients")):
        raise FilesRefused("the reply draft did not read back with exactly the vetted copies")
    stored = _words(str((draft.get("body") or {}).get("content") or ""))
    if words and words not in stored:
        raise FilesRefused("the reply draft did not read back carrying the vetted words")
    listing = ops._request(base + "/attachments?$select=id,name,contentType", "GET", None, **_read(ops))
    found = {}
    for item in listing.get("value") or []:
        if isinstance(item, dict):
            got = _raw_get(ops, base + f"/attachments/{item.get('id')}/$value")
            found[str(item.get("name"))] = (str(item.get("contentType") or ""), hashlib.sha256(got).hexdigest())
    want = {name: (PDF_CONTENT_TYPE, sha) for name, _, sha in files}
    if found != want:
        raise FilesRefused("the reply draft's files did not read back exactly as vetted")


def send(
    ops: Any,
    message_id: str,
    comment: str,
    html: str,
    audit_token: str,
    sender: str,
    cc: list[str],
    files: list[tuple[str, bytes, str]],
    *,
    transport_error: type[Exception],
) -> bool:
    """Build, verify and send the reply with ``files``. True iff the audit header rode."""
    draft_id, stamped = _create_draft(ops, message_id, comment, html, audit_token, transport_error)
    try:
        ops._request(
            ops._mail_path("messages", draft_id),
            "PATCH",
            {"ccRecipients": [{"emailAddress": {"address": a}} for a in cc], "bccRecipients": []},
            **_read(ops),
        )
        for name, raw, _sha in files:
            _attach(ops, draft_id, name, raw)
        _verify(ops, draft_id, sender, cc, _words(html or comment), files)
    except Exception:
        _discard(ops, draft_id)
        raise
    try:
        ops._request(ops._mail_path("messages", draft_id, "send"), "POST", None)
    except transport_error as exc:
        status = getattr(exc, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            _discard(ops, draft_id)
        raise
    return stamped


def discard_spooled(spool: Path, payload: dict[str, Any]) -> None:
    """The reply is decided: drop its handles from the broker's spool."""
    for entry in payload.get("attachments") or []:
        if isinstance(entry, dict):
            attachment_intake.discard(spool, entry.get("handle"))


__all__ = ["CONFIG_BLOCK", "FilesRefused", "MAX_FILES", "may_receive", "people", "send", "staff_list", "vet"]
