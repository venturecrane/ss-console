"""The relay's send binds the read-back it carried, in the same step (2026-11-19).

When the establishment plugin renders a read-back of a person's own rules, the
broker records it as OWED for the email being answered (``establish_readback_owed``).
The moment the relay has the sent message's id, this tells the broker
(``establish_readback_sent``), which turns what was owed for that email into a
binding keyed by the sent id: the reply to it then confirms those rules by its
``In-Reply-To``. Nothing waits for a later flush, and nothing lives only in the
gateway's memory, so a restart between the read-back and the reply loses
nothing. A reply that owed no read-back costs one broker round trip that binds
nothing.

Never raises: a binding that cannot be formed costs the person a numbered list
on their next reply, and is logged at WARNING with the ids.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared import act_broker, broker_socket

logger = logging.getLogger(__name__)

_SOCKET_ENV = act_broker.SOCKET_ENV  # the overlay's one home for the name
_TIMEOUT_SECONDS = 10
VERB_SENT = "establish_readback_sent"


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    socket_path = os.environ.get(_SOCKET_ENV, "")
    if not socket_path:
        return {"ok": True, "bound": False}  # no broker on this host: nothing to bind
    raw = broker_socket.request(socket_path, payload, timeout=_TIMEOUT_SECONDS)
    parsed = json.loads(raw.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def bind_sent(
    inbound_message_id: Any, sent_message_id: Any, recipient: Any, send: Any = None
) -> bool:
    """Bind whatever read-back was owed for ``inbound_message_id`` to the sent
    message. True when a binding formed. ``send`` replaces the broker call (tests)."""
    inbound = str(inbound_message_id or "").strip()
    sent = str(sent_message_id or "").strip()
    to = str(recipient or "").strip().lower()
    if not inbound or not to:
        return False
    call = send or _request
    try:
        answer = call(
            {
                "action": VERB_SENT,
                "inbound_message_id": inbound,
                "sent_message_id": sent,
                "recipient": to,
            }
        )
    except Exception:  # noqa: BLE001 - the reply is already sent; binding is best-effort
        logger.warning(
            "readback_send: could not bind the reply to %s (sent %s)", inbound, sent, exc_info=True
        )
        return False
    if not isinstance(answer, dict) or not answer.get("ok"):
        logger.warning("readback_send: broker refused the binding for %s (sent %s)", inbound, sent)
        return False
    return bool(answer.get("bound"))


__all__ = ["VERB_SENT", "bind_sent"]
