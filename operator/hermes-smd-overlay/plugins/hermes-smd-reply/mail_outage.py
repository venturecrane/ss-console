"""A reply the mail service refused: the one row and page, then the model only.

The backoff itself is in ``shared.mail_outage`` (its docstring has the
2026-11-12 incident): both brokers' ``send_reply`` refuse a reply to an email
whose backoff is running before the broker is called, and record each outage
refusal the broker makes. This is what the reply plugin does with a reply that
failed that way, reached from its ``_send_failed``:

* the email's FIRST outage refusal writes ONE ``REPLY_HELD`` row with reason
  ``mail_outage`` (the plugin's ``_held``, which also tells the model and counts
  toward the redraft cap) and pages SMD once;
* every later one, held back by the backoff or refused again, tells the model
  and writes nothing, so a model that tries again does not fill the ledger.

The reply that later goes through writes ``REPLY_SENT`` with ``in_reply_to``,
which closes work health's "held" line for the email (``_held_then_sent``).
"""

from __future__ import annotations

import sys
from typing import Any

from shared.mail_outage import OUTAGES

#: The REPLY_HELD reason, the notice reason and the page reason.
REASON = "mail_outage"
_PACKAGE = __name__.rpartition(".")[0]


def held(origin: Any, adapter: str, sender_class: str, body_digest: str, matter_ref: Any) -> bool:
    """True when this email's failed reply was a mail outage, now recorded.

    Plugin names are read at call time, so the plugin loaded last writes."""
    first = OUTAGES.take(origin.message_id)
    if first is None:
        return False
    plugin = sys.modules[_PACKAGE]
    recipient = plugin._reply_recipient(origin)[0]
    if first:
        plugin._held(REASON, origin, _matter_ref=matter_ref)
        plugin._notify_hold(
            reason=REASON,
            sender=recipient,
            sender_class=sender_class,
            adapter=adapter,
            message_id=origin.message_id,
            body_digest=body_digest,
        )
        return True
    plugin._HOLD_NOTICES.record(
        tool_call_id=getattr(plugin._CURRENT_CALL, "tool_call_id", "") or "",
        reason=REASON,
        recipient=recipient,
        message_id=origin.message_id,
    )
    return True


__all__ = ["REASON", "held"]
