"""The mailbox keeper's decisions, all in code (doctrine Law 18, Class N).

The SMD operations seat keeps one Google Workspace mailbox: it trashes settled
"[SMD Ops]" alert threads and lists, once a day, the people waiting on a reply.
No model reads a message and no model decides anything; this module is the
whole of the judgement, and it is pure (no I/O), so every rule is a test.
``mailbox_keeper_verbs.py`` reads the mailbox, hands the threads here, and acts
on what comes back.

RULE 1, A SETTLED SMD OPS THREAD IS TRASHED. Every one of these must hold, or
the thread is left alone:

* every message is from the alert sender (``team@smd.services``), with the
  mailbox's own ``Authentication-Results`` (the first one, stamped by
  ``mx.google.com``) showing ``dkim=pass header.i=@smd.services``;
* every subject is an ``[SMD Ops]`` subject, and none is a kind that never
  clears (below);
* the newest message replies to an alert (``In-Reply-To`` or ``References``
  names ``<smd-alert.<uuid>@smd.services>``), and its first paragraph is
  exactly the recovery paragraph ``workers/fleet-alerts/src/transition-email.ts``
  writes;
* walking the thread in order, every alert that opened is followed by a
  recovery (a thread can hold more than one incident);
* no message is from the mailbox owner (a thread he wrote in is his);
* when the seat authors ``require_alert_state_header``, the newest message also
  carries ``X-SMD-Alert-State: resolved`` (the worker stamps it from
  2026-11-13; the setting stays off until that is live).

WHY THE THREAD AND NOT THE ROOT MESSAGE-ID. The alert's own Message-ID
(``<smd-alert.<uuid>@smd.services>``) is rewritten by Amazon SES on delivery
(``<...@email.amazonses.com>``), so the delivered root cannot be matched to it
(measured 2026-11-13, vfy_01M7ATXM50M0T1Q50YT67RW45A). The reply's own
``In-Reply-To`` still names it, and Gmail's threadId is what groups the pair.

RULE 1B, AN SMD OPS THREAD THAT IS NOT SETTLED IS LISTED, never trashed: an
open alert, a reminder, one he replied to, one that failed authentication.
The two kinds that never recover (``send-refused.ts``: "a scheduled message did
not go out"; ``shortfalls.ts``: "a reply did not go out", "a limit stopped the
work") are listed in their own section and are never trashed.

RULE 2, A PERSON WAITING ON HIM IS LISTED. In the inbox, the newest message
from a person (not a list: no ``List-Unsubscribe``, no bulk ``Precedence``, not
auto-submitted; not a no-reply address; not the alert sender or an authored
system sender; not the seat's own inbox; not one of his own addresses), with
nothing from his addresses after it in the thread, older than
``needs_you_after_hours``.

EVERYTHING ELSE IS UNTOUCHED.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from typing import Any

#: The recovery email's first paragraph, exactly as
#: ``workers/fleet-alerts/src/transition-email.ts`` builds it: ``alertHtml``
#: writes ``<p><strong>WHAT HAPPENED:</strong> {happened}</p>`` and a recovery's
#: ``happened`` is ``This has cleared.``. A reminder's is ``Still not fixed. ...``
#: and an alert's is the condition's own words, so neither can match.
RECOVERY_PARAGRAPH = "WHAT HAPPENED: This has cleared."
ALERT_STATE_HEADER = "x-smd-alert-state"
ALERT_REF_RE = re.compile(r"<smd-alert\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}@smd\.services>")
SMD_OPS_SUBJECT_RE = re.compile(r"^(?:re:\s*)*\[SMD Ops\]\s", re.IGNORECASE)
#: Subjects of the alerts that have no recovery email (send-refused.ts,
#: shortfalls.ts). Listed once, never trashed.
NEVER_CLEARS: tuple[str, ...] = (
    "a scheduled message did not go out",
    "a reply did not go out",
    "a limit stopped the work",
)
_NO_REPLY_LOCAL = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster)(?:[+._-].*)?$")
_SKIP_LABELS = frozenset({"TRASH", "SPAM", "DRAFT"})
_WS = re.compile(r"\s+")
_FIRST_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    internal_ms: int
    labels: frozenset[str]
    sender: str
    sender_name: str
    subject: str
    in_reply_to: str
    references: str
    auth_results: str
    list_unsubscribe: bool
    bulk: bool
    alert_state: str
    first_paragraph: str


@dataclass(frozen=True)
class Settings:
    mailbox: str
    owners: frozenset[str]
    digest_recipient: str = ""
    needs_you_after_hours: float = 24.0
    window_days: int = 14
    require_alert_state_header: bool = False
    alert_sender: str = "team@smd.services"
    alert_domain: str = "smd.services"
    system_senders: frozenset[str] = frozenset()
    seat_inbox: str = ""
    timezone: str = "America/Phoenix"
    max_pages: int = 20


@dataclass(frozen=True)
class Waiting:
    message_id: str
    thread_id: str
    sender: str
    sender_name: str
    subject: str
    since_ms: int


@dataclass(frozen=True)
class OpsThread:
    thread_id: str
    subject: str
    verified: bool


@dataclass
class Plan:
    settled: list[str] = field(default_factory=list)
    open_alerts: list[OpsThread] = field(default_factory=list)
    never_clears: list[OpsThread] = field(default_factory=list)
    waiting: list[Waiting] = field(default_factory=list)


def _norm(address: str) -> str:
    return parseaddr(address or "")[1].strip().lower()


def _clean(text: str, limit: int = 160) -> str:
    """One line of untrusted header text, safe to put in a plain-text email."""
    value = _WS.sub(" ", "".join(ch for ch in str(text or "") if ch.isprintable() or ch.isspace())).strip()
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _headers(payload: dict[str, Any]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for header in payload.get("headers") or []:
        if isinstance(header, dict) and isinstance(header.get("name"), str):
            found.setdefault(header["name"].lower(), []).append(str(header.get("value") or ""))
    return found


def _decode(data: Any) -> str:
    if not isinstance(data, str) or not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _part(payload: dict[str, Any], mime: str) -> str:
    """The first body part of ``mime``, depth-first, decoded."""
    if str(payload.get("mimeType") or "").lower() == mime:
        body = payload.get("body")
        text = _decode(body.get("data")) if isinstance(body, dict) else ""
        if text:
            return text
    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            found = _part(part, mime)
            if found:
                return found
    return ""


def first_paragraph(payload: dict[str, Any]) -> str:
    """The message's first paragraph as text, whitespace collapsed. The HTML
    part's first ``<p>`` when there is one (the alert worker sends HTML), else
    the plain part's first non-blank paragraph."""
    markup = _part(payload, "text/html")
    if markup:
        match = _FIRST_P.search(markup)
        if match:
            return _WS.sub(" ", html.unescape(_TAG.sub("", match.group(1)))).strip()
    plain = _part(payload, "text/plain")
    for chunk in re.split(r"\n\s*\n", plain.replace("\r\n", "\n")):
        text = _WS.sub(" ", chunk).strip()
        if text:
            return text
    return ""


def parse_message(raw: dict[str, Any]) -> Message:
    found = raw.get("payload")
    payload: dict[str, Any] = found if isinstance(found, dict) else {}
    headers = _headers(payload)

    def one(name: str) -> str:
        return (headers.get(name) or [""])[0]

    name, address = parseaddr(one("from"))
    precedence = one("precedence").strip().lower()
    auto = one("auto-submitted").strip().lower()
    try:
        internal = int(raw.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal = 0
    return Message(
        id=str(raw.get("id") or ""),
        thread_id=str(raw.get("threadId") or ""),
        internal_ms=internal,
        labels=frozenset(str(label) for label in raw.get("labelIds") or []),
        sender=address.strip().lower(),
        sender_name=name.strip(),
        subject=one("subject"),
        in_reply_to=one("in-reply-to"),
        references=" ".join(headers.get("references") or []),
        # The FIRST Authentication-Results only: Gmail prepends its own, and a
        # sender can write any number of its own below it.
        auth_results=one("authentication-results"),
        list_unsubscribe=bool(headers.get("list-unsubscribe")),
        bulk=precedence in ("bulk", "list", "junk") or (auto not in ("", "no")),
        alert_state=one(ALERT_STATE_HEADER),
        first_paragraph=first_paragraph(payload),
    )


def thread_messages(thread: dict[str, Any]) -> list[Message]:
    """The thread's live messages (not trashed, spam or draft), oldest first."""
    found = [parse_message(m) for m in thread.get("messages") or [] if isinstance(m, dict)]
    live = [m for m in found if not (m.labels & _SKIP_LABELS)]
    return sorted(live, key=lambda m: m.internal_ms)


def dkim_pass(auth_results: str, domain: str) -> bool:
    """The mailbox's own verdict: ``mx.google.com`` saw a passing DKIM
    signature by exactly ``domain`` (not a subdomain, not a lookalike)."""
    text = (auth_results or "").strip()
    if not text.lower().startswith("mx.google.com"):
        return False
    pattern = r"\bdkim=pass\b[^;]*\bheader\.i=@" + re.escape(domain.lower()) + r"(?=[\s;]|$)"
    return re.search(pattern, text.lower()) is not None


def _replies_to_alert(m: Message) -> bool:
    return ALERT_REF_RE.search(f"{m.in_reply_to} {m.references}") is not None


def _is_recovery(m: Message, settings: Settings) -> bool:
    if not _replies_to_alert(m) or m.first_paragraph != RECOVERY_PARAGRAPH:
        return False
    return not settings.require_alert_state_header or m.alert_state.strip().lower() == "resolved"


def never_clears(subject: str) -> bool:
    lowered = subject.lower()
    return any(phrase in lowered for phrase in NEVER_CLEARS)


def _is_ops(m: Message, settings: Settings) -> bool:
    return m.sender == settings.alert_sender and SMD_OPS_SUBJECT_RE.match(m.subject) is not None


def _verified(m: Message, settings: Settings) -> bool:
    return _is_ops(m, settings) and dkim_pass(m.auth_results, settings.alert_domain)


def settled(messages: list[Message], settings: Settings) -> bool:
    """Rule 1. True only when every condition in the module docstring holds."""
    if not messages:
        return False
    if any(not _verified(m, settings) or never_clears(m.subject) for m in messages):
        return False
    if any(m.sender in settings.owners for m in messages):
        return False
    if not _is_recovery(messages[-1], settings):
        return False
    open_incidents = 0
    for m in messages:
        if _is_recovery(m, settings):
            open_incidents = max(0, open_incidents - 1)
        elif not _replies_to_alert(m):
            open_incidents += 1
    return open_incidents == 0


def _is_person(m: Message, settings: Settings) -> bool:
    sender = m.sender
    if "@" not in sender or m.list_unsubscribe or m.bulk or "SENT" in m.labels:
        return False
    local, _, domain = sender.partition("@")
    if sender in settings.owners or sender in (settings.seat_inbox, settings.alert_sender):
        return False
    if sender in settings.system_senders or f"@{domain}" in settings.system_senders:
        return False
    return _NO_REPLY_LOCAL.match(local) is None


def waiting_on_owner(messages: list[Message], settings: Settings, now_ms: int) -> Message | None:
    """Rule 2: the newest message from a person, when nothing from his own
    addresses follows it and it is older than ``needs_you_after_hours``."""
    people = [i for i, m in enumerate(messages) if _is_person(m, settings)]
    if not people:
        return None
    newest = people[-1]
    message = messages[newest]
    if "INBOX" not in message.labels:
        return None
    for later in messages[newest + 1 :]:
        if later.sender in settings.owners or "SENT" in later.labels:
            return None
    if now_ms - message.internal_ms < settings.needs_you_after_hours * 3_600_000:
        return None
    return message


def plan(threads: list[dict[str, Any]], settings: Settings, now_ms: int) -> Plan:
    """Every decision for one run, from the threads as read. Pure."""
    out = Plan()
    for thread in threads:
        messages = thread_messages(thread)
        if not messages:
            continue
        thread_id = str(thread.get("id") or messages[0].thread_id)
        if any(_is_ops(m, settings) for m in messages):
            subject = _clean(messages[0].subject)
            verified = all(_verified(m, settings) or m.sender in settings.owners for m in messages)
            if any(never_clears(m.subject) for m in messages):
                out.never_clears.append(OpsThread(thread_id, subject, verified))
            elif settled(messages, settings):
                out.settled.append(thread_id)
            else:
                out.open_alerts.append(OpsThread(thread_id, subject, verified))
            continue
        person = waiting_on_owner(messages, settings, now_ms)
        if person is not None:
            out.waiting.append(
                Waiting(
                    message_id=person.id,
                    thread_id=thread_id,
                    sender=person.sender,
                    sender_name=_clean(person.sender_name, 80),
                    subject=_clean(person.subject),
                    since_ms=person.internal_ms,
                )
            )
    return out


def _addresses(value: Any) -> frozenset[str]:
    items = value.split(",") if isinstance(value, str) else value if isinstance(value, list) else []
    found: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        text = item.strip().lower()
        if text.startswith("@"):
            found.add(text)
        else:
            found.update(a.lower() for _, a in getaddresses([text]) if "@" in a)
    return frozenset(found)


class SettingsError(ValueError):
    """The skill's authored settings cannot run the keeper."""


def _number(raw: Any, default: float, low: float, high: float, name: str) -> float:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        raise SettingsError(f"{name} must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{name} must be a number") from exc
    if not low <= value <= high:
        raise SettingsError(f"{name} must be from {low:g} to {high:g}")
    return value


def _flag(raw: Any, name: str) -> bool:
    if raw is None or raw == "":
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        return raw.strip().lower() == "true"
    raise SettingsError(f"{name} must be true or false")


def settings_from(raw: dict[str, Any], *, seat_inbox: str = "") -> Settings:
    """The authored skill settings (scalars, ADR 0075), checked. A missing
    mailbox refuses; everything else has the default named in SKILL.md."""
    mailbox = _norm(str(raw.get("mailbox") or ""))
    if "@" not in mailbox:
        raise SettingsError("mailbox: the managed mailbox address is required")
    owners = _addresses(raw.get("owner_addresses")) or frozenset({mailbox})
    recipient = _norm(str(raw.get("digest_recipient") or ""))
    timezone = str(raw.get("timezone") or "America/Phoenix").strip()
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception as exc:
        raise SettingsError(f"timezone: unknown zone {timezone!r}") from exc
    return Settings(
        mailbox=mailbox,
        owners=owners,
        digest_recipient=recipient if "@" in recipient else "",
        needs_you_after_hours=_number(raw.get("needs_you_after_hours"), 24.0, 1, 720, "needs_you_after_hours"),
        window_days=int(_number(raw.get("window_days"), 14, 1, 60, "window_days")),
        require_alert_state_header=_flag(raw.get("require_alert_state_header"), "require_alert_state_header"),
        system_senders=_addresses(raw.get("system_senders")),
        seat_inbox=_norm(str(raw.get("seat_inbox") or "")) or _norm(seat_inbox),
        timezone=timezone,
        max_pages=int(_number(raw.get("max_pages"), 20, 1, 200, "max_pages")),
    )


__all__ = [
    "ALERT_REF_RE",
    "NEVER_CLEARS",
    "RECOVERY_PARAGRAPH",
    "Message",
    "OpsThread",
    "Plan",
    "Settings",
    "SettingsError",
    "Waiting",
    "dkim_pass",
    "first_paragraph",
    "never_clears",
    "parse_message",
    "plan",
    "settings_from",
    "settled",
    "thread_messages",
    "waiting_on_owner",
]
