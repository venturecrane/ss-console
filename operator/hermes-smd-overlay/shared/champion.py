"""A client champion on a guide seat: product answers only, everything rep-only walled by code.

A CHAMPION is an insider at a client business who carries the internal case for
the Operator to the people who decide there. SMD can enroll one on its guide
seat (``seat.role: guide``) with a champion code: the broker writes the
enrolled entry with label :data:`shared.sender_enrollment.CHAMPION_LABEL`
(``workspace_broker/sender_enrollment.py`` redeem). From then on the guide
answers them like anyone enrolled, with three differences, each made in code
and never left to the model:

* THE WALL, at the reply relay (``hermes-smd-reply``, ``relay_checks``), keyed
  on the RECIPIENT: a reply to a champion is held when it carries a rep marker
  (a practice or drill header, a lesson line, a quiz), when a ``Source:`` line
  names a library document outside the champion allowlist, or when it names a
  ``##`` heading that document does not have. The champion then receives the
  one fixed :data:`SENTENCE` at the end of the turn (the held-notice path), and
  nothing from the held reply.
* THE ALLOWLIST is parsed from the product-guide library's own index
  (``references/INDEX.md``, the "The documents" table, its ``Audience``
  column): a document is open to a champion only when its Audience names
  ``champion``. A missing column, an unreadable index or a document the index
  does not mark is closed to champions (fail closed for champions only; a
  representative's reply never reads the allowlist).
* THE FENCE (``hermes-smd-initiation`` ``champion_fence``) and the handler
  guards (:func:`refuse_champion`): while a champion's turn is in flight, reads
  of the skills tree are narrowed to product-guide's SKILL.md and the allowed
  documents, and the representative tools (training, the desk, the record
  stores) refuse the champion with :data:`SENTENCE`.

THE IN-FLIGHT MARKER (:data:`IN_FLIGHT`) is in memory, per process: the
session ids of champion turns now running, each with a deadline. It is set by
the initiation plugin's ``pre_llm_call`` (which has the real session id) and
cleared at the turn's end; the deadline bounds a turn whose end hook never
fired. Nothing about the champion is written to disk here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The one sentence a champion receives for anything the guide holds from them.
SENTENCE = (
    "That is not available on this guide; for anything else, ask SMD Services at team@smd.services."
)
#: The platform-stated line the model reads on a champion's turn.
ROLE_LINE = "This sender is a client champion: follow Champion mode in the product-guide skill."
#: What a representative tool answers a champion: the sentence to send.
REFUSED_RESULT = json.dumps(
    {
        "ok": False,
        "say": SENTENCE,
        "instruction": "Reply with the say sentence, word for word, and add nothing else.",
    }
)
#: The audit reason of a reply the wall held.
HOLD_REASON = "champion_wall"
#: What the model is told when the wall holds its reply.
MODEL_DETAIL = (
    "This sender is a client champion, and this reply carried something that is not for "
    "them. Do not draft it again. The seat sends them its fixed sentence."
)

SKILL = "product-guide"
#: Where the product-guide skill is read from; unset, the seat's own copy
#: (``shared.training.skill_dir``: the persona's skills, the profiles', the image's).
DIR_ENV = "SMD_PRODUCT_GUIDE_DIR"
INDEX_FILE = "INDEX.md"
CHAMPION_AUDIENCE = "champion"
#: How long a champion turn counts as in flight when its end hook never fires.
FLIGHT_TTL_S = 900.0

# The wall's reasons, recorded on the hold row (never the reply's words).
REP_MARKER = "rep_marker"
SOURCE_NOT_ALLOWED = "source_not_allowed"
HEADING_NOT_FOUND = "heading_not_found"
SOURCE_UNREADABLE = "source_unreadable"

_REP_MARKERS = (
    re.compile(r"Practice \(playing:", re.IGNORECASE),
    re.compile(r"Drill \(theme:", re.IGNORECASE),
    re.compile(r"^\s*Lesson\s+\d+", re.IGNORECASE | re.MULTILINE),
)
#: A quiz: lettered choices (three or more distinct letters), or answer lines
#: ("1 B", "2. c").
_QUIZ_CHOICE = re.compile(r"^\s*\(?([A-Da-d])[.)]\s+\S", re.MULTILINE)
_QUIZ_ANSWER = re.compile(r"^\s*\d{1,2}\s*[.):-]?\s*[A-Da-d]\s*$", re.MULTILINE)
_SOURCE_LINE = re.compile(r"^\s*Source\s*:(.*)$", re.IGNORECASE | re.MULTILINE)
_SOURCE_FILE = re.compile(r"([A-Za-z0-9][A-Za-z0-9_./-]*\.md)\b")
_SOURCE_HEADING = re.compile(r"[\"“]([^\"”]+)[\"”]")
_MD_NAME = re.compile(r"(?<![A-Za-z0-9_-])([a-z0-9][a-z0-9-]*\.md)\b")
_LINK_NAME = re.compile(r"\[([^\]]+\.md)\]")


# ---------------------------------------------------------------------------
# The library: where it is, what the index opens to a champion
# ---------------------------------------------------------------------------


def skill_dir() -> Path | None:
    """The product-guide skill's directory, or None when it cannot be found."""
    configured = os.environ.get(DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    try:
        from shared import training

        return training.skill_dir(SKILL)
    except Exception:  # noqa: BLE001 - no library found opens nothing to a champion
        return None


def references_dir() -> Path | None:
    base = skill_dir()
    return base / "references" if base is not None else None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_allowlist(index_text: str) -> frozenset[str]:
    """The document file names the index's "The documents" table opens to a
    champion: the rows whose ``Audience`` cell names ``champion``. Anything
    else (no table, no Audience column, a malformed row) opens nothing."""
    if not isinstance(index_text, str):
        return frozenset()
    lines = index_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## The documents")
    except StopIteration:
        return frozenset()
    table = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.strip().startswith("|"):
            table.append(_cells(line))
    if len(table) < 2:
        return frozenset()
    header = [h.lower() for h in table[0]]
    if "audience" not in header or "document" not in header:
        return frozenset()
    doc_col, aud_col = header.index("document"), header.index("audience")
    out: set[str] = set()
    for row in table[2:]:
        if len(row) != len(header):
            continue
        audience = {a.strip().strip("`").strip().lower() for a in row[aud_col].split(",")}
        named = _LINK_NAME.search(row[doc_col]) or _MD_NAME.search(row[doc_col])
        if CHAMPION_AUDIENCE in audience and named:
            out.add(os.path.basename(named.group(1)))
    return frozenset(out)


def allowlist(refs: Path | None = None) -> frozenset[str]:
    """The champion allowlist, read live from this seat's INDEX.md."""
    refs = refs if refs is not None else references_dir()
    if refs is None:
        return frozenset()
    try:
        return parse_allowlist((refs / INDEX_FILE).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable index opens nothing to a champion
        logger.warning("champion: the library index is unreadable; nothing is open to champions")
        return frozenset()


def headings(path: Path) -> frozenset[str] | None:
    """Every ``##`` heading of a document, or None when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - unreadable is treated as no such heading
        return None
    return frozenset(ln[3:].strip() for ln in text.splitlines() if ln.startswith("## "))


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------


def _has_rep_marker(text: str) -> bool:
    if any(p.search(text) for p in _REP_MARKERS):
        return True
    letters = {m.group(1).upper() for m in _QUIZ_CHOICE.finditer(text)}
    return len(letters) >= 3 or len(_QUIZ_ANSWER.findall(text)) >= 2


def _source_problem(line: str, allowed: frozenset[str], refs: Path | None) -> str:
    found = _SOURCE_FILE.search(line)
    if found is None:
        return SOURCE_UNREADABLE
    name = os.path.basename(found.group(1))
    if name not in allowed or refs is None:
        return SOURCE_NOT_ALLOWED
    heading = _SOURCE_HEADING.search(line[found.end() :])
    if heading is None:
        return ""
    have = headings(refs / name)
    return "" if have is not None and heading.group(1).strip() in have else HEADING_NOT_FOUND


def wall_reason(
    text: str, *, allowed: frozenset[str] | None = None, refs: Path | None = None
) -> str:
    """Why a reply may not reach a champion, or ``""`` when it may.

    Never raises: anything it cannot decide is :data:`SOURCE_UNREADABLE`."""
    try:
        body = text if isinstance(text, str) else ""
        if _has_rep_marker(body):
            return REP_MARKER
        refs = refs if refs is not None else references_dir()
        allowed = allowed if allowed is not None else allowlist(refs)
        for line in _SOURCE_LINE.findall(body):
            problem = _source_problem(line, allowed, refs)
            if problem:
                return problem
        library = _library_names(refs)
        for name in _MD_NAME.findall(body):
            if name in library and name not in allowed:
                return SOURCE_NOT_ALLOWED
        return ""
    except Exception:  # noqa: BLE001 - a wall that cannot decide holds
        logger.warning("champion: the wall could not decide; holding", exc_info=True)
        return SOURCE_UNREADABLE


def _library_names(refs: Path | None) -> frozenset[str]:
    if refs is None:
        return frozenset()
    try:
        return frozenset(p.name for p in refs.glob("*.md"))
    except OSError:
        return frozenset()


# ---------------------------------------------------------------------------
# Who is a champion, and which turns are theirs
# ---------------------------------------------------------------------------


def is_champion(address: Any) -> bool:
    """Whether this address is enrolled with a champion code (read live). Never raises."""
    try:
        from shared import sender_enrollment

        return sender_enrollment.is_champion(address)
    except Exception:  # noqa: BLE001 - unreadable enrollment marks nobody
        return False


class InFlight:
    """The champion turns now running, by session id, each with a deadline."""

    def __init__(self, ttl_s: float = FLIGHT_TTL_S, keep: int = 256) -> None:
        self._ttl = ttl_s
        self._keep = keep
        self._lock = threading.Lock()
        self._until: OrderedDict[str, float] = OrderedDict()

    def _evict(self, now: float) -> None:
        for sid in [s for s, until in self._until.items() if until <= now]:
            del self._until[sid]

    def mark(self, session_id: str, *, now: float | None = None) -> None:
        if not session_id:
            return
        ts = time.monotonic() if now is None else now
        with self._lock:
            self._until[session_id] = ts + self._ttl
            self._until.move_to_end(session_id)
            while len(self._until) > self._keep:
                self._until.popitem(last=False)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._until.pop(session_id, None)

    def has(self, session_id: str, *, now: float | None = None) -> bool:
        with self._lock:
            self._evict(time.monotonic() if now is None else now)
            return bool(session_id) and session_id in self._until

    def any(self, *, now: float | None = None) -> bool:
        with self._lock:
            self._evict(time.monotonic() if now is None else now)
            return bool(self._until)

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._until.clear()


IN_FLIGHT = InFlight()


class Walled:
    """The emails whose reply the wall held: the held-notice path sends their
    sender :data:`SENTENCE` instead of its own line. Bounded, thread-safe."""

    def __init__(self, keep: int = 2048) -> None:
        self._lock = threading.Lock()
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._keep = keep

    def mark(self, message_id: str) -> None:
        if not message_id:
            return
        with self._lock:
            self._ids[message_id] = None
            self._ids.move_to_end(message_id)
            while len(self._ids) > self._keep:
                self._ids.popitem(last=False)

    def has(self, message_id: str) -> bool:
        with self._lock:
            return bool(message_id) and message_id in self._ids

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._ids.clear()


WALLED = Walled()


def reply_wall(recipient: Any, scan_text: str, message_id: Any) -> str:
    """The relay's one call: why this reply may not reach its recipient, or
    ``""`` (always, for anyone who is not a champion). A walled email is marked
    so the held-notice path sends the champion :data:`SENTENCE`. Never raises."""
    if not is_champion(recipient):
        return ""
    why = wall_reason(scan_text)
    if why:
        WALLED.mark(message_id if isinstance(message_id, str) else "")
    return why


def owes_sentence(message_id: str, sender: str) -> bool:
    """Whether a held reply to this email ends in :data:`SENTENCE`: the wall
    held it, or its sender is a champion ("send it again" would not help them)."""
    return WALLED.has(message_id) or is_champion(sender)


def held_line(message_id: str, sender: str, default: str) -> str:
    """The line the held-notice path sends for this email."""
    return SENTENCE if owes_sentence(message_id, sender) else default


def champion_turn(session_id: Any) -> bool:
    """Whether the turn this handler serves is a champion's: its session is a
    champion turn in flight, or the email that opened it is a champion's."""
    sid = session_id if isinstance(session_id, str) else ""
    if not sid:
        return False
    if IN_FLIGHT.has(sid):
        return True
    try:
        from shared.inbound import SESSION_INBOUND_ORIGIN

        origin = SESSION_INBOUND_ORIGIN.bound_this_turn(sid)
    except Exception:  # noqa: BLE001 - no origin is no champion
        return False
    return origin is not None and is_champion(origin.sender_address)


def refuse_champion(handler: Callable[..., Any]) -> Callable[..., Any]:
    """A representative tool's handler that answers a champion's turn with
    :data:`REFUSED_RESULT` (:data:`SENTENCE`, to send word for word) and never
    reaches the tool."""

    @wraps(handler)
    def guarded(args: Any, **kwargs: Any) -> Any:
        if champion_turn(kwargs.get("session_id")):
            logger.info("champion: %s refused for a champion's turn", handler.__name__)
            return REFUSED_RESULT
        return handler(args, **kwargs)

    return guarded


__all__ = [
    "HEADING_NOT_FOUND",
    "HOLD_REASON",
    "IN_FLIGHT",
    "MODEL_DETAIL",
    "REFUSED_RESULT",
    "REP_MARKER",
    "ROLE_LINE",
    "SENTENCE",
    "SOURCE_NOT_ALLOWED",
    "SOURCE_UNREADABLE",
    "WALLED",
    "allowlist",
    "champion_turn",
    "held_line",
    "is_champion",
    "owes_sentence",
    "parse_allowlist",
    "refuse_champion",
    "reply_wall",
    "wall_reason",
]
