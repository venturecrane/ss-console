"""The ``smd_send_message`` tool's description and schema (ss#2258, ADR 0089).

Moved out of the plugin's ``__init__`` (module-size ratchet) when ADR 0089
section 9 added ``thread_id``. What the tool does is documented where it is
registered and executed (``__init__._smd_send_message``); this is only what the
model is shown.
"""

from __future__ import annotations

from typing import Any

SEND_TOOL_DESCRIPTION = (
    "Send an email from this Operator's own mailbox. Recipients must be people "
    "this engagement's configuration names; anyone else is refused. Subject to "
    "the authored send posture — an external send may be held for the owner's "
    "approval rather than sent immediately. To send AS a staff member instead, "
    "set `from` to that person's address: the full draft is emailed to them and "
    "it goes out from their address only when they reply to approve it."
)

SEND_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Recipient email addresses.",
        },
        "cc": {"type": "array", "items": {"type": "string"}},
        "bcc": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "string"},
        "text": {"type": "string", "description": "Plain-text body."},
        "html": {"type": "string", "description": "Optional HTML body."},
        "reply_to": {"type": "string"},
        # ss ADR 0089. Optional; absent means today's send, byte for byte. When
        # present the gate PROPOSES rather than sends (trust ``send_as``), drops
        # bcc / reply_to / html, and the broker transmits only on the named
        # staff member's emailed approval.
        "from": {
            "type": "string",
            "description": (
                "Optional. A staff member's address to send AS, for a message that "
                "person asked you to send for them. Only people this engagement "
                "authorizes may be named. The draft is emailed to them for "
                "approval and nothing is sent until they approve it. Omit to send "
                "from the Operator's own mailbox."
            ),
        },
        # ADR 0089 section 9. Read only beside ``from``, on a seat that sends as
        # staff from a Google Workspace mailbox: the broker reads the thread,
        # replies in it, and sends from the address the thread was received at.
        "thread_id": {
            "type": "string",
            "description": (
                "Optional, only with `from`, and only when the staff member's mail is "
                "Gmail: the Gmail thread id of the email this answers (from the "
                "mailbox search). The reply goes in that thread, from the address "
                "it was received at. Omit for a new email."
            ),
        },
    },
    "required": ["to", "subject"],
}

__all__ = ["SEND_TOOL_DESCRIPTION", "SEND_TOOL_SCHEMA"]
