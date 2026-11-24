"""How many requests a propounded set carries, owned by code (design B).

The firm's targets are an optional section of ``drafting-firm.yaml``::

    discovery_counts: {<variant>: {min?, max?}}

``<variant>`` is a propounded set's key (``sets.VARIANTS``); each target names
a minimum, a maximum or both, whole numbers. Absent, nothing reads it and every
set is drafted as before. The statutory 35 stays a constant in code, never
config. ``facts`` and ``declaration`` are the interfaces the counts change
builds; until then nothing calls them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import sets as sets_mod

KEY = "discovery_counts"
BOUNDS = ("min", "max")
#: The count over which a declaration is attached (Code Civ. Proc. 2030.050, 2033.050).
STATUTORY_LIMIT = 35


def _whole(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate(body: object) -> list[str]:
    """The section's closed shape: each variant maps to ``{min?, max?}``, whole
    numbers, at least one bound, min not above max."""
    keys = [k for k, _label, _pat in sets_mod.VARIANTS]
    if not isinstance(body, dict) or not body:
        return [f"{KEY}: expected a map of {keys} to {{min?, max?}}"]
    out: list[str] = []
    for variant, target in body.items():
        where = f"{KEY}.{variant}"
        if variant not in keys:
            out.append(f"{where}: unknown set (expected one of {keys})")
            continue
        if not isinstance(target, dict) or not target or not set(target) <= set(BOUNDS):
            out.append(f"{where}: expected {{min?, max?}} with at least one")
            continue
        out += [
            f"{where}.{b}: expected a whole number above 0" for b in BOUNDS if b in target and not _whole(target[b])
        ]
        if _whole(target.get("min")) and _whole(target.get("max")) and target["min"] > target["max"]:
            out.append(f"{where}: min is above max")
    return out


def targets(firm_data: dict[str, Any] | None) -> dict[str, tuple[int | None, int | None]]:
    """The firm's (min, max) per variant; {} when it authored none."""
    body = (firm_data or {}).get(KEY)
    if body is None or validate(body):
        return {}
    return {v: (t.get("min"), t.get("max")) for v, t in body.items()}


@dataclass(frozen=True)
class Facts:
    """What code counted in one unit, and what it decided: the set's variant,
    its requests (every one counts, genuineness requests included), the firm's
    target, the proven prior requests to that party, and the attachment key of
    the declaration attached (``decl_2030_050`` or ``decl_2033_050``), or None."""

    variant: str
    count: int
    target: tuple[int | None, int | None] = (None, None)
    prior: int = 0
    declaration: str | None = None


def facts(unit: Any) -> Facts | None:
    """The counts code recorded for a unit (``counts.json``); None while
    nothing records them."""
    del unit
    return None


def declaration(variant: str, count: int, prior: int = 0) -> str | None:
    """The one decision ``render.attach``, ``format_check`` and ``review`` will
    share: the attachment key of the declaration a set needs, or None. Built
    with the counts change; nothing calls it yet."""
    raise NotImplementedError("counts.declaration is built with the counts change (design B, B1)")
