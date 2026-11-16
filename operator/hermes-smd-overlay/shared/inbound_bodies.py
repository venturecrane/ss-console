"""The whole plain-text body of a recent verified inbound, keyed by its message id.

Read only by deterministic code that checks a forwarded draft's wording on a
guide seat (:func:`shared.material_check.turn_material`). Kept apart from
:class:`shared.inbound.InboundOrigin` on purpose: the origin is attribution
(who sent it, which message), recorded and compared in many places, and an
email body is never part of it. Nothing here is written to disk or logged, the
model never reads it (it already reads the body in the turn's own message), and
it holds only the last :data:`MAX_BODIES` messages, so a body lasts minutes on a
busy seat.

The webhook router notes each verified inbound's body as it derives the origin;
:func:`body_for` returns it, or ``""`` when the message is unknown.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

#: How many recent bodies are kept, oldest dropped first.
MAX_BODIES = 64

_BODIES: OrderedDict[str, str] = OrderedDict()
_LOCK = threading.Lock()


def note(message_id: object, body_text: object) -> None:
    """Keep ``body_text`` for ``message_id``. Anything not a non-empty string is ignored."""
    if not isinstance(message_id, str) or not message_id or not isinstance(body_text, str):
        return
    with _LOCK:
        _BODIES[message_id] = body_text
        _BODIES.move_to_end(message_id)
        while len(_BODIES) > MAX_BODIES:
            _BODIES.popitem(last=False)


def body_for(message_id: object) -> str:
    """The body noted for ``message_id``, or ``""``."""
    if not isinstance(message_id, str) or not message_id:
        return ""
    with _LOCK:
        return _BODIES.get(message_id, "")


def _reset_for_tests() -> None:
    with _LOCK:
        _BODIES.clear()


__all__ = ["MAX_BODIES", "body_for", "note"]
