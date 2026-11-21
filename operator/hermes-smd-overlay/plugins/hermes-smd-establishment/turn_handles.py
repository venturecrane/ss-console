"""Per-turn handles for a person's own proposals (R2.2, 2026-11-19).

When a gate stops the code commit of a person's own confirmed rule, the model is
told to commit it with ``establish_submit``. It must name the proposal, but it
is never shown the proposal id (an id it can see is an id it can quote), so it
is handed "rule-1-of-this-turn" instead, and the submit swaps it back. Split out
of ``own_rules`` under the module-size ratchet; reads the plugin's own register
(``_TURN_HANDLES``) at call time.
"""

from __future__ import annotations

import re
import sys
from types import ModuleType
from typing import Any


def _plugin() -> ModuleType:
    """The establishment plugin module this copy serves, read at call time."""
    return sys.modules[__name__.rpartition(".")[0]]


def turn_handle(session_id: str, proposal_id: str) -> str:
    """An opaque per-turn handle for a proposal the model must pass to a tool:
    "rule-1-of-this-turn". The model never sees the proposal id (R2.2)."""
    handles = _plugin()._TURN_HANDLES.setdefault(session_id, [])
    if proposal_id not in handles:
        handles.append(proposal_id)
    return f"rule-{handles.index(proposal_id) + 1}-of-this-turn"


def with_real_id(args: dict[str, Any], session_id: Any) -> dict[str, Any]:
    """``args`` with a handle in ``proposal_id`` swapped for the id it stands for."""
    if not args.get("proposal_id"):
        return args
    return {**args, "proposal_id": real_id(session_id, args.get("proposal_id"))}


def real_id(session_id: Any, value: Any) -> str:
    """The proposal id a handle from :func:`turn_handle` stands for, else the
    value as given (lowercased): an administrator's rule is still named by id."""
    text = str(value or "").strip().lower()
    found = re.fullmatch(r"rule-(\d+)-of-this-turn", text)
    if not found or not isinstance(session_id, str):
        return text
    handles = _plugin()._TURN_HANDLES.get(session_id) or []
    index = int(found.group(1)) - 1
    return handles[index] if 0 <= index < len(handles) else text


__all__ = ["real_id", "turn_handle", "with_real_id"]
