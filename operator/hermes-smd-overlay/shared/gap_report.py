"""Where the product library falls short, counted by code (ss-console #3946, item 4).

WHAT IS COUNTED. When a listed person asks the guide seat a product question
the library does not cover, the product-guide skill answers with one fixed
sentence (``operator/skills/product-guide/SKILL.md``, Refusals, "Not covered"):
:data:`NOT_COVERED`. This module notices that sentence in a finished turn and
adds one to a counter under a TOPIC KEY. The weekly administrator report calls
:func:`weekly_gap_counts` and gets ``{topic key: count}``, nothing else.

NO MODEL DECIDES ANY OF IT, AND NO PERSON'S WORDS CAN ENTER IT:

* the detection is a string match on the fixed sentence, in the turn's final
  text or in the reply it drafted;
* only a PRODUCT-QUESTION TURN counts: an email turn from a verified, listed
  sender that opened the product library's index and did nothing else the desk,
  practice or training skills do (no record store, no reminder, no invite code,
  no training tool, no other skill's procedure read). A desk or practice turn
  that happens to quote the sentence is not counted;
* the topic key comes from a CLOSED, authored list (:data:`TOPICS`) matched
  against the email's subject and body by keyword; a question matching none is
  ``other``. The counter stores the key, the day and a count. It never stores
  the question, the sender, the thread or any reply text, so a note or a lead
  cannot reach the report through it;
* the report reads only the counter.

THE COUNTER is ``smd/gap_counts.db`` on the volume (``SMD_GAP_COUNTS_DB_PATH``):
one row per (UTC day, topic key) with a count, pruned after
:data:`KEEP_DAYS`. It holds nothing keyed to a person, so removing a person
(``person_purge``) has nothing to clear here.

Best-effort throughout: a failed count loses a count, never a turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The product-guide skill's fixed reply when the library does not cover a
#: question. Pinned to the skill text by test.
NOT_COVERED = "The product library doesn't cover that."
_NOT_COVERED_RE = re.compile(r"the product library doesn[’']t cover that", re.I)

DB_ENV = "SMD_GAP_COUNTS_DB_PATH"
DEFAULT_DB = "/opt/data/smd/gap_counts.db"
KEEP_DAYS = 120

OTHER = "other"
#: The closed list of topic keys and the words that select each, in order (the
#: first match wins). Authored data: a new key is a reviewed change here.
TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "integrations",
        ("integrat", "connect to", "connector", "crm", "api", "plug in", "plugin", "sync with"),
    ),
    (
        "security_and_compliance",
        ("secur", "complian", "hipaa", "soc 2", "soc2", "encrypt", "gdpr", "audit", "breach"),
    ),
    (
        "data_and_privacy",
        ("privacy", "data retention", "who can see", "delete our data", "where is the data"),
    ),
    (
        "setup_and_onboarding",
        ("onboard", "set up", "setup", "install", "get started", "go live", "launch"),
    ),
    (
        "industries",
        (
            "industr",
            "vertical",
            "real estate",
            "healthcare",
            "dental",
            "accounting",
            "construction",
        ),
    ),
    (
        "competition",
        ("competitor", "compared to", " versus ", " vs ", "chatgpt", "copilot", "alternative"),
    ),
    ("contracts_and_terms", ("contract", "terms", "cancel", "agreement", "sla", "commitment")),
    (
        "languages_and_channels",
        ("language", "spanish", "phone call", "voice", "text message", "sms", "whatsapp"),
    ),
    ("capabilities", ("can it", "can the operator", "does it", "is it able", "feature", "capab")),
)
TOPIC_KEYS: tuple[str, ...] = tuple(key for key, _ in TOPICS) + (OTHER,)

#: Tools whose use marks a turn as desk, practice or training work, not a
#: product question.
NON_PRODUCT_TOOLS = frozenset(
    {
        "record_store_list",
        "record_store_read",
        "record_store_write",
        "reminder_set",
        "invite_code_issue",
    }
)
NON_PRODUCT_TOOL_PREFIXES = ("training_", "tone_check")
LIBRARY_INDEX = "product-guide/references/INDEX.md"
PRODUCT_SKILL_DIR = "/product-guide/"
_BODY_MARK = "--- untrusted email body below"


# ---------------------------------------------------------------------------
# Detection (pure)
# ---------------------------------------------------------------------------


def topic_of(question: str) -> str:
    """The topic key for ``question`` from the closed list, or ``other``."""
    text = f" {question.lower()} " if isinstance(question, str) else ""
    for key, words in TOPICS:
        if any(word in text for word in words):
            return key
    return OTHER


def question_text(user_message: str) -> str:
    """The subject and body of the inbound email in the turn's prompt: the
    ``subject:`` line and everything after the untrusted-body marker."""
    if not isinstance(user_message, str):
        return ""
    subject = ""
    for line in user_message.splitlines():
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1]
            break
    _, _, body = user_message.partition(_BODY_MARK)
    body = body.split("\n", 1)[1] if "\n" in body else body
    return f"{subject}\n{body}"


def _turn_messages(history: Any) -> list[dict]:
    """This turn's messages: everything after the last user message."""
    if not isinstance(history, list):
        return []
    last_user = max(
        (i for i, m in enumerate(history) if isinstance(m, dict) and m.get("role") == "user"),
        default=-1,
    )
    return [m for m in history[last_user + 1 :] if isinstance(m, dict)]


def _tool_calls(messages: list[dict]) -> list[tuple[str, str]]:
    """``(tool name, arguments as text)`` for every tool call in ``messages``."""
    out: list[tuple[str, str]] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict):
                args = fn.get("arguments")
                out.append(
                    (
                        str(fn.get("name") or ""),
                        args if isinstance(args, str) else json.dumps(args or {}),
                    )
                )
    return out


def _texts(messages: list[dict], final: str) -> list[str]:
    out = [final] if isinstance(final, str) else []
    for message in messages:
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            out.append(message["content"])
    return out


def _is_product_turn(calls: list[tuple[str, str]]) -> bool:
    opened_index = False
    for name, args in calls:
        bare = name.removeprefix("mcp__")
        if bare in NON_PRODUCT_TOOLS or bare.startswith(NON_PRODUCT_TOOL_PREFIXES):
            return False
        if bare == "read_file":
            if LIBRARY_INDEX in args:
                opened_index = True
            elif "SKILL.md" in args and PRODUCT_SKILL_DIR not in args:
                return False
    return opened_index


def detect(user_message: str, history: Any, final_response: str) -> str | None:
    """The topic key when this finished turn is a product question answered
    with the fixed "not covered" sentence; None otherwise."""
    messages = _turn_messages(history)
    calls = _tool_calls(messages)
    if not _is_product_turn(calls):
        return None
    said = any(_NOT_COVERED_RE.search(t) for t in _texts(messages, final_response))
    said = said or any(_NOT_COVERED_RE.search(args.replace("\\u2019", "’")) for _, args in calls)
    if not said:
        return None
    return topic_of(question_text(user_message))


# ---------------------------------------------------------------------------
# The counter
# ---------------------------------------------------------------------------


def db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gap_counts ("
        "day TEXT NOT NULL, topic TEXT NOT NULL, n INTEGER NOT NULL, "
        "PRIMARY KEY (day, topic))"
    )
    return conn


def count(topic: str, *, now: datetime | None = None, path: Path | None = None) -> bool:
    """Add one to ``topic`` for today (UTC). Only a key from the closed list
    is ever written. True on success; never raises."""
    if topic not in TOPIC_KEYS:
        return False
    moment = now if now is not None else datetime.now(timezone.utc)
    day = moment.astimezone(timezone.utc).date()
    try:
        conn = _connect(path or db_path())
        try:
            with conn:
                conn.execute(
                    "INSERT INTO gap_counts (day, topic, n) VALUES (?, ?, 1) "
                    "ON CONFLICT(day, topic) DO UPDATE SET n = n + 1",
                    (day.isoformat(), topic),
                )
                conn.execute(
                    "DELETE FROM gap_counts WHERE day < ?",
                    ((day - timedelta(days=KEEP_DAYS)).isoformat(),),
                )
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 - a lost count, never a lost turn
        logger.warning("gap_report: count failed", exc_info=True)
        return False


def weekly_gap_counts(
    now: datetime | None = None, *, days: int = 7, path: Path | None = None
) -> dict[str, int]:
    """``{topic key: count}`` for the ``days`` UTC days ending today, for the
    weekly administrator report. Keys with no count are absent. Raises
    ``sqlite3.Error`` when the counter exists and cannot be read, so the report
    can say "could not be read" rather than "no gaps"."""
    target = path or db_path()
    if not target.exists():
        return {}
    moment = now if now is not None else datetime.now(timezone.utc)
    end: date = moment.astimezone(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT topic, SUM(n) FROM gap_counts WHERE day >= ? AND day <= ? GROUP BY topic",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return {str(topic): int(n) for topic, n in rows if topic in TOPIC_KEYS and n}


def note_turn(
    session_id: Any,
    user_message: Any,
    history: Any,
    final_response: Any,
    *,
    verified: Any = None,
) -> str | None:
    """The hook's whole job: count a not-covered product answer from a
    verified, listed sender. Returns the topic counted, or None."""
    try:
        check = verified if callable(verified) else _verified
        if not check(session_id):
            return None
        topic = detect(str(user_message or ""), history, str(final_response or ""))
        if topic is None:
            return None
        return topic if count(topic) else None
    except Exception:  # noqa: BLE001 - never raise into a hook
        logger.warning("gap_report: note_turn failed", exc_info=True)
        return None


def _verified(session_id: Any) -> bool:
    from shared.reminders import verified_person

    return verified_person(session_id) is not None


__all__ = [
    "NOT_COVERED",
    "OTHER",
    "TOPICS",
    "TOPIC_KEYS",
    "count",
    "detect",
    "note_turn",
    "question_text",
    "topic_of",
    "weekly_gap_counts",
]
