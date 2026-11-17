"""A reply the mail service did not let through: one row, then a backoff.

WHY (2026-11-12). Graph answered 5xx for half an hour. The broker's participant
fence could not read the email a reply answered, so it refused the send, and
the refusal reached the reply plugin as an ordinary failed send: the reply was
refused fourteen times in thirty-four minutes, each one a broker read of a mail
service that was down and each one a fresh row, with nothing that said why.
Work health then showed "held and nobody was told" with no cause.

The broker now refuses that case as a mail outage (``MsGraphMailOutage`` /
``AgentMailMailOutage``, ss-console ``workspace_broker/participant_lookup.py``
``vendor_outage``). This module is the agent process's half. Both brokers'
``send_reply`` run their broker call through :func:`checked`:

* a reply for an email whose backoff is running is refused HERE, before the
  broker is called, with the same outage code the broker uses, so every caller
  handles it the one way it already handles the broker's;
* an outage refusal from the broker is recorded: the first for an email starts
  a one-minute wait, each further one lengthens it, up to fifteen minutes;
* a reply that goes through clears the email.

What the reply plugin learns from it (:meth:`Outages.take`): whether this
email's failure was an outage and whether it was the FIRST, which is the one
that writes the single ``REPLY_HELD`` row with reason ``mail_outage`` and pages
(plugin ``mail_outage.held``). The send that later goes through writes
``REPLY_SENT`` with ``in_reply_to``, which closes the work-health line.

In memory and bounded: a restart forgets the backoff, which costs at most one
more broker read.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shared.workspace_broker import MAIL_OUTAGE_CODES, BrokerError

#: Seconds to wait after the first, second, ... outage refusal for one email.
BACKOFF_S = (60.0, 120.0, 300.0, 600.0, 900.0)
#: The message a reply refused during its backoff carries.
WAITING = "the mail service is not answering; this reply waits for its retry, nothing was sent"
_KEEP = 512


@dataclass
class _State:
    failures: int
    retry_at: float


class Outages:
    """Per-email outage backoff, and the verdict the reply plugin reads."""

    def __init__(self, keep: int = _KEEP, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._state: OrderedDict[str, _State] = OrderedDict()
        self._verdicts: OrderedDict[str, bool] = OrderedDict()
        self._keep = keep
        self.clock = clock

    def waiting(self, message_id: str) -> bool:
        """True while a reply to this email must not reach the broker yet."""
        with self._lock:
            state = self._state.get(message_id) if message_id else None
            return state is not None and self.clock() < state.retry_at

    def failed(self, message_id: str) -> bool:
        """Record an outage refusal; True only for the email's first."""
        with self._lock:
            state = self._state.get(message_id)
            failures = state.failures + 1 if state else 1
            wait = BACKOFF_S[min(failures, len(BACKOFF_S)) - 1]
            self._state[message_id] = _State(failures, self.clock() + wait)
            self._trim(self._state)
            return state is None

    def note(self, message_id: str, first: bool) -> None:
        with self._lock:
            self._verdicts[message_id] = first
            self._trim(self._verdicts)

    def take(self, message_id: str) -> bool | None:
        """None when this email's last failure was no mail outage; else whether
        it was the first outage refusal for the email."""
        with self._lock:
            return self._verdicts.pop(message_id, None)

    def cleared(self, message_id: str) -> None:
        with self._lock:
            self._state.pop(message_id, None)
            self._verdicts.pop(message_id, None)

    def _trim(self, table: OrderedDict) -> None:
        while len(table) > self._keep:
            table.popitem(last=False)

    def reset(self) -> None:
        """Tests only."""
        with self._lock:
            self._state.clear()
            self._verdicts.clear()


#: The agent process's one table.
OUTAGES = Outages()


def checked(message_id: str, code: str, call: Callable[[], Any]) -> Any:
    """Run one broker reply to the email ``message_id`` through its backoff.
    ``code`` is the channel's outage code, raised here while the email waits."""
    if OUTAGES.waiting(message_id):
        OUTAGES.note(message_id, False)
        raise BrokerError(WAITING, code=code)
    try:
        result = call()
    except BrokerError as exc:
        if exc.code in MAIL_OUTAGE_CODES:
            OUTAGES.note(message_id, OUTAGES.failed(message_id))
        raise
    OUTAGES.cleared(message_id)
    return result


__all__ = ["BACKOFF_S", "OUTAGES", "Outages", "WAITING", "checked"]
