"""The facts of a matter-records reply, written by code from the page finder (ADR 0102).

WHY THIS EXISTS (2026-11-02, an A&P records request). An attorney asked the A&P
Operator for records. It found the right files, but both replies it drafted
were wrong, so SMD wrote the one that was sent: the model retyped the finder's
page ranges and counts into its own sentences, dropped pp 48-49 and wrote 59
for 57. Nothing compared those sentences with what the finder had reported.

SINCE ADR 0102 AMENDMENT 4 (2026-11-03) a records request is a queued job (the
``records`` lane): the runner renders the facts with the same
``records_contract.facts.render`` this module calls, and the two in-turn tools
are withdrawn from the seat. What stays here serves a turn that still produced
a finder result (an older connector): its reply is rendered the same way and
held exactly as before.

WHAT THIS DOES. The facts a records reply states are rendered by code, from the
``find_pages_in_file`` results this turn produced and the files actually staged,
never typed by the model:

* **Attached:** each staged file, its pages and its page count (the count is
  of the staged bytes, ``matter_file_reply.StagedFile.page_count``), with the
  heading and date the finder printed for those pages.
* **Possibly relevant, not attached:** the finder's ``relatedCandidates``,
  labelled as candidates.
* **Not found:** a record the finder looked for over every page of a file.
* **Not established:** a record not found while pages were unread, with the
  counts and the closest sections.

The block goes into the reply BEFORE the relay builds its scan text and html,
so every outbound gate and the SMD review digest cover it. The model writes the
opening line, what the seat could not attach, and the open questions.

WHAT HOLDS. Each of these holds the reply for SMD's review (no redraft loop: the
model cannot fix what code wrote):

* a finder result that is not :data:`SCHEMA_VERSION` (an older connector, or a
  newer one than this overlay knows): no partial block is ever rendered;
* a staged page range that is not a range the finder reported for that file;
* the model's own words stating a page (``pp 48``, ``page 5``) or a count
  (``12 pages``) that the facts do not state. Dates, matter numbers and phone
  numbers do not match these patterns.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# The renderer and the reply-body helpers are the records contract's (ADR 0102
# amendment 4): the records lane renders the same words from the same code, so
# neither keeps a copy. ``operator/connectors/_contract`` is installed beside the
# overlay in the seat's Hermes venv (templates/Dockerfile).
from records_contract import facts as _contract
from records_contract.facts import (
    MARKER,
    SCHEMA_VERSION,
    insert,
    missing,
    model_words,
    tokens,
    unstated,
)

from shared import output_checklist, result_unwrap

FIND_TOOL = "mcp_smokeball_find_pages_in_file"
_MAX_SESSIONS = 128


@dataclass
class _Search:
    message_id: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


_LOCK = threading.Lock()
_SEARCHES: OrderedDict[str, _Search] = OrderedDict()


def _reset_for_tests() -> None:
    with _LOCK:
        _SEARCHES.clear()


def record_from_result(session_id: str, args: Any, result: Any, *, message_id: str) -> None:
    """Note that this turn searched a file's pages, and keep its latest whole result.

    Called from the reply plugin's post-tool hooks, so the result is the
    connector's own (never the model's restatement). A call that errored still
    counts as a search: the reply that follows is held while review is on.

    Nothing is recorded without the email this turn answers: a record that
    could match any later reply would carry one request's file facts into a
    reply about another (second review of PR #3711)."""
    if not session_id or not message_id:
        return
    parsed = result_unwrap.parse_tool_result(result)
    file_id = ""
    if isinstance(parsed, dict) and not parsed.get("error"):
        file_id = str(parsed.get("fileId") or "").strip()
    with _LOCK:
        search = _SEARCHES.get(session_id)
        if search is None or search.message_id != message_id:
            search = _Search(message_id)
        if file_id and isinstance(parsed, dict):
            search.results[file_id] = parsed
        _SEARCHES[session_id] = search
        _SEARCHES.move_to_end(session_id)
        while len(_SEARCHES) > _MAX_SESSIONS:
            _SEARCHES.popitem(last=False)


def _search_for(session_id: str, message_id: str) -> _Search | None:
    with _LOCK:
        search = _SEARCHES.get(session_id) if session_id else None
    if search is None or not message_id or search.message_id != message_id:
        return None
    return search


def searched(session_id: str, message_id: str) -> bool:
    """Did the turn answering ``message_id`` call ``find_pages_in_file``?"""
    return _search_for(session_id, message_id) is not None


def finder_matter(session_id: str, message_id: str) -> str:
    search = _search_for(session_id, message_id)
    for result in search.results.values() if search else []:
        if result.get("matterId"):
            return str(result["matterId"])
    return ""


# ---------------------------------------------------------------------------
# Rendering (records_contract.facts.render)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """The rendered block and whether the reply must wait for SMD."""

    text: str
    html: str
    hold: str
    #: The page and count tokens the block states (see :func:`tokens`).
    stated: frozenset[str]
    #: The match model's words the block prints (``asked``), guarded like the reply's.
    echoed: tuple[str, ...] = ()
    #: The file names the block prints, exactly as on the matter. The reply
    #: checklist does not judge them (they are authored data, not our words).
    names: tuple[str, ...] = ()


#: No block: what :func:`insert` writes when the facts cannot be (it still takes the marker out).
EMPTY = Facts("", "", "", frozenset())


def without_names(facts: Facts | None, text: str) -> str:
    """``text`` with each file name the facts print replaced by a neutral
    word, for the reply checklist: it judges our prose, not a file's name."""
    return _contract.without_names(facts, text)  # type: ignore[arg-type]


def build(
    session_id: str, message_id: str, staging: Any, read_matters: set[str] | None = None
) -> Facts | None:
    """The facts block for the reply to ``message_id``, or ``None`` when the
    turn neither searched a file's pages nor staged a file. ``read_matters``
    (the session's content reads) must name only the facts' own matter.

    The words are :func:`records_contract.facts.render`'s, so this turn's
    reply and the records lane's state the same facts the same way."""
    search = _search_for(session_id, message_id)
    files = list(staging.files) if staging is not None and not staging.refusal else []
    if search is None and not files:
        return None
    rendered = _contract.render(
        dict(search.results) if search else {},
        [f.as_row() for f in files],
        staged_matter=str(staging.matter_id or "") if staging is not None else "",
        read_matters=read_matters or set(),
        known_caps=output_checklist.KNOWN_CAPS,
    )
    return Facts(
        rendered.text,
        rendered.html,
        rendered.hold_reason or "",
        rendered.stated,
        rendered.echoed,
        rendered.names,
    )


__all__ = [
    "FIND_TOOL",
    "EMPTY",
    "MARKER",
    "SCHEMA_VERSION",
    "Facts",
    "build",
    "finder_matter",
    "insert",
    "missing",
    "model_words",
    "record_from_result",
    "searched",
    "tokens",
    "unstated",
    "without_names",
]
