"""The mailbox keeper's reads and writes against Gmail and AgentMail.

Every decision is made elsewhere (``mailbox_keeper.py``, ``mailbox_keeper_ops.py``,
``mailbox_keeper_classify.py``); this module reads, files, drafts and sends.

FAIL CLOSED ON A READ. A search is read to its last page, and every thread in
full, BEFORE anything is acted on. A read error, an answer of the wrong shape,
more pages than allowed, or a full page with no next token raises
``ReadFailed``: nothing filed, nothing sent, no state written.

ONE MORNING LIST, ACROSS MACHINES. Before the list is sent the run waits a
machine-specific few seconds (``jitter_seconds``, from ``FLY_MACHINE_ID``), then
asks AgentMail whether this seat's inbox already sent a ``Mailbox:`` email to
the recipient since seat-local midnight (``AgentMailOps.sent_to_since``). Sent
means ``already_sent_today``; a lookup that fails means ``send_check_failed``
and no send. On 2026-11-16 a second machine, back after a host failure, sent the
list again because "sent today" lived only on the first machine's volume.

THE ACT PHASE runs after the list went out. Each thread is read again first and
skipped when its newest message changed since it was read (a reply that arrived
in between is never archived unseen). Reply drafts are the one exception to
"after the send": a draft hides nothing, and the list may say "Draft is in the
thread" only when Gmail returned a draft id, so drafts are made just before the
send, on the same re-read and the same skip rule. Any draft already in the
thread (his own included, read from Gmail's DRAFT label) blocks a new one.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import mailbox_keeper as mk
from . import mailbox_keeper_classify as mkc
from . import mailbox_keeper_ops as mko
from .broker_context import BrokerContext

PAGE_SIZE = 100
BACKFILL_MAX_PAGES = 200
JITTER_SECONDS = 90
DIGEST_PREFIX = "Mailbox:"
SKILL_NAME = "mailbox-keeper"
LANE = "skill:" + SKILL_NAME
#: The seam the tests replace (never real time in a test).
SLEEP: Callable[[float], None] = time.sleep


class ReadFailed(RuntimeError):
    """The mailbox could not be read in full; ``code`` is content-free."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- reading


def _search_ids(operations: Any, mailbox: str, query: str, max_pages: int) -> tuple[list[str], int]:
    thread_ids: list[str] = []
    token, pages = "", 0
    while True:
        pages += 1
        if pages > max_pages:
            raise ReadFailed("too_many_pages")
        try:
            page = operations.gmail_search(
                {"query": query, "mailbox": mailbox, "max_results": PAGE_SIZE, "page_token": token}
            )
        except Exception as exc:
            raise ReadFailed(f"search_error:{type(exc).__name__}") from exc
        if not isinstance(page, dict) or not isinstance(page.get("messages"), list):
            raise ReadFailed("search_bad_shape")
        for item in page["messages"]:
            thread_id = str(item.get("threadId") or "") if isinstance(item, dict) else ""
            if not thread_id:
                raise ReadFailed("search_bad_shape")
            if thread_id not in thread_ids:
                thread_ids.append(thread_id)
        token = str(page.get("next_page_token") or "")
        if not token:
            if len(page["messages"]) >= PAGE_SIZE:
                raise ReadFailed("full_page_without_token")
            return thread_ids, pages


def get_thread(operations: Any, mailbox: str, thread_id: str) -> dict[str, Any]:
    try:
        thread = operations.gmail_get_thread({"thread_id": thread_id, "mailbox": mailbox})
    except Exception as exc:
        raise ReadFailed(f"thread_error:{type(exc).__name__}") from exc
    if not isinstance(thread, dict) or not isinstance(thread.get("messages"), list):
        raise ReadFailed("thread_bad_shape")
    return thread


def read_threads(
    operations: Any, settings: mk.Settings, query: str, max_pages: int
) -> tuple[list[dict[str, Any]], int]:
    """Every thread the query finds, in full, and the page count."""
    ids, pages = _search_ids(operations, settings.mailbox, query, max_pages)
    return [get_thread(operations, settings.mailbox, t) for t in ids], pages


def inbox_query(settings: mk.Settings) -> str:
    return f"in:inbox newer_than:{settings.window_days}d"


def ops_query(settings: mk.Settings, *, windowed: bool = True) -> str:
    window = f" newer_than:{settings.window_days}d" if windowed else ""
    return f"in:inbox{window} from:{settings.alert_sender}"


def newest_id(thread: dict[str, Any]) -> str:
    messages = mk.thread_messages(thread)
    return messages[-1].id if messages else ""


# ---------------------------------------------------------------- SMD Ops


@dataclass
class Sweep:
    """What one pass over SMD Ops mail found and did."""

    trash: list[str] = field(default_factory=list)
    label: list[str] = field(default_factory=list)
    needs_you: list[dict[str, Any]] = field(default_factory=list)
    kept_owner: int = 0
    kept_unverified: int = 0
    #: Verified, not settled, and no ``alert_label`` authored: left in the inbox.
    unlabelled: int = 0
    trashed: int = 0
    trash_failed: int = 0
    labelled: int = 0
    label_failed: int = 0


def plan_sweep(threads: list[dict[str, Any]], settings: mk.Settings, listed: set[str]) -> Sweep:
    """Pure: which SMD Ops threads to trash and to label, and which messages
    in them are new needs-you lines (``mailbox_keeper_ops.needs_you``)."""
    out = Sweep()
    for thread in threads:
        messages = mk.thread_messages(thread)
        ops = [m for m in messages if mk.is_ops(m, settings)]
        if not ops:
            continue
        thread_id = str(thread.get("id") or messages[0].thread_id)
        if mk.settled(messages, settings):
            out.trash.append(thread_id)
            continue
        if any(m.sender in settings.owners for m in messages):
            out.kept_owner += 1
            continue
        if not all(mk.verified(m, settings) for m in messages):
            out.kept_unverified += 1
            continue
        if settings.alert_label:
            out.label.append(thread_id)
        else:
            out.unlabelled += 1
        for m in ops:
            found = mko.needs_you(m.subject, m.first_paragraph)
            if found and m.id not in listed:
                kind, line = found
                out.needs_you.append(
                    {"message_id": m.id, "thread_id": thread_id, "kind": kind, "line": line, "ms": m.internal_ms}
                )
    return out


def apply_sweep(operations: Any, settings: mk.Settings, sweep: Sweep, on_trash: Callable[[str], None]) -> None:
    for thread_id in sweep.trash:
        try:
            operations.gmail_trash_thread({"thread_id": thread_id, "mailbox": settings.mailbox})
        except Exception:  # noqa: BLE001 - one failed trash leaves that thread in the inbox, counted
            sweep.trash_failed += 1
            continue
        sweep.trashed += 1
        try:
            on_trash(thread_id)
        except OSError:
            pass  # the trash happened; the undo log is a convenience, and Trash itself holds the thread
    if not sweep.label:
        return
    try:
        label_id = operations.gmail_label_ensure({"name": settings.alert_label, "mailbox": settings.mailbox})
    except Exception:  # noqa: BLE001 - no label, nothing leaves the inbox; counted
        sweep.label_failed += len(sweep.label)
        return
    for thread_id in sweep.label:
        try:
            operations.gmail_modify_thread(
                {
                    "thread_id": thread_id,
                    "mailbox": settings.mailbox,
                    "add_label_ids": [label_id],
                    "remove_label_ids": ["INBOX"],
                }
            )
        except Exception:  # noqa: BLE001 - that thread stays in the inbox, counted
            sweep.label_failed += 1
            continue
        sweep.labelled += 1


# ---------------------------------------------------------------- send once


def jitter_seconds(machine_id: str) -> int:
    """A stable per-machine wait, so two machines firing the same slot do not
    check AgentMail in the same second. sha256, not ``hash()`` (salted per
    process)."""
    if not machine_id:
        return 0
    return int(hashlib.sha256(machine_id.encode("utf-8")).hexdigest(), 16) % JITTER_SECONDS


def local_midnight_utc(zone: str, now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def already_sent(broker: BrokerContext, settings: mk.Settings, *, wait: bool = True) -> bool:
    """Wait the machine's jitter (once, before the reading), then ask AgentMail.
    Raises when it cannot look."""
    if wait:
        SLEEP(jitter_seconds(os.environ.get("FLY_MACHINE_ID") or ""))
    if broker.agentmail is None:
        raise RuntimeError("no AgentMail channel to look in")
    since = local_midnight_utc(settings.timezone)
    return bool(broker.agentmail.sent_to_since(settings.digest_recipient, DIGEST_PREFIX, since))


def send_digest(broker: BrokerContext, recipient: str, subject: str, text: str) -> dict[str, Any]:
    """The one list, through the fenced, audited AgentMail transmit."""
    from .agentmail_ops import AgentMailRefused, AgentMailTransportError, collect_recipients
    from .transmit_verbs import dispatch_transmit

    if broker.agentmail is None or broker.ledger is None:
        raise ValueError("the list needs the AgentMail channel and the audit ledger")
    agentmail = broker.agentmail
    return dispatch_transmit(
        broker,
        "agentmail_send",
        {"payload": {"to": [recipient], "subject": subject, "text": text}, "audit_extra": {"skill_name": SKILL_NAME}},
        send=agentmail.send,
        reply=agentmail.reply,
        refused=AgentMailRefused,
        transport=AgentMailTransportError,
        attempted_for_send=collect_recipients,
        identity_key="inbox_id",
        channel="agentmail",
        internal_lane=LANE,
    )


# ---------------------------------------------------------------- acting


def _reread(operations: Any, settings: mk.Settings, o: mkc.Outcome) -> dict[str, Any] | None:
    """The thread as it is now, or None when it cannot be read or changed."""
    try:
        thread = get_thread(operations, settings.mailbox, o.thread_id)
    except ReadFailed:
        return None
    return thread if newest_id(thread) == o.message_id else None


def _from_alias(operations: Any, settings: mk.Settings, received_at: str) -> str:
    """The alias the draft is sent from: where the message was received, when
    the mailbox authors it as a send-as; else the mailbox itself ("")."""
    if not received_at or received_at == settings.mailbox:
        return ""
    return received_at if operations.is_send_as(settings.mailbox, received_at) else ""


def make_draft(operations: Any, settings: mk.Settings, c: mkc.Candidate, o: mkc.Outcome) -> str:
    """'made', 'exists', 'changed', 'no_address' or 'failed'."""
    thread = _reread(operations, settings, o)
    if thread is None:
        return "changed"
    if mk.has_draft(thread):
        return "exists"
    to = c.reply_to or c.sender
    if "@" not in to or mk._NO_REPLY_LOCAL.search(to.partition("@")[0]):
        return "no_address"
    subject = c.subject if c.subject.lower().startswith("re:") else f"Re: {c.subject}"
    body = o.draft + (f"\n\n{settings.draft_signoff}" if settings.draft_signoff else "")
    refs = " ".join(x for x in (c.references.strip(), c.message_id_header) if x)
    payload = {
        "mailbox": settings.mailbox,
        "from": _from_alias(operations, settings, c.received_at),
        "to": to,
        "subject": subject,
        "body": body,
        "thread_id": c.thread_id,
        "in_reply_to": c.message_id_header,
        "references": refs,
    }
    try:
        made = operations.gmail_create_draft(payload)
    except Exception:  # noqa: BLE001 - no draft; the decision is still listed
        return "failed"
    return "made" if isinstance(made, dict) and made.get("id") else "failed"


def file_thread(operations: Any, settings: mk.Settings, o: mkc.Outcome, receipts_label_id: str) -> str:
    """Archive a notice, or label and archive a receipt. 'done', 'changed' or 'failed'."""
    if _reread(operations, settings, o) is None:
        return "changed"
    add = [receipts_label_id] if o.kind == "receipt" else []
    try:
        operations.gmail_modify_thread(
            {"thread_id": o.thread_id, "mailbox": settings.mailbox, "add_label_ids": add, "remove_label_ids": ["INBOX"]}
        )
    except Exception:  # noqa: BLE001 - that thread stays in the inbox, counted
        return "failed"
    return "done"


__all__ = [
    "BACKFILL_MAX_PAGES",
    "DIGEST_PREFIX",
    "JITTER_SECONDS",
    "PAGE_SIZE",
    "ReadFailed",
    "Sweep",
    "already_sent",
    "apply_sweep",
    "file_thread",
    "get_thread",
    "inbox_query",
    "jitter_seconds",
    "local_midnight_utc",
    "make_draft",
    "newest_id",
    "ops_query",
    "plan_sweep",
    "read_threads",
    "send_digest",
]
