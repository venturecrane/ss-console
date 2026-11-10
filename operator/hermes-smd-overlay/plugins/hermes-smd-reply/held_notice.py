"""When a reply is held at the end of a turn, the sender is told, in fixed words.

WHY (ss-console #3946, gate 18). A turn whose reply the seat's checks held
ends with the sender hearing nothing: the model is told about the hold, but the
model's turn is over, and the only other telling is a page to SMD. On a seat
whose people write in to practise and keep notes, silence reads as the seat not
working. A seat that authors ``send_policy.reply.held_notice: true`` instead has
CODE send the sender one fixed line, never a model's words:

    We could not answer this message. Please send it again.

No amount, no time, no promise, nothing from the held reply. It goes once per
email, only when that email got no reply at all, only to a sender on the
roster, through the broker's recipient-checked reply (the broker re-reads the
email and refuses anyone not listed), and never for a reply that is queued for
automatic release (that reply is going out on its own).

THE REDRAFT CAP. On the same seat, a held draft may be redrafted at most
``send_policy.reply.max_redrafts`` times (default 2). The next draft for that
email is refused without relaying, and the model is told plainly to stop; the
fixed line then goes at the end of the turn. A model that keeps redrafting a
reply its checks keep holding spends the turn and still says nothing.

A seat that authors neither key behaves exactly as before: no line, no cap.
The counts are in memory and per email: a restart forgets them, which costs at
most one extra redraft round and, at worst, a second line after a restart that
landed mid-turn.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

#: The one line the sender receives. Fixed: no model writes it, and nothing from
#: the held reply is in it.
FIXED_LINE = "We could not answer this message. Please send it again."

#: What the model is told when its draft hits the cap.
REDRAFT_LIMIT_DETAIL = (
    "This reply has been held too many times. Do not draft it again. "
    "The seat tells the sender it could not answer."
)

#: The reason code of a draft refused at the cap.
REDRAFT_LIMIT_REASON = "redraft_limit"

DEFAULT_MAX_REDRAFTS = 2
MAX_MAX_REDRAFTS = 10
_KEEP = 2048


@dataclass(frozen=True)
class Policy:
    """The seat's authored posture. ``enabled`` False is today's behaviour."""

    enabled: bool = False
    max_redrafts: int = DEFAULT_MAX_REDRAFTS


OFF = Policy()


def policy(raw_config: Any) -> Policy:
    """``send_policy.reply.held_notice`` and ``max_redrafts`` from the seat's
    raw config. Anything unreadable is OFF (today's behaviour)."""
    if not isinstance(raw_config, dict):
        return OFF
    block = raw_config.get("send_policy")
    reply = block.get("reply") if isinstance(block, dict) else None
    if not isinstance(reply, dict) or reply.get("held_notice") is not True:
        return OFF
    cap = reply.get("max_redrafts", DEFAULT_MAX_REDRAFTS)
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= MAX_MAX_REDRAFTS:
        cap = DEFAULT_MAX_REDRAFTS
    return Policy(enabled=True, max_redrafts=cap)


class HoldCounter:
    """How many times each email's reply was held. Bounded, thread-safe."""

    def __init__(self, keep: int = _KEEP) -> None:
        self._lock = threading.Lock()
        self._counts: OrderedDict[str, int] = OrderedDict()
        self._keep = keep

    def note(self, message_id: str) -> int:
        if not message_id:
            return 0
        with self._lock:
            n = self._counts.get(message_id, 0) + 1
            self._counts[message_id] = n
            self._counts.move_to_end(message_id)
            while len(self._counts) > self._keep:
                self._counts.popitem(last=False)
            return n

    def count(self, message_id: str) -> int:
        with self._lock:
            return self._counts.get(message_id, 0)


class Once:
    """True the first time a key is claimed, False after. Bounded."""

    def __init__(self, keep: int = _KEEP) -> None:
        self._lock = threading.Lock()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._keep = keep

    def claim(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            while len(self._seen) > self._keep:
                self._seen.popitem(last=False)
            return True


def over_cap(pol: Policy, holds: int) -> bool:
    """Whether a new draft for an email whose reply was held ``holds`` times
    is past the cap: the first draft plus ``max_redrafts`` redrafts relay."""
    return pol.enabled and holds > pol.max_redrafts


__all__ = [
    "DEFAULT_MAX_REDRAFTS",
    "FIXED_LINE",
    "HoldCounter",
    "OFF",
    "Once",
    "Policy",
    "REDRAFT_LIMIT_DETAIL",
    "REDRAFT_LIMIT_REASON",
    "over_cap",
    "policy",
]
