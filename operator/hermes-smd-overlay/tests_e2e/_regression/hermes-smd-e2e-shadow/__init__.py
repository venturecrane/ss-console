"""hermes-smd-e2e-shadow: harness-only fixtures, loaded by Hermes' own plugin
loader from the temp seat's plugins dir (see ``plugin.yaml``).

* ``email_send`` is in ``shared.action_classes.BANNED_TOOLS``: no exposure an
  engagement could author permits it. Registering a tool under that name lets
  ``tests_e2e/test_hooks.py`` script the model into calling it and prove the
  trust gate refuses it in ``pre_tool_call``, before Hermes dispatches: the
  handler below records every call it receives, and must receive none.
* ``send_0813._smd_send_message`` is the 2026-08-13 handler shape, which the
  regression test swaps in behind ``smd_send_message``.
"""

from __future__ import annotations

import json
from typing import Any

BANNED_SEND_TOOL = "email_send"
TOOLSET = "smd_e2e_shadow"

#: Every call the banned tool's handler received. The gate must keep it empty.
BANNED_HANDLER_CALLS: list[dict[str, Any]] = []

_BANNED_SCHEMA: dict[str, Any] = {
    "name": BANNED_SEND_TOOL,
    "description": "Harness fixture: a send under a name the trust gate bans.",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["to", "subject"],
    },
}


def _banned_send(args: dict[str, Any], **kwargs: Any) -> str:
    BANNED_HANDLER_CALLS.append(dict(args))
    return json.dumps({"sent": True})


def register(ctx: Any) -> None:
    ctx.register_tool(
        name=BANNED_SEND_TOOL,
        toolset=TOOLSET,
        schema=_BANNED_SCHEMA,
        handler=_banned_send,
        description=_BANNED_SCHEMA["description"],
    )
