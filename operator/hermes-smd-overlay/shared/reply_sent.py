"""What happens the moment the reply relay has SENT a reply (2026-11-19).

Two things key off a reply actually going out, never off a draft the relay may
then hold:

* the read-back of a person's own rules it carried is bound to the sent
  message's id (``shared.readback_send``), so their reply to it confirms them;
* a plugin that must know a line reached the person registers here: the help
  line is recorded as offered only when the reply carrying it was sent.

The relay calls :func:`notify` once per sent reply, from its send record
(``plugins/hermes-smd-reply/relay_sent.py``). Never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared import readback_send

logger = logging.getLogger(__name__)

#: name -> ``(origin, text) -> None``. The latest registration of a name wins
#: (a plugin loaded twice registers twice).
_LISTENERS: dict[str, Callable[[Any, str], None]] = {}


def listen(name: str, listener: Callable[[Any, str], None]) -> None:
    _LISTENERS[name] = listener


def notify(origin: Any, sent_message_id: Any, text: Any) -> None:
    """The reply to ``origin``'s email went out with ``text``."""
    readback_send.bind_sent(
        getattr(origin, "message_id", ""), sent_message_id, getattr(origin, "sender_address", "")
    )
    for name, listener in list(_LISTENERS.items()):
        try:
            listener(origin, text if isinstance(text, str) else "")
        except Exception:  # noqa: BLE001 - one listener never costs the send record
            logger.warning("reply_sent: listener %s failed", name, exc_info=True)


__all__ = ["listen", "notify"]
