"""What happens the moment the reply relay has SENT a reply (2026-11-19).

A plugin that must know a line reached the person registers here, so it keys
off a reply actually going out, never off a draft the relay may then hold: the
help line is recorded as offered only when the reply carrying it was sent
(``plugins/hermes-smd-initiation/help_line.py``).

The relay calls :func:`notify` once per sent reply, from its send record
(``plugins/hermes-smd-reply/relay_sent.py``). Never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: name -> ``(origin, text) -> None``. The latest registration of a name wins
#: (a plugin loaded twice registers twice).
_LISTENERS: dict[str, Callable[[Any, str], None]] = {}


def listen(name: str, listener: Callable[[Any, str], None]) -> None:
    _LISTENERS[name] = listener


def notify(origin: Any, sent_message_id: Any, text: Any) -> None:
    """The reply to ``origin``'s email went out with ``text``."""
    del sent_message_id  # the listeners key on the origin; kept for the call shape
    for name, listener in list(_LISTENERS.items()):
        try:
            listener(origin, text if isinstance(text, str) else "")
        except Exception:  # noqa: BLE001 - one listener never costs the send record
            logger.warning("reply_sent: listener %s failed", name, exc_info=True)


__all__ = ["listen", "notify"]
