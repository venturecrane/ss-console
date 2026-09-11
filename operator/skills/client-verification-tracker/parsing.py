"""Pure date/string coercions for the verification pre_run.

Split out of ``pre_run.py`` under the module-size ratchet
(``tests/operator-module-size.test.ts``) when the blind-wake control landed.
No I/O, no state: every function here maps an untrusted payload value to a
typed value or None, which is why they were the safe thing to move.

Sibling module, path-loaded like ``blind_wake.py`` and ``handoff_writer.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence


def parse_iso_date(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def first_date(item: dict, keys: Sequence[str]) -> date | None:
    for key in keys:
        parsed = parse_iso_date(item.get(key))
        if parsed is not None:
            return parsed
    return None


def first_str(item: dict, keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""
