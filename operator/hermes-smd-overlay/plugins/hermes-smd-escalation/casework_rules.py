"""The casework reply tools' shared rules: ledger rows, bounded state, wording.

Split from ``casework.py`` so each file stays readable. Everything here is pure or
file-local: the casework ledger rows as they are written, the per-session reply
state, and the small wording helpers the replies share. Nothing here reaches the
model. (The task review's and date-prep brief's envelope and rendering rules that
lived here were removed 2026-11-10 with those routines.)
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from shared import casework_ledger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


class _Bounded:
    """A small per-session map; a long-lived gateway must not grow one forever."""

    def __init__(self, cap: int = 64) -> None:
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._cap = cap
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._cap:
                self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


_REPLIES = _Bounded()


def _join(parts: list[str], conjunction: str = "and") -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} {conjunction} {parts[-1]}"


def _numbers(values: list[int], conjunction: str = "and") -> str:
    return _join([str(n) for n in sorted(values)], conjunction)


def _queued_note(count: int, tool: str) -> str:
    """What the turn is told while replayed task writes are waiting."""
    calls = "once" if count == 1 else f"{count} times"
    return (
        f"Call mcp_smokeball_update_task {calls}. Its arguments are filled in for you, "
        f"so pass the task_id you see and nothing else matters. Then call {tool} again."
    )


def ledger_path() -> str:
    """The casework ledger twin the agent uid reads (the broker is its only writer)."""
    return os.environ.get("SMD_CASEWORK_LEDGER_PATH") or casework_ledger.DEFAULT_LEDGER_PATH


def _event(
    name: str,
    *,
    skill: str,
    matter_id: str,
    kind: str,
    source_id: str,
    item_key: str,
    session_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """One row bound for the broker's ``casework_event_append`` verb, in the
    ledger's shape (``shared/casework_ledger.py``). The broker stamps ts and id;
    nobody here can backdate. Absent optional fields are left off, because the
    broker refuses a field an event kind does not carry."""
    event: dict[str, Any] = {
        "ts": None,
        "skill": skill,
        "matter_id": matter_id,
        "kind": kind,
        "source_id": source_id,
        "item_key": item_key,
        "event": name,
        "session_id": session_id,
    }
    event.update({k: v for k, v in fields.items() if v is not None})
    return event


def _ok(response: object) -> bool:
    return isinstance(response, dict) and bool(response.get("ok"))


def _append(append: Callable[[dict], Any], event: dict[str, Any]) -> bool:
    try:
        response = append(event)
    except Exception as exc:  # noqa: BLE001 — one lost row must not lose the rest
        logger.warning("casework: %s row not written (%s)", event.get("event"), exc)
        return False
    if not _ok(response):
        logger.warning("casework: %s row refused (%s)", event.get("event"), response)
        return False
    return True
