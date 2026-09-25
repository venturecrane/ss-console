"""Pure date/string coercions for the verification pre_run.

Split out of ``pre_run.py`` under the module-size ratchet
(``tests/operator-module-size.test.ts``) when the blind-wake control landed.
No I/O, no state: every function here maps an untrusted payload value to a
typed value or None, which is why they were the safe thing to move.

Sibling module, path-loaded like ``blind_wake.py`` and ``handoff_writer.py``.

``parse_iso_date`` and ``first_date`` lived here too, as copies of the shared
``skill_helpers`` bodies; the pre_run calls ``_H.first_date`` now (code review
2026-09-25, found when the sync gate's private-copy scan was widened from
``pre_run.py`` to every skill module).
"""

from __future__ import annotations

from typing import Sequence


def first_str(item: dict, keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""
