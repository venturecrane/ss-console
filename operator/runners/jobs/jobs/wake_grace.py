"""A gateway that is not up yet does not spend a DELIVER wake (2026-10-15).

A wake pending across a deploy met a gateway that was already gone, or still
starting (about four minutes at 1 GB, ``operator/CLAUDE.md``; the daemon starts
before it), and spent its five attempts in under two minutes. That loss paged
SMD as ``proof:wake_lost``: a page for our own deploy, and for a delivery that
had in fact finished, a person never told.

So a wake that could not reach the gateway at all waits, without spending an
attempt, for ``WAKE_UNREACHABLE_GRACE_SECONDS`` of THIS process. The clock is
kept in memory on the daemon (``Daemon._unreachable_since``), never on the
volume: time the Machine spent down must not use up the next boot's grace.
After the grace a wake spends attempts as before, so a gateway that never
comes back is still a loud loss.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: How long an unreachable gateway may stay so before a wake spends attempts.
WAKE_UNREACHABLE_GRACE_SECONDS = 900


def failed_outcome(exc: BaseException, *, sent: bool) -> str:
    """The wake decision for a dispatch that raised.

    Never connected: the gateway did not get it (``unreachable``). Once
    connected, any error stays ``timeout after send``, consumed, because bytes
    may already be with the gateway and a duplicate DELIVER is worse than a
    lost one (the at-most-once rule of ``Daemon.dispatch_wakes``)."""
    if not sent:
        return f"unreachable ({type(exc).__name__})"
    return "timeout after send"


def waiting(d: Any, job_id: str, outcome: str) -> bool:
    """True when this wake should stay pending without spending an attempt."""
    if not outcome.startswith("unreachable"):
        d._unreachable_since.pop(job_id, None)
        return False
    since = d._unreachable_since.setdefault(job_id, d.clock())
    if d.clock() - since < WAKE_UNREACHABLE_GRACE_SECONDS:
        logger.info("gateway not reachable yet (%s); wake for %s stays pending", outcome, job_id)
        return True
    return False


__all__ = ["WAKE_UNREACHABLE_GRACE_SECONDS", "failed_outcome", "waiting"]
