"""One Telegram Bot API call the overlay makes outside a Hermes turn: ``sendMessage``.

Hermes' own Telegram adapter answers the person who spoke. A message that one
partner asks the agent to pass to the OTHER partner (``shared.partner_messages``,
ADR 0097) lands in a chat nobody is speaking in, which Hermes has no path for,
so the overlay posts it with the seat's own bot token.

Same shape as ``shared.group_audience``: ``urllib`` rather than ``httpx``
(httpx logs every request URL at INFO, and a Bot API URL carries the token),
a constant scheme and host, an allowlisted method name, and every error
re-raised without the URL. The token never reaches a log.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from shared.secrets import get_secret

_API = "https://api.telegram.org"
_TIMEOUT_S = 10.0
#: Telegram's own ceiling for one text message.
MAX_TEXT = 4096


class TelegramSendError(RuntimeError):
    """The Bot API did not accept the message. The message never names the token."""


def send_message(chat_id: str, text: str) -> int:
    """Post ``text`` to ``chat_id`` as the seat's bot; return Telegram's message id.

    Raises :class:`TelegramSendError` on any failure (no token, network, a
    ``not ok`` reply). Plain text only: no parse mode, so nothing in ``text``
    is markup."""
    try:
        token = get_secret("TELEGRAM_BOT_TOKEN").strip()
    except KeyError:
        token = ""
    if not token:
        raise TelegramSendError("TELEGRAM_BOT_TOKEN is not set")
    if not str(chat_id or "").strip():
        raise TelegramSendError("no chat to send to")
    if not text.strip():
        raise TelegramSendError("nothing to send")
    if len(text) > MAX_TEXT:
        raise TelegramSendError(f"message is longer than Telegram allows ({MAX_TEXT} characters)")
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text}).encode()
    # Scheme and host are the constant _API; only the token and the fixed method
    # name vary, so no caller can steer this to file:// or another host.
    req = urllib.request.Request(f"{_API}/bot{token}/sendMessage", data=data, method="POST")
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected - scheme and host are the constant _API (https); only the token and the fixed method name vary, so no caller can steer this to file:// or another host
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            body: Any = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — strip the URL (it holds the token)
        raise TelegramSendError(f"Bot API sendMessage failed: {type(exc).__name__}") from None
    if not isinstance(body, dict) or not body.get("ok"):
        desc = body.get("description") if isinstance(body, dict) else None
        raise TelegramSendError(f"Bot API sendMessage returned not ok: {desc}")
    result = body.get("result")
    msg_id = result.get("message_id") if isinstance(result, dict) else None
    return int(msg_id) if isinstance(msg_id, int) else 0
