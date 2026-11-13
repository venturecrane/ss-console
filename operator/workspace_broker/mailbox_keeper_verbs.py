"""``mailbox_keeper_run``: the broker keeps one managed mailbox, in code.

Class N (ADR 0050, doctrine Law 18): the cron pre_run of the
``mailbox-keeper`` skill calls this verb and wakes no turn. Everything is
decided by ``mailbox_keeper.py`` (pure) from the mailbox as read here; no model
reads a message, and the caller names only the MODE. Who is written to, what
is trashed and what the email says are the broker's, from the seat's own
customer.yaml and the mailbox itself.

MODES.
``scheduled``  what the cron rows send: ``daily`` when today's list has not
               gone out yet (seat-local date), else ``midday``. So the first
               run of the day sends the list, a later run only trashes, and a
               morning run that failed is made up by the next one.
``daily``      trash settled alert threads, then send the one list.
``midday``     trash settled alert threads only.
``dry_run``    read and decide only: the thread ids, the rule each fell to and
               the counts come back; nothing is trashed, sent or recorded.
               This is the $0 rehearsal (root, through the seat's socket).

FAIL CLOSED ON A READ. The inbox (``in:inbox newer_than:<window_days>d``) is
read to its last page, and every thread in full, BEFORE anything is acted on.
A read error, an answer of the wrong shape, more pages than ``max_pages``, or a
full page with no next token answers ``read_failed``: nothing trashed, nothing
sent, no state written. "Could not look" must never read as "nothing there".

THE EMAIL goes only to the authored ``digest_recipient``, through the same
fenced, audited AgentMail transmit every broker send uses
(``transmit_verbs.dispatch_transmit``: the participant fence, the intent row,
CONFIRM_SEND_DISPATCHED), from the seat's own inbox, on the skill's own lane.
At most one list per seat-local day (the state file's ``digest_sent_on``).

STATE (gone-means-gone, CLAUDE.md layer inventory):
``$SMD_MAILBOX_KEEPER_DIR`` (default ``/opt/data/mailbox-keeper/``)
``listed.json``    ids already listed (alert thread ids, waiting message ids)
                   and the date of the last list; ids only, no content.
``trashed.jsonl``  one line per trashed thread (time, thread id, rule), so a
                   trash can be found and undone from Gmail's Trash (30 days).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import mailbox_keeper as mk
from . import seat_config
from .broker_context import BrokerContext

VERBS: tuple[str, ...] = ("mailbox_keeper_run",)
SKILL_NAME = "mailbox-keeper"
LANE = "skill:" + SKILL_NAME
MODES: tuple[str, ...] = ("scheduled", "daily", "midday", "dry_run")
PAGE_SIZE = 100
STATE_DIR_ENV = "SMD_MAILBOX_KEEPER_DIR"
DEFAULT_STATE_DIR = "/opt/data/mailbox-keeper"
EMPTY_DAY = "Nothing waiting on you. Trashed {n} settled alerts."


class ReadFailed(RuntimeError):
    """The mailbox could not be read in full; ``code`` is content-free."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- config


def skill_entry(customer_path: Path) -> dict[str, Any] | None:
    """The skill's entry, or None unless the seat LISTS it and does not
    disable it. Omission is refusal (ADR 0035); unreadable enables nothing."""
    try:
        doc = seat_config.load(customer_path, encoding="utf-8", on_missing="raise") or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    personas = doc.get("personas") if isinstance(doc, dict) else None
    for persona in personas if isinstance(personas, list) else []:
        skills = persona.get("skills") if isinstance(persona, dict) else None
        for skill in skills if isinstance(skills, list) else []:
            if isinstance(skill, dict) and skill.get("name") == SKILL_NAME:
                return None if skill.get("enabled") is False else skill
    return None


def _seat_inbox(broker: BrokerContext) -> str:
    from .agentmail_auth import seat_inbox_address

    try:
        return seat_inbox_address(broker.customer_path, broker.customer_slug)
    except Exception:  # noqa: BLE001 - the setting can still name it
        return ""


# ---------------------------------------------------------------- state


def state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR)


def load_state(directory: Path) -> dict[str, Any]:
    path = directory / "listed.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"alerts": [], "waiting": [], "digest_sent_on": ""}
    if not isinstance(data, dict):
        raise ValueError("listed.json is not an object")
    return {
        "alerts": [str(x) for x in data.get("alerts") or []],
        "waiting": [str(x) for x in data.get("waiting") or []],
        "digest_sent_on": str(data.get("digest_sent_on") or ""),
    }


def save_state(directory: Path, state: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "listed.json"
    tmp = directory / "listed.json.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def _log_trashed(directory: Path, thread_id: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "trashed.jsonl"
    line = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "thread_id": thread_id, "rule": "settled"}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")
    path.chmod(0o600)


# ---------------------------------------------------------------- reading


def read_inbox(operations: Any, settings: mk.Settings) -> tuple[list[dict[str, Any]], int]:
    """Every inbox thread in the window, in full, and the page count. Raises
    ``ReadFailed`` on anything short of a complete read."""
    query = f"in:inbox newer_than:{settings.window_days}d"
    thread_ids: list[str] = []
    token, pages = "", 0
    while True:
        pages += 1
        if pages > settings.max_pages:
            raise ReadFailed("too_many_pages")
        try:
            page = operations.gmail_search(
                {"query": query, "mailbox": settings.mailbox, "max_results": PAGE_SIZE, "page_token": token}
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
            break
    threads: list[dict[str, Any]] = []
    for thread_id in thread_ids:
        try:
            thread = operations.gmail_get_thread({"thread_id": thread_id, "mailbox": settings.mailbox})
        except Exception as exc:
            raise ReadFailed(f"thread_error:{type(exc).__name__}") from exc
        if not isinstance(thread, dict) or not isinstance(thread.get("messages"), list):
            raise ReadFailed("thread_bad_shape")
        threads.append(thread)
    return threads, pages


# ---------------------------------------------------------------- the email


def _local(ms: int, zone: str) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(zone)).strftime("%b %d, %I:%M %p").replace(" 0", " ")


def render_digest(
    waiting: list[mk.Waiting],
    alerts: list[mk.OpsThread],
    never: list[mk.OpsThread],
    trashed: int,
    trash_failed: int,
    zone: str,
) -> tuple[str, str]:
    """The subject and body, in a fixed section order. Code-rendered only."""
    tail = f"Trashed {trashed} settled alerts."
    if trash_failed:
        tail += f" {trash_failed} settled alert threads could not be trashed and are still in the inbox."
    if not (waiting or alerts or never):
        line = EMPTY_DAY.format(n=trashed)
        if trash_failed:
            line = f"Nothing waiting on you. {tail}"
        return "Mailbox: nothing waiting on you", line
    lines: list[str] = []
    if waiting:
        lines.append(f"Waiting on a reply from you ({len(waiting)}):")
        for w in waiting:
            who = f"{w.sender_name} <{w.sender}>" if w.sender_name else w.sender
            lines.append(f"- {who}: {w.subject or '(no subject)'} (since {_local(w.since_ms, zone)})")
        lines.append("")
    if alerts:
        lines.append(f"SMD Ops alerts not cleared ({len(alerts)}):")
        for a in alerts:
            lines.append(f"- {a.subject or '(no subject)'}" + ("" if a.verified else " (sender not verified)"))
        lines.append("")
    if never:
        lines.append(f"SMD Ops notices that do not clear on their own ({len(never)}):")
        for a in never:
            lines.append(f"- {a.subject or '(no subject)'}" + ("" if a.verified else " (sender not verified)"))
        lines.append("")
    lines.append(tail)
    subject = f"Mailbox: {len(waiting)} waiting on you" if waiting else "Mailbox: SMD Ops alerts to look at"
    return subject, "\n".join(lines)


def _send_digest(broker: BrokerContext, recipient: str, subject: str, text: str) -> dict[str, Any]:
    """The one list, through the fenced, audited AgentMail transmit."""
    from .agentmail_ops import AgentMailRefused, AgentMailTransportError, collect_recipients
    from .transmit_verbs import dispatch_transmit

    if broker.agentmail is None or broker.ledger is None:
        raise ValueError("the list needs the AgentMail channel and the audit ledger")
    agentmail = broker.agentmail
    return dispatch_transmit(
        broker,
        "agentmail_send",
        {
            "payload": {"to": [recipient], "subject": subject, "text": text},
            "audit_extra": {"skill_name": SKILL_NAME},
        },
        send=agentmail.send,
        reply=agentmail.reply,
        refused=AgentMailRefused,
        transport=AgentMailTransportError,
        attempted_for_send=collect_recipients,
        identity_key="inbox_id",
        channel="agentmail",
        internal_lane=LANE,
    )


# ---------------------------------------------------------------- the verb


def _today(zone: str) -> str:
    return datetime.now(tz=ZoneInfo(zone)).strftime("%Y-%m-%d")


def _decisions(plan: mk.Plan, state: dict[str, Any]) -> dict[str, Any]:
    listed_alerts, listed_waiting = set(state["alerts"]), set(state["waiting"])
    return {
        "settled": [{"thread_id": t, "rule": "1_settled_alert"} for t in plan.settled],
        "open_alerts": [
            {"thread_id": a.thread_id, "rule": "1b_open_alert", "already_listed": a.thread_id in listed_alerts}
            for a in plan.open_alerts
        ],
        "never_clears": [
            {"thread_id": a.thread_id, "rule": "1b_never_clears", "already_listed": a.thread_id in listed_alerts}
            for a in plan.never_clears
        ],
        "waiting": [
            {
                "thread_id": w.thread_id,
                "message_id": w.message_id,
                "rule": "2_waiting",
                "already_listed": w.message_id in listed_waiting,
            }
            for w in plan.waiting
        ],
    }


def _counts(plan: mk.Plan, pages: int, threads: int) -> dict[str, int]:
    return {
        "pages": pages,
        "threads": threads,
        "settled": len(plan.settled),
        "open_alerts": len(plan.open_alerts),
        "never_clears": len(plan.never_clears),
        "waiting": len(plan.waiting),
    }


def run_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    mode = str(request.get("mode") or "scheduled")
    if mode not in MODES:
        raise ValueError(f"mailbox_keeper_run mode must be one of {list(MODES)}")
    entry = skill_entry(broker.customer_path)
    if entry is None:
        return {"ok": True, "status": "not_enabled"}
    found = entry.get("settings")
    raw: dict[str, Any] = found if isinstance(found, dict) else {}
    try:
        settings = mk.settings_from(raw, seat_inbox=_seat_inbox(broker))
    except mk.SettingsError as exc:
        return {"ok": True, "status": "settings_invalid", "reason": str(exc)}
    directory = state_dir()
    try:
        state = load_state(directory)
    except (OSError, ValueError) as exc:
        return {"ok": True, "status": "state_unreadable", "reason": type(exc).__name__}
    try:
        threads, pages = read_inbox(broker.operations, settings)
    except ReadFailed as exc:
        return {"ok": True, "status": "read_failed", "reason": exc.code}
    plan = mk.plan(threads, settings, int(time.time() * 1000))
    counts = _counts(plan, pages, len(threads))
    if mode == "dry_run":
        return {"ok": True, "status": "dry_run", "mode": mode, "counts": counts, "decisions": _decisions(plan, state)}
    today = _today(settings.timezone)
    if mode == "scheduled":
        mode = "midday" if state["digest_sent_on"] == today else "daily"
    trashed, trash_failed = 0, 0
    for thread_id in plan.settled:
        try:
            broker.operations.gmail_trash_thread({"thread_id": thread_id, "mailbox": settings.mailbox})
        except Exception:  # noqa: BLE001 - one failed trash leaves that thread in the inbox, counted
            trash_failed += 1
            continue
        trashed += 1
        try:
            _log_trashed(directory, thread_id)
        except OSError:
            pass  # the trash happened; the undo log is a convenience, and Trash itself holds the thread
    out: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "counts": {**counts, "trashed": trashed, "trash_failed": trash_failed},
    }
    if mode == "midday":
        return {**out, "status": "done" if not trash_failed else "trash_partial"}
    if state["digest_sent_on"] == today:
        return {**out, "status": "already_sent_today"}
    if not settings.digest_recipient:
        return {**out, "status": "recipient_unauthored"}
    return _deliver(broker, settings, plan, state, directory, today, out)


def _deliver(
    broker: BrokerContext,
    settings: mk.Settings,
    plan: mk.Plan,
    state: dict[str, Any],
    directory: Path,
    today: str,
    out: dict[str, Any],
) -> dict[str, Any]:
    listed_alerts, listed_waiting = set(state["alerts"]), set(state["waiting"])
    waiting = [w for w in plan.waiting if w.message_id not in listed_waiting]
    alerts = [a for a in plan.open_alerts if a.thread_id not in listed_alerts]
    never = [a for a in plan.never_clears if a.thread_id not in listed_alerts]
    counts = out["counts"]
    subject, text = render_digest(waiting, alerts, never, counts["trashed"], counts["trash_failed"], settings.timezone)
    try:
        _send_digest(broker, settings.digest_recipient, subject, text)
    except Exception as exc:  # noqa: BLE001 - a refused or failed send is recorded by the transmit itself
        return {**out, "status": "digest_failed", "reason": type(exc).__name__}
    seen_alerts = {a.thread_id for a in (*plan.open_alerts, *plan.never_clears)}
    seen_waiting = {w.message_id for w in plan.waiting}
    new_state = {
        # Pruned to what this read saw: an id that left the window cannot return.
        "alerts": sorted(seen_alerts),
        "waiting": sorted(seen_waiting),
        "digest_sent_on": today,
    }
    listed = {"listed_waiting": len(waiting), "listed_alerts": len(alerts) + len(never)}
    try:
        save_state(directory, new_state)
    except OSError as exc:
        return {**out, "status": "sent_state_unsaved", "reason": type(exc).__name__, "counts": {**counts, **listed}}
    return {**out, "status": "done", "counts": {**counts, **listed}}


__all__ = [
    "MODES",
    "SKILL_NAME",
    "VERBS",
    "ReadFailed",
    "load_state",
    "read_inbox",
    "render_digest",
    "run_verb",
    "save_state",
    "skill_entry",
]
