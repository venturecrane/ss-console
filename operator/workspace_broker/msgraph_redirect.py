"""The DEVICE redirect of a Graph reply: answer a scanner's email to a person.

A seat's office scanner emails scans to the operator mailbox from an address on
the firm's own domain. A reply Graph derives from that message goes back to the
scanner, where nobody reads it. ``scope.device_senders`` names the person who
answers for the device, and this module sends the reply to that person instead.
``MsGraphOps.reply`` decides WHETHER a redirect is allowed (the fetched sender,
the authored pairing, the admins); this module only carries it out.

TWO WIRE PATHS, AND WHY THE DEFAULT IS THE LONGER ONE.

* ``create_reply`` (default): ``POST /messages/{id}/createReply`` builds the
  reply draft in the thread, ``PATCH /messages/{draft}`` REPLACES its
  ``toRecipients`` (and empties cc/bcc), a ``GET`` reads the draft back and
  refuses unless its recipients are exactly the one person, and
  ``POST /messages/{draft}/send`` sends it. A PATCH of a collection property
  sets it; it cannot add. And the read-back means the address that leaves is
  observed, not assumed.
* ``reply``: one ``POST /messages/{id}/reply`` with ``message.toRecipients``.
  Graph's reference for ``message: reply`` describes its own example as one
  that "includes a comment and adds a recipient to the reply message", and says
  nowhere whether ``toRecipients`` there replaces the derived recipient (the
  source sender) or joins it. Joining would still deliver to the scanner. That
  is harmless to the firm, but it is not the behaviour this key promises, and it
  cannot be read back before it happens. So this path exists, behind
  ``SMD_MSGRAPH_DEVICE_REPLY_MODE=reply``, for the day a live send settles it.

The create path writes the draft on the READ credential (``Mail.ReadWrite``;
the send app holds ``Mail.Send`` only, overlay#280) and sends it on the SEND
credential. A draft is deleted again whenever the path fails at a point where
Graph provably sent nothing, so a failure does not strand a half-written reply
in the firm's Drafts folder. Scope honesty, as in ``reply``: the read credential
is the one the agent also holds, so between the read-back and the send an agent
could in principle PATCH the draft; the window is one request long, and it is
the same pre-existing property as replying to a mutable message object.
"""

from __future__ import annotations

import os
from typing import Any

MODE_ENV = "SMD_MSGRAPH_DEVICE_REPLY_MODE"
MODE_CREATE_REPLY = "create_reply"
MODE_REPLY = "reply"


def redirect_mode() -> str:
    """The configured wire path; anything unrecognised is the default."""
    value = os.environ.get(MODE_ENV, "").strip().lower()
    return MODE_REPLY if value == MODE_REPLY else MODE_CREATE_REPLY


def send_redirected(
    ops: Any,
    message_id: str,
    to: str,
    comment: str,
    html: str,
    audit_token: str,
    *,
    transport_error: type[Exception],
    refused: type[Exception],
) -> bool:
    """Send the reply to ``message_id`` to ``to``. True iff the audit header rode.

    ``ops`` is the ``MsGraphOps`` whose transport, mailbox and credentials are
    used; the two exception types are its own, passed in so this module does
    not import it back.
    """
    if redirect_mode() == MODE_REPLY:
        return _via_reply(ops, message_id, to, comment, html, audit_token, transport_error)
    return _via_create_reply(ops, message_id, to, comment, html, audit_token, transport_error, refused)


def _to_recipients(to: str) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": to}}]


def _with_recipient(body: dict[str, Any], to: str) -> dict[str, Any]:
    """``_reply_body``'s output with ``message.toRecipients`` set to ``to`` alone."""
    message = dict(body.get("message") or {})
    message["toRecipients"] = _to_recipients(to)
    return {**body, "message": message}


def _via_reply(
    ops: Any,
    message_id: str,
    to: str,
    comment: str,
    html: str,
    audit_token: str,
    transport_error: type[Exception],
) -> bool:
    path = ops._mail_path("messages", message_id, "reply")
    try:
        ops._request(path, "POST", _with_recipient(ops._reply_body(comment, html, audit_token), to))
        return True
    except transport_error as exc:
        # Same single retry as the ordinary reply: only a 400 proves nothing
        # was sent, so only a 400 may be re-shaped (unstamped) and repeated.
        if getattr(exc, "status", None) != 400:
            raise
    ops._request(path, "POST", _with_recipient(ops._reply_body(comment, html, ""), to))
    return False


def _via_create_reply(
    ops: Any,
    message_id: str,
    to: str,
    comment: str,
    html: str,
    audit_token: str,
    transport_error: type[Exception],
    refused: type[Exception],
) -> bool:
    draft_id, stamped = _create_draft(ops, message_id, comment, html, audit_token, transport_error)
    try:
        _address_draft(ops, draft_id, to, refused)
    except Exception:
        _discard(ops, draft_id)
        raise
    try:
        ops._request(ops._mail_path("messages", draft_id, "send"), "POST", None)
    except transport_error as exc:
        # A 4xx is Graph refusing the send outright: nothing left, so the draft
        # is removed. Anything else (5xx, timeout) may have sent, and deleting
        # then could remove the firm's only record of what went out.
        status = getattr(exc, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            _discard(ops, draft_id)
        raise
    return stamped


def _read(ops: Any) -> dict[str, Any]:
    return {"credential_path": ops._read_credential_path, "role": "read"}


def _create_draft(
    ops: Any,
    message_id: str,
    comment: str,
    html: str,
    audit_token: str,
    transport_error: type[Exception],
) -> tuple[str, bool]:
    """``createReply`` in the source thread; returns ``(draft_id, stamped)``."""
    path = ops._mail_path("messages", message_id, "createReply")
    stamped = True
    try:
        draft = ops._request(path, "POST", ops._reply_body(comment, html, audit_token), **_read(ops))
    except transport_error as exc:
        if getattr(exc, "status", None) != 400:
            raise
        stamped = False
        draft = ops._request(path, "POST", ops._reply_body(comment, html, ""), **_read(ops))
    draft_id = str(draft.get("id") or "")
    if not draft_id:
        raise transport_error("createReply returned no draft id; nothing was sent")
    return draft_id, stamped


def _address_draft(ops: Any, draft_id: str, to: str, refused: type[Exception]) -> None:
    """REPLACE the draft's recipients with ``to`` alone, then read them back."""
    ops._request(
        ops._mail_path("messages", draft_id),
        "PATCH",
        {"toRecipients": _to_recipients(to), "ccRecipients": [], "bccRecipients": []},
        **_read(ops),
    )
    readback = ops._request(
        ops._mail_path("messages", draft_id) + "?$select=toRecipients,ccRecipients,bccRecipients",
        "GET",
        None,
        **_read(ops),
    )
    found = {
        field: sorted(_addresses(readback.get(field))) for field in ("toRecipients", "ccRecipients", "bccRecipients")
    }
    if found != {"toRecipients": [to], "ccRecipients": [], "bccRecipients": []}:
        raise refused(
            "the redirected reply draft did not read back as addressed to the one authored person; refusing to send it"
        )


def _addresses(value: Any) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        email = item.get("emailAddress") if isinstance(item, dict) else None
        address = email.get("address") if isinstance(email, dict) else None
        if isinstance(address, str) and address.strip():
            out.append(address.strip().lower())
    return out


def _discard(ops: Any, draft_id: str) -> None:
    """Best-effort delete of an unsent draft; a failure here is not the error."""
    try:
        ops._request(ops._mail_path("messages", draft_id), "DELETE", None, **_read(ops))
    except Exception:  # noqa: BLE001 - the caller is already raising the real failure
        pass


__all__ = ["MODE_CREATE_REPLY", "MODE_ENV", "MODE_REPLY", "redirect_mode", "send_redirected"]
