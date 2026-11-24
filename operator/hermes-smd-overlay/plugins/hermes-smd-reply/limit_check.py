"""The limit rule of the relay's step 5 (2026-11-24, A&P matter-file request).

The Operator told an attorney "I cannot attach files to this reply" and then
"my document reader cannot open that format ... This is a limit of how I read
files". The owner's rule: the Operator never states a limit of its own to a
person; it says ``reply_could_not.LOOK_INTO`` and SMD does the work, paged by
that sentence's ``looking_into`` code on the sent row. A reply to anyone but
SMD that states one is held with the sentence to use, under the same redraft
cap as step 5a, counted on its own (``turn_says.limit_hold``); past it the hold
stands and pages SMD.

Split out of ``relay_checks`` (its size ceiling). This module decides; the
relay records the hold through the plugin's own ``_held``, as every step does.
"""

from __future__ import annotations

from typing import Any

from shared import turn_says

from . import held_notice, notice

#: The hold reason of a reply that states a limit of the Operator's own.
LIMIT_HOLD_REASON = "limit_named"


def stated(
    origin: Any, reply_to: Any, text: str, html: str, holds: held_notice.HoldCounter
) -> tuple[str, str] | None:
    """``(rule, detail)`` when a reply's ``text`` or ``html`` states a limit of
    the Operator's own, else None; the relay records the hold. ``holds`` is the
    relay's own redraft counter for this rule, kept apart from 5a's (review of
    #4210), so a fixed-wording hold on the same email never uses up these
    redrafts."""
    return turn_says.limit_hold(
        reply_to, text, html, lambda: holds.note(origin.message_id), notice.LIMIT_STOP
    )


__all__ = ["LIMIT_HOLD_REASON", "stated"]
