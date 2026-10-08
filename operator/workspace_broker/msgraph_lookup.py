"""Mailbox reads the verified reply binding needs (reply_binding.py).

Kept beside ``MsGraphOps`` rather than in it (that module is at the size
ceiling). Every read here is on the READ credential, and every one RAISES on a
failure to look: "could not look" must never read as "not there" or "not
answered", because either reading would let a reply through that should not go.

WHAT COUNTS AS AN EMAIL A REPLY MAY BIND TO (security review, 2026-10-06):

* it is in the INBOX (``parentFolderId`` equals the Inbox's id). ``/messages``
  spans every folder, so without this a draft, a sent item, an outbox item,
  junk, or a deleted message could be "answered";
* it is not a draft;
* its ``replyTo`` is empty or names exactly its sender. Graph's ``/reply`` sends
  to ``replyTo`` when it is set, not to ``from``, so a message whose replyTo
  names someone else would carry the reply to an address nobody verified;
* its sender is not this mailbox itself.
"""

from __future__ import annotations

import urllib.parse

from .msgraph_ops import SENT_ITEMS_FOLDER, MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import normalize_address

_SELECT = "id,from,sender,replyTo,conversationId,receivedDateTime,isDraft,internetMessageId,parentFolderId"
#: The participant fence's own projection (participant_fence.py). Kept apart
#: from ``_SELECT`` on purpose: widening the binding's projection would hand
#: every caller of it two address lists it never asked for.
_PARTICIPANT_SELECT = "id,from,sender,toRecipients,ccRecipients,conversationId,isDraft,internetMessageId"
_MAX_SENT_PAGES = 5


def _read(ops: MsGraphOps, path: str) -> dict:
    read = ops._read_credential_path
    if read is None:
        raise MsGraphTransportError("no msgraph read credential; cannot look up a message to reply to")
    return ops._request(path, "GET", None, credential_path=read, role="read")


def _bracketed(imid: str) -> str:
    return f"<{imid.strip().strip('<>')}>"


def _quoted(value: str) -> str:
    return value.replace("'", "''")


def inbox_id(ops: MsGraphOps) -> str:
    """The Inbox folder's id in this mailbox. Raises when it cannot be read."""
    found = _read(ops, ops._mail_path("mailFolders", "inbox") + "?$select=id")
    value = str(found.get("id") or "") if isinstance(found, dict) else ""
    if not value:
        raise MsGraphTransportError("could not read this mailbox's Inbox id")
    return value


def _vetted(ops: MsGraphOps, m: dict, inbox: str) -> dict[str, str] | None:
    """The message as a reply target, None when it is not one, or a refusal."""
    if m.get("isDraft") is True or str(m.get("parentFolderId") or "") != inbox:
        return None
    sender = normalize_address(m.get("from") or m.get("sender"))
    imid = str(m.get("internetMessageId") or "").strip()
    if not sender or not imid:
        raise MsGraphRefused("that message names no sender or carries no internet message id; refusing")
    if sender == normalize_address(ops.mailbox()):
        raise MsGraphRefused("that message was sent by this mailbox itself; refusing")
    reply_to = m.get("replyTo") or []
    others = {normalize_address(r) for r in (reply_to if isinstance(reply_to, list) else [reply_to]) if r} - {sender}
    if others:
        raise MsGraphRefused(
            "that message asks for replies to go to an address other than its sender (replyTo), "
            "and a reply to it would reach that address; refusing"
        )
    return {
        "graph_message_id": str(m.get("id") or ""),
        "sender": sender,
        "conversation_id": str(m.get("conversationId") or ""),
        "received_at": str(m.get("receivedDateTime") or ""),
        "internet_message_id": _bracketed(imid),
    }


def find_received(ops: MsGraphOps, internet_message_id: str) -> dict[str, str] | None:
    """The one Inbox message with this RFC 5322 id, or None."""
    if not str(internet_message_id or "").strip():
        raise MsGraphRefused("no internet message id to look up")
    wanted = _bracketed(internet_message_id)
    inbox = inbox_id(ops)
    path = (
        ops._mail_path("messages")
        + f"?$select={_SELECT}&$top=10&$filter="
        + urllib.parse.quote(f"internetMessageId eq '{_quoted(wanted)}'", safe="")
    )
    value = _read(ops, path).get("value")
    found = [
        v
        for m in (value if isinstance(value, list) else [])
        if isinstance(m, dict) and _bracketed(str(m.get("internetMessageId") or "")) == wanted
        for v in [_vetted(ops, m, inbox)]
        if v is not None
    ]
    if not found:
        return None
    if len({f["sender"] for f in found}) != 1:
        raise MsGraphRefused("the Inbox copies of that message disagree on who sent it; refusing")
    return found[0]


def received_by_graph_id(ops: MsGraphOps, graph_message_id: str) -> dict[str, str] | None:
    """``find_received`` for a mailbox-local Graph id (what a turn is handed).

    The answer's id must be the id asked for: a lookup that resolved to some
    other message is refused rather than answered.
    """
    wanted = str(graph_message_id or "").strip()
    if not wanted:
        raise MsGraphRefused("no message id to look up")
    inbox = inbox_id(ops)
    try:
        m = _read(ops, ops._mail_path("messages", wanted) + f"?$select={_SELECT}")
    except MsGraphTransportError as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise
    if not isinstance(m, dict):
        return None
    if str(m.get("id") or "") != wanted:
        raise MsGraphRefused("the mailbox answered for a different message than the one named; refusing")
    return _vetted(ops, m, inbox)


def _address_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    return [a for a in (normalize_address(v) for v in items) if a]


def participants_of(ops: MsGraphOps, graph_message_id: str) -> dict[str, object]:
    """Who sent the email with this Graph id, and whom it reached (To and Cc).

    The participant fence's read (participant_fence.py). RAISES on any failure
    to look, a 404 included, and refuses a draft, an email this mailbox sent,
    or an answer for a different message: the fence reads every one of those as
    "the request could not be verified" and sends nothing.
    """
    wanted = str(graph_message_id or "").strip()
    if not wanted:
        raise MsGraphRefused("no message id to read participants from")
    m = _read(ops, ops._mail_path("messages", wanted) + f"?$select={_PARTICIPANT_SELECT}")
    if not isinstance(m, dict) or str(m.get("id") or "") != wanted:
        raise MsGraphRefused("the mailbox answered for a different message than the one named; refusing")
    if m.get("isDraft") is True:
        raise MsGraphRefused("that message is a draft, not a request anyone sent")
    sender = normalize_address(m.get("from") or m.get("sender"))
    if not sender:
        raise MsGraphRefused("that message names no sender")
    if sender == normalize_address(ops.mailbox()):
        raise MsGraphRefused("that message was sent by this mailbox itself, not to it")
    return {
        "graph_message_id": wanted,
        "sender": sender,
        "to": _address_list(m.get("toRecipients")),
        "cc": _address_list(m.get("ccRecipients")),
        "conversation_id": str(m.get("conversationId") or ""),
    }


def sent_in_conversation_since(ops: MsGraphOps, conversation_id: str, since: str) -> bool:
    """Whether this mailbox SENT anything in this conversation at or after ``since``.

    Read from Sent Items, filtered server-side on the time (newest first, as
    Graph requires the ordered property to lead the filter) and on the
    conversation, and paged, so a long thread cannot hide its newest reply past
    the first page. A reply by any path (the inbound turn, a released hold, a
    person in Outlook) counts. An unknown conversation or receipt time is
    treated as answered: the caller refuses rather than risk a second reply.
    """
    if not conversation_id or not since:
        return True
    path = (
        ops._mail_path("mailFolders", SENT_ITEMS_FOLDER, "messages")
        + "?$select=id,sentDateTime,conversationId&$top=50&$orderby="
        + urllib.parse.quote("sentDateTime desc", safe="")
        + "&$filter="
        + urllib.parse.quote(f"sentDateTime ge {since} and conversationId eq '{_quoted(conversation_id)}'", safe="")
    )
    for _page in range(_MAX_SENT_PAGES):
        page = _read(ops, path)
        value = page.get("value")
        for m in value if isinstance(value, list) else []:
            if not isinstance(m, dict) or str(m.get("conversationId") or "") != conversation_id:
                continue
            sent = str(m.get("sentDateTime") or "")
            if not sent or sent >= since:
                return True
        nxt = page.get("@odata.nextLink")
        if not (isinstance(nxt, str) and nxt):
            return False
        if not nxt.startswith(ops._graph_base + "/"):
            return True  # a next page we will not follow is not evidence of absence
        path = nxt[len(ops._graph_base) :]
    # More pages than we read: refuse rather than assume the rest are empty.
    return True


__all__ = ["find_received", "inbox_id", "participants_of", "received_by_graph_id", "sent_in_conversation_since"]
