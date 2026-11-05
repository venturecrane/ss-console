"""What the conflict check said about THIS turn's email, recorded by code.

WHY THIS EXISTS (2026-11-04, the model-as-logic audit). The matter inbox
router's conflict step was prose the model followed or did not: nothing in
code knew a check had been owed, run, or answered. The check is now a
connector read (``mcp_smokeball_conflict_check``, ``conflict_check.py`` in the
Smokeball connector) and the trust gate (``hermes-smd-trust``,
``conflict_fence.py``) enforces its answer. This module is the record between
them: the connector's own verdict, taken from the tool RESULT in
``post_tool_call`` (never from anything the model says about it), keyed by the
session AND the message id of the email the turn answers, so a verdict for one
email can never open the writes of another.

WHAT IS KEPT, per (session, message):

* ``hit`` is sticky: once any check this turn returned ``hit``, the turn is
  held whatever a later call returns. A conflict does not clear by asking
  again.
* ``clear`` counts only when the call checked the turn's own verified sender
  (``sender_email``) and subject line (``subject_line``) as the router
  recorded them from the provider's payload (the subject only when the
  provider gave one). A clear for someone else, or for a different message,
  is recorded as ``mismatch`` and opens nothing.
* ``search_failed``, an error result, or a result in a shape this module does
  not know: not clear.

In-memory and bounded, like ``matter_file_facts``: the record lives for the
turn it answers, and a gateway restart forgets it, which leaves the turn
fenced (fail closed), never open.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from shared import result_unwrap

TOOL = "mcp_smokeball_conflict_check"

HIT = "hit"
CLEAR = "clear"
SEARCH_FAILED = "search_failed"
#: A clear for a sender or subject other than this turn's email.
MISMATCH = "mismatch"
#: No result this module could read (an error result, an unknown shape).
UNREADABLE = "unreadable"

_VERDICTS = frozenset({HIT, CLEAR, SEARCH_FAILED})
_MAX_SESSIONS = 256


@dataclass(frozen=True)
class Verdict:
    message_id: str
    verdict: str
    clearance_task_id: str = ""


_LOCK = threading.Lock()
_RECORDS: OrderedDict[str, Verdict] = OrderedDict()


def _reset_for_tests() -> None:
    with _LOCK:
        _RECORDS.clear()


def _norm(value: Any) -> str:
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _read(result: Any) -> tuple[str, str]:
    """``(verdict, clearance task id)`` from the connector's result."""
    parsed = result_unwrap.parse_tool_result(result)
    if not isinstance(parsed, dict) or parsed.get("error"):
        return UNREADABLE, ""
    verdict = parsed.get("verdict")
    if verdict not in _VERDICTS:
        return UNREADABLE, ""
    task = parsed.get("clearance_task_id")
    return str(verdict), task if isinstance(task, str) else ""


def classify(args: Any, result: Any, *, sender_address: str, subject: str) -> tuple[str, str]:
    """The verdict this call earns for the turn's email (see the module docstring)."""
    verdict, task = _read(result)
    if verdict != CLEAR:
        return verdict, task
    call = args if isinstance(args, dict) else {}
    if _norm(call.get("sender_email")) != _norm(sender_address) or not _norm(sender_address):
        return MISMATCH, ""
    # A provider that did not say the subject leaves nothing to compare; the
    # sender still has to be the turn's own.
    if _norm(subject) and _norm(call.get("subject_line")) != _norm(subject):
        return MISMATCH, ""
    return CLEAR, ""


def record(session_id: str, message_id: str, verdict: str, clearance_task_id: str = "") -> None:
    """Keep this turn's verdict. ``hit`` is never replaced within a turn."""
    if not session_id or not message_id:
        return
    with _LOCK:
        held = _RECORDS.get(session_id)
        if held is not None and held.message_id == message_id and held.verdict == HIT:
            _RECORDS.move_to_end(session_id)
            return
        _RECORDS[session_id] = Verdict(message_id, verdict, clearance_task_id)
        _RECORDS.move_to_end(session_id)
        while len(_RECORDS) > _MAX_SESSIONS:
            _RECORDS.popitem(last=False)


def verdict_for(session_id: str, message_id: str) -> Verdict | None:
    """The verdict recorded for this session's email ``message_id``, or None."""
    if not session_id or not message_id:
        return None
    with _LOCK:
        held = _RECORDS.get(session_id)
    return held if held is not None and held.message_id == message_id else None


__all__ = [
    "CLEAR",
    "HIT",
    "MISMATCH",
    "SEARCH_FAILED",
    "TOOL",
    "UNREADABLE",
    "Verdict",
    "classify",
    "record",
    "verdict_for",
]
