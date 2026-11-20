"""The quiet help line's model-facing block and its offered mark (ss-console#3423).

The block is what the per-turn hook hands the model when a person's first email
is due the line (``_help_pointer_block`` in the plugin). The line counts as
offered only once a reply carrying it is SENT (review 2026-11-19): a draft the
relay then holds is not an offer, and neither is a line handed to the model and
never sent. The relay's send record calls :func:`on_reply_sent` through
``shared.reply_sent``.
"""

from __future__ import annotations

import logging
from typing import Any

from shared import reply_sent, request_index

from . import help_state

logger = logging.getLogger(__name__)

#: The block header the reply's line arrives under. Grep-able.
HEADER = "HELP LINE (platform-decided for this email)"


def block(line: str) -> str:
    return (
        f"{HEADER}: end your reply with this line, exactly as written, "
        f"as its own last line:\n{line}\n"
        "Leave it out only if this reply answers how to work with you, delivers a "
        "draft or confirms work you queued, "
        "declines an action reserved to administrators, or goes to anyone not on "
        "your roster. If you leave it out, say nothing about help. "
        "It is recorded as offered when a reply carrying it is sent: do not call "
        "operator_help_signals."
    )


#: The draft tools whose body can carry the help line (the reply relay's set).
_DRAFT_TOOLS = frozenset(
    {"mcp_agentmail_create_draft", "agentmail:create_draft", "mcp_msgraph_mail_create_draft"}
)


def _draft_text(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    return " ".join(
        str(args.get(k) or "")
        for k in ("text", "body_text", "html")
        if isinstance(args.get(k), str)
    )


def on_post_tool_call(**kwargs: Any) -> None:
    """A drafted reply records nothing (review 2026-11-19): the relay may hold
    it. The offered mark is written by :func:`on_reply_sent`, when it is sent."""
    return None


def on_reply_sent(origin: Any, text: str) -> None:
    """The relay sent a reply: if it carried the help line, record it offered
    for the person it went to. Never raises."""
    try:
        if help_state.POINTER_LINE not in (text or ""):
            return
        sender = request_index.bare_sender(getattr(origin, "sender_address", None))
        if sender is not None:
            help_state.record(help_state.KIND_POINTER, sender)
    except Exception:  # noqa: BLE001 - recording never breaks the send record
        logger.warning("hermes-smd-initiation: help line not recorded", exc_info=True)


reply_sent.listen("help_line", on_reply_sent)
