"""The staff reassignment's DELIVER wake. Code-authored text only: integers,
staff names (from the practice system's staff list, filtered to letters),
matter numbers (filtered to the number alphabet), and fixed tokens. Never a
client's name, a task's words or the runner's free-text reason.

The detail is the runner's verdict file (``verdict.json`` ``reassign``), read
by the lane when it composes the wake; the counts ride the ledger row too.
"""

from __future__ import annotations

import re
from typing import Any

from ...skill_load import load_line

_NAME = re.compile(r"[A-Za-z][A-Za-z .'’-]{0,79}")
_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_STAGE = re.compile(r"[a-z0-9_]{1,24}")
_KINDS = ("unknown", "ambiguous", "inactive", "same")
_ROLES = ("from", "to", "attorney")
MAX_LISTED = 60


def _names(raw: Any) -> list[str]:
    return [n for n in (str(x) for x in (raw if isinstance(raw, list) else [])) if _NAME.fullmatch(n)][:MAX_LISTED]


def _numbers(raw: Any) -> list[str]:
    return [n for n in (str(x) for x in (raw if isinstance(raw, list) else [])) if _NUMBER.fullmatch(n)][:MAX_LISTED]


def _tally(raw: Any, rx: re.Pattern[str]) -> str:
    items = raw.items() if isinstance(raw, dict) else []
    kept = [f"{k} {int(v)}" for k, v in items if rx.fullmatch(str(k)) and isinstance(v, int) and v > 0]
    return "; ".join(kept[:MAX_LISTED]) or "none"


def _unresolved(raw: Any) -> str:
    u = raw if isinstance(raw, dict) else {}
    role, kind, name = str(u.get("role") or ""), str(u.get("kind") or ""), str(u.get("name") or "")
    if role not in _ROLES or kind not in _KINDS:
        return "Unresolved: none."
    shown = f' "{name}"' if _NAME.fullmatch(name) else ""
    return f"Unresolved: {role} {kind}{shown}."


def compose(
    *, job_id: str, skill: str, state: str, fields: dict[str, Any], env: dict[str, Any], trig: str,
    stage: str | None, detail: dict[str, Any], hold_line: str | None,
) -> str:  # fmt: skip
    raw = fields.get("counts")
    counts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    d = detail if isinstance(detail, dict) else {}
    statuses = [s for s in d.get("statuses") or [] if s in ("Open", "Pending", "Closed")]
    lines = [
        f"Run the {skill} skill's DELIVER mode for litigation job {job_id}.",
        load_line(skill),
        "Kind: litigation. Report: reassign.",
        f"Trigger: {trig if trig in ('request', 'scheduled') else 'unknown'}.",
        f"Outcome: {state}.",
        "Counts: "
        + ("; ".join(f"{k} {int(v)}" for k, v in sorted(counts.items()) if isinstance(v, int)) or "none")
        + ".",
        f"Moved from: {'; '.join(_names(d.get('from'))) or 'none'}.",
        f"Moved to: {'; '.join(_names([d.get('to')])) or 'none'}.",
        f"Attorneys: {'; '.join(_names(d.get('attorneys'))) or 'all'}.",
        f"Scope: {'litigation cases only' if d.get('litigation_only') else 'all cases'}; statuses {', '.join(statuses) or 'none'}.",
        f"Cases not moved: {'; '.join(_numbers(d.get('cases_not_moved'))) or 'none'}.",
        f"Tasks not moved: {_tally(d.get('tasks_not_moved'), _NUMBER)}.",
        f"Left outside the scope: {_tally(d.get('outside_by_attorney'), _NAME)}.",
        f"Nothing to move for: {'; '.join(_names(d.get('from_with_nothing'))) or 'none'}.",
        _unresolved(d.get("unresolved")),
        f"Requested by: {env.get('requester') or ''}.",
    ]
    if state != "delivered":
        where = str(fields.get("stage") or stage or "")
        lines.append(f"Reason: stopped at {where if _STAGE.fullmatch(where) else 'the runner (no verdict)'}.")
    if state == "held" and hold_line:
        lines.append(hold_line)
    return "\n".join(lines)


__all__ = ["compose"]
