"""Mailbox reads the verified reply binding needs (reply_binding.py).

Kept beside ``MsGraphOps`` rather than in it (that module is at the size
ceiling). Every read here is on the READ credential, and every one RAISES on a
failure to look: "could not look" must never read as "not there" or "not
answered", because either reading would let a reply through that should not go.
"""

from __future__ import annotations

import urllib.parse

from .msgraph_ops import SENT_ITEMS_FOLDER, MsGraphOps, MsGraphRefused, MsGraphTransportError
from .recipient_policy import normalize_address

_SELECT = "id,from,sender,conversationId,receivedDateTime,isDraft,internetMessageId"


def _read(ops: MsGraphOps, path: str) -> dict:
    read = ops._read_credential_path
    if read is None:
        raise MsGraphTransportError("no msgraph read credential; cannot look up a message to reply to")
    return ops._request(path, "GET", None, credential_path=read, role="read")


def _bracketed(imid: str) -> str:
    return f"<{imid.strip().strip('<>')}>"


def find_received(ops: MsGraphOps, internet_message_id: str) -> dict[str, str] | None:
    """The RECEIVED message in this mailbox with this RFC 5322 id, or None.

    A draft is never a match: anything holding ``Mail.ReadWrite`` can create a
    message with any ``from`` it likes, and it lands as a draft. Copies of one
    message in two folders must agree on the sender or the lookup refuses.
    """
    if not str(internet_message_id or "").strip():
        raise MsGraphRefused("no internet message id to look up")
    wanted = _bracketed(internet_message_id)
    path = (
        ops._mail_path("messages")
        + f"?$select={_SELECT}&$top=10&$filter="
        + urllib.parse.quote(f"internetMessageId eq '{wanted.replace(chr(39), chr(39) * 2)}'", safe="")
    )
    value = _read(ops, path).get("value")
    matches = [
        m
        for m in (value if isinstance(value, list) else [])
        if isinstance(m, dict)
        and str(m.get("internetMessageId") or "").strip() == wanted
        and m.get("isDraft") is not True
    ]
    if not matches:
        return None
    senders = {normalize_address(m.get("from") or m.get("sender")) for m in matches}
    if len(senders) != 1 or not next(iter(senders)):
        raise MsGraphRefused("the copies of that message disagree on who sent it, or name no one; refusing")
    first = matches[0]
    return {
        "graph_message_id": str(first.get("id") or ""),
        "sender": next(iter(senders)),
        "conversation_id": str(first.get("conversationId") or ""),
        "received_at": str(first.get("receivedDateTime") or ""),
        "internet_message_id": wanted,
    }


def received_by_graph_id(ops: MsGraphOps, graph_message_id: str) -> dict[str, str] | None:
    """``find_received`` for a mailbox-local Graph id (what a turn is handed).

    Same answer shape and refusals; a 404 is the only None besides a draft.
    """
    wanted = str(graph_message_id or "").strip()
    if not wanted:
        raise MsGraphRefused("no message id to look up")
    try:
        m = _read(ops, ops._mail_path("messages", wanted) + f"?$select={_SELECT}")
    except MsGraphTransportError as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise
    if not isinstance(m, dict) or m.get("isDraft") is True:
        return None
    sender = normalize_address(m.get("from") or m.get("sender"))
    imid = str(m.get("internetMessageId") or "").strip()
    if not sender or not imid:
        raise MsGraphRefused("that message names no sender or carries no internet message id; refusing")
    return {
        "graph_message_id": str(m.get("id") or wanted),
        "sender": sender,
        "conversation_id": str(m.get("conversationId") or ""),
        "received_at": str(m.get("receivedDateTime") or ""),
        "internet_message_id": _bracketed(imid),
    }


def sent_in_conversation_since(ops: MsGraphOps, conversation_id: str, since: str) -> bool:
    """Whether this mailbox SENT anything in this conversation at or after ``since``.

    Read from Sent Items, so a reply by any path (the inbound turn, a released
    hold, a person in Outlook) counts. An unknown conversation or receipt time
    is treated as answered: the caller refuses rather than risk a second reply.
    """
    if not conversation_id or not since:
        return True
    path = (
        ops._mail_path("mailFolders", SENT_ITEMS_FOLDER, "messages")
        + "?$select=id,sentDateTime,conversationId&$top=50&$filter="
        + urllib.parse.quote(f"conversationId eq '{conversation_id.replace(chr(39), chr(39) * 2)}'", safe="")
    )
    value = _read(ops, path).get("value")
    for m in value if isinstance(value, list) else []:
        if not isinstance(m, dict) or str(m.get("conversationId") or "") != conversation_id:
            continue
        sent = str(m.get("sentDateTime") or "")
        # ISO-8601 UTC strings from one server compare in time order; a sent
        # item with no time is counted, never skipped.
        if not sent or sent >= since:
            return True
    return False


__all__ = ["find_received", "received_by_graph_id", "sent_in_conversation_since"]
