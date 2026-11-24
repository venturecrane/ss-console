"""What this mailbox already said in a thread, for the turn about to answer in it.

WHY (A&P, 2026-11-23). An attorney asked for a set of special interrogatories to
be cut to "120125" (120 to 125). SMD did the trim, and SMD's note told him in
the thread that each set was cut to 120. His next email, "120-125", opened a
turn that could not see that note, and its reply said nothing had been changed
and asked him to choose again: it contradicted a delivery already in his
thread. The turn reads only the email it answers; what the Operator itself sent
in the thread (its in-turn replies, SMD notes, job deliveries, SMD's hand
replies) and what was filed for it were invisible to it.

``thread_recent`` (gateway only) answers, for the turn's own email:

* ``sent``: the newest :data:`MAX_SENT` messages this mailbox sent in that
  conversation within :data:`WINDOW_DAYS` TO the email's sender (To or Cc; a
  forward or a reply to someone else is not something said to them), oldest
  first, each its sent time, its recipients, the start of its OWN text (Graph's
  ``bodyPreview`` cut at the first quoted-history marker, then to
  :data:`PREVIEW_CHARS`) and the names of its attachments;
* ``filed``: the drafting jobs delivered for a request email in that
  conversation within the window, each its time, document class, matter number
  and filed file names (``DraftingLedger.delivered_for_refs``).

Each section is read on its own: a failed read drops that section (``null``,
with its ``*_error``), never the other. The gateway calls this under a short
client timeout, so it costs at most four Graph reads (the Inbox id is cached,
attachment names ride the sent query as ``$expand``).

The overlay renders them as the turn's context (``shared/thread_recent.py``).
File names and times only, never a file's content. The email must be a received
message in this mailbox's Inbox (``msgraph_lookup.received_by_graph_id``), so
the gateway reads only a thread a firm email opened.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from . import msgraph_lookup
from .broker_context import BrokerContext
from .msgraph_ops import SENT_ITEMS_FOLDER, MsGraphRefused
from .recipient_policy import normalize_address

logger = logging.getLogger(__name__)

VERBS: tuple[str, ...] = ("thread_recent",)
MAX_SENT = 5
PREVIEW_CHARS = 240
WINDOW_DAYS = 14
MAX_ATTACHMENTS = 10
MAX_REFS = 50
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")
#: Where a message's own words end and the quoted email below them begins.
#: "On <date> <name> wrote:" is matched case-sensitively, so a sentence of the
#: message's own ("the rogs on matter 900417 ...") never cuts it.
_QUOTED_HISTORY_RE = re.compile(
    r"(?:(?i:\bfrom:\s|\bsent:\s|-{2,}\s*original message)|\bOn\s[^\n]{0,200}?\bwrote:|(?:^|\s)>)"
)


def _refusal(reason: str) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason}


def own_words(preview: str) -> str:
    """A message preview cut at its first quoted-history marker, one line."""
    text = str(preview or "")
    cut = _QUOTED_HISTORY_RE.search(text)
    if cut:
        text = text[: cut.start()]
    return " ".join(text.split())[:PREVIEW_CHARS]


def _addresses(m: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("toRecipients", "ccRecipients"):
        rows = m.get(key)
        for r in rows if isinstance(rows, list) else []:
            address = normalize_address(r) if isinstance(r, dict) else ""
            if address and address not in out:
                out.append(address)
    return out


def _sent(ops: Any, conversation_id: str, since: str, sender: str) -> list[dict[str, Any]]:
    path = (
        ops._mail_path("mailFolders", SENT_ITEMS_FOLDER, "messages")
        + "?$select=id,sentDateTime,conversationId,bodyPreview,toRecipients,ccRecipients"
        + f"&$expand=attachments($select=name)&$top=25&$orderby="
        + urllib.parse.quote("sentDateTime desc", safe="")
        + "&$filter="
        + urllib.parse.quote(
            f"sentDateTime ge {since} and conversationId eq '{msgraph_lookup._quoted(conversation_id)}'", safe=""
        )
    )
    value = msgraph_lookup._read(ops, path).get("value")
    rows = [
        m
        for m in (value if isinstance(value, list) else [])
        if isinstance(m, dict) and str(m.get("conversationId") or "") == conversation_id and sender in _addresses(m)
    ]
    rows.sort(key=lambda m: str(m.get("sentDateTime") or ""), reverse=True)
    out: list[dict[str, Any]] = []
    for m in rows[:MAX_SENT]:
        att = m.get("attachments")
        files = [
            str(a.get("name"))[:200]
            for a in (att if isinstance(att, list) else [])[:MAX_ATTACHMENTS]
            if isinstance(a, dict) and a.get("name")
        ]
        out.append(
            {
                "at": str(m.get("sentDateTime") or ""),
                "to": _addresses(m),
                "text": own_words(str(m.get("bodyPreview") or "")),
                "files": files,
            }
        )
    return list(reversed(out))


def _request_refs(ops: Any, conversation_id: str, since: str) -> list[str]:
    """The internet message ids of the newest emails this mailbox received in
    the conversation within the window."""
    path = (
        ops._mail_path("messages")
        + f"?$select=internetMessageId,conversationId,receivedDateTime&$top={MAX_REFS}&$orderby="
        + urllib.parse.quote("receivedDateTime desc", safe="")
        + "&$filter="
        + urllib.parse.quote(
            f"receivedDateTime ge {since} and conversationId eq '{msgraph_lookup._quoted(conversation_id)}'",
            safe="",
        )
    )
    value = msgraph_lookup._read(ops, path).get("value")
    return [
        str(m.get("internetMessageId") or "")
        for m in (value if isinstance(value, list) else [])
        if isinstance(m, dict)
        and str(m.get("conversationId") or "") == conversation_id
        and str(m.get("internetMessageId") or "")
    ]


def _filed(broker: BrokerContext, ops: Any, conversation_id: str, since: str) -> list[dict[str, Any]]:
    verbs = getattr(broker, "drafting", None)
    ledger = getattr(verbs, "ledger", None) if verbs is not None else None
    if ledger is None:
        return []
    refs = _request_refs(ops, conversation_id, since)
    if not refs:
        return []
    out: list[dict[str, Any]] = []
    for row in ledger.delivered_for_refs(refs, since):
        try:
            files = (json.loads(row.get("delivery_json") or "{}") or {}).get("files") or []
        except ValueError:
            files = []
        names = [str(f.get("name") or "")[:200] for f in files if isinstance(f, dict) and f.get("name")]
        out.append(
            {
                "at": str(row.get("updated_at") or ""),
                "document": str(row.get("document_class") or ""),
                "matter": str(row.get("matter_number") or ""),
                "files": names,
            }
        )
    return out


def _section(name: str, read: Any) -> tuple[Any, dict[str, str]]:
    """One section's answer, or ``(None, {"<name>_error": kind})``: one failed
    read never costs the other section."""
    try:
        return read(), {}
    except Exception as exc:  # noqa: BLE001 - a section that cannot be read is left out, by name
        logger.warning("thread_recent: %s not read (%s)", name, type(exc).__name__)
        return None, {f"{name}_error": type(exc).__name__}


def recent(broker: BrokerContext, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """The verb's answer for one received email (``graph_message_id``)."""
    ops = broker.msgraph
    if ops is None:
        return _refusal("this seat has no Graph mailbox")
    gid = str(request.get("graph_message_id") or "").strip()
    if not _GRAPH_ID.match(gid):
        return _refusal("graph_message_id must be the turn's email's Graph id")
    try:
        found = msgraph_lookup.received_by_graph_id(ops, gid)
    except MsGraphRefused as exc:
        return _refusal(str(exc))
    if found is None or not found.get("conversation_id"):
        return _refusal("that email is not in this seat's Inbox as a received message")
    conversation_id = str(found["conversation_id"])
    sender = normalize_address(found.get("sender") or "")
    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent, sent_err = _section("sent", lambda: _sent(ops, conversation_id, since, sender))
    filed, filed_err = _section("filed", lambda: _filed(broker, ops, conversation_id, since))
    return {
        "ok": True,
        "accepted": True,
        "received_at": str(found.get("received_at") or ""),
        "sender": sender,
        "sent": sent,
        "filed": filed,
        **sent_err,
        **filed_err,
    }


def recent_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return recent(broker, request)


__all__ = ["MAX_SENT", "PREVIEW_CHARS", "VERBS", "WINDOW_DAYS", "own_words", "recent", "recent_verb"]
