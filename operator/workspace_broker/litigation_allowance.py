"""The billing cycle's litigation status-list allowance (2026-10-24).

The firm's proposal: a number of litigation status lists is included in each
billing cycle; one beyond it is quoted before it starts; a scheduled weekday
list counts as one. This module answers the one question the lane asks before
a status list starts: may it?

* THE SETTING is the ``litigation-status`` skill's
  ``litigation_list_allowance_per_cycle`` (``litigation_intake.list_allowance_of``).
  Unset is no cap; nothing is defaulted.
* THE CYCLE is the firm's one billing cycle, the chronology skill's authored
  anchor and effective-from (``cycle_window.py``, ADR 0087), read exactly the
  way the drafting and demand counts read it. No anchor is the calendar month.
* THE COUNT is status lists that reached delivery among jobs created in the
  window (``LitigationLedger.delivered_lists``). Held and failed jobs, a
  deadlines report, a staff reassignment and a rehearsal (which has no row)
  never count.

Like the chronology's page allowance, it is checked when the job STARTS, by
the lane, never at submit: a list past the allowance is held there with a
``list_allowance:`` reason (class ``smd_finishing``, SMD paged ``limit_held``),
before anything is spent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cycle_window import AnchorInvalid, cycle_window
from .litigation_intake import ListAllowanceInvalid, list_allowance_of, settings_of, skill_entry
from .litigation_ledger import LitigationLedger
from .medchron_ledger import cycle_from_customer_yaml


def list_allowance(customer_yaml: str, ledger: LitigationLedger, now: datetime, exclude: str = "") -> dict[str, Any]:
    """The allowance state for the cycle ``now`` falls in.

    ``authored: False`` is no cap. ``readable: False`` is an authored cap (or
    the cycle it runs on) the seat cannot read: the lane holds the list rather
    than run it unmetered. Otherwise ``used`` is the delivered status lists this
    cycle without ``exclude`` and ``remaining`` what is left."""
    try:
        allowance = list_allowance_of(settings_of(skill_entry(customer_yaml)))
    except ListAllowanceInvalid as exc:
        return {"authored": True, "readable": False, "problem": str(exc)}
    if allowance is None:
        return {"authored": False}
    try:
        anchor, effective_from = cycle_from_customer_yaml(customer_yaml)
    except AnchorInvalid as exc:
        return {"authored": True, "readable": False, "problem": str(exc)}
    window = cycle_window(now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), anchor, effective_from)
    used = ledger.delivered_lists(window.start, window.end, exclude)
    return {
        "authored": True,
        "readable": True,
        "unit": "lists",
        "allowance": allowance,
        "used": used,
        "remaining": max(0, allowance - used),
        "cycle": window.label,
        "cycle_start": window.start,
        "cycle_end": window.end,
    }


__all__ = ["list_allowance"]
