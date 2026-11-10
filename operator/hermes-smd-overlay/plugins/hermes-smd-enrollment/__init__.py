"""hermes-smd-enrollment: an administrator asks the guide for an invite code (ADR 0106).

One tool, registered only on a guide seat that authors
``sender_enrollment: {enabled: true}``:

``invite_code_issue``  "I need a code for <name>" from an administrator. The
    broker mints a code tied to that name and emails it to the administrator,
    in the thread they asked in, with fixed wording. The tool's result names
    the code's id and that it was sent. The code itself never reaches this
    process, so it is in no tool result, transcript, log or audit row.

WHO MAY ASK, decided here in code and again by the broker:

* the turn was opened by a verified email (the router's recorded origin for
  this session); a chat turn has none, and on a guide seat no chat id is
  mapped to an administrator, so a request in chat is refused with a pointer
  to email;
* that email's sender is on ``scope.admins`` exactly; a listed or enrolled
  person who is not an administrator gets the fixed decline :data:`DECLINE`;
* the turn read no untrusted content (a tainted turn is refused);
* the name is a plain name (``shared.sender_enrollment.valid_name``), else the
  fixed :data:`shared.sender_enrollment.NAME_REFUSAL`.

The handler raises nothing to Hermes: every refusal is a sentence for the
model, and nothing is issued by one.
"""

from __future__ import annotations

import logging
from typing import Any

from shared import sender_enrollment as se
from shared.tool_registration import register_wrapped_tool

logger = logging.getLogger(__name__)

TOOL_ISSUE = "invite_code_issue"

#: Fixed wording for a person who may not ask for a code.
DECLINE = (
    "Invite codes are issued only to the guide seat's administrators at SMD Services. "
    "If someone needs access, ask SMD Services at team@smd.services."
)
CHAT_REFUSAL = (
    "Invite codes are issued by email only: an administrator asks from their own "
    "address, and the code is sent back in that email thread."
)

TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    TOOL_ISSUE: (
        "Issue an invite code for one named person, when an administrator of this seat "
        "asks (for example 'I need a code for Jane Smith'). The code is emailed to that "
        "administrator in this thread by the platform; you never see it and must never "
        "write one. Pass the person's name exactly as given. The result says whether it "
        "was sent and gives the code's id, or gives a refusal sentence: pass a refusal "
        "on to the person in your own words, unchanged in meaning.",
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The person's name, as the administrator gave it.",
                    "minLength": 1,
                    "maxLength": se.NAME_MAX,
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 90,
                    "description": "How many days the code works. Default 14.",
                },
                "max_uses": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "How many people may connect with it. Default 1.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
}

_REFUSED = "{tool} refused: {why} No code was issued."
_DONE = (
    "DONE: invite code {code_id} for {name} was emailed to you in this thread. "
    "Tell the administrator it is in their inbox, in one line, and add nothing else."
)


def _tainted(session_id: str) -> bool:
    try:
        from shared.inbound import SESSION_TAINT

        return SESSION_TAINT.is_tainted(session_id)
    except Exception:  # noqa: BLE001 - unknown taint reads as tainted
        return True


def _origin(session_id: str) -> Any:
    try:
        from shared.inbound import SESSION_INBOUND_ORIGIN

        return SESSION_INBOUND_ORIGIN.get(session_id)
    except Exception:  # noqa: BLE001 - no origin is no email
        return None


def _bounded(value: Any, default: int, high: int) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
        return None
    return value


def issue_handler(args: dict[str, Any], **kwargs: Any) -> str:
    session_id = str(kwargs.get("session_id") or "")
    try:
        from shared.customer_config import CustomerConfig

        cfg = CustomerConfig.from_volume()
    except Exception:  # noqa: BLE001
        return _REFUSED.format(tool=TOOL_ISSUE, why="this seat's configuration could not be read.")
    if not se.enabled_in(cfg.raw):
        return _REFUSED.format(tool=TOOL_ISSUE, why="this seat does not issue invite codes.")
    origin = _origin(session_id)
    if (
        origin is None
        or not getattr(origin, "sender_address", "")
        or not getattr(origin, "message_id", "")
    ):
        return _REFUSED.format(tool=TOOL_ISSUE, why=CHAT_REFUSAL)
    if not cfg.sender_is_admin(origin.sender_address):
        return _REFUSED.format(tool=TOOL_ISSUE, why=DECLINE)
    if _tainted(session_id):
        return _REFUSED.format(
            tool=TOOL_ISSUE,
            why="this conversation read outside content; ask for the code in a new email of your own.",
        )
    args = args if isinstance(args, dict) else {}
    name = args.get("name")
    if not se.valid_name(name):
        return _REFUSED.format(tool=TOOL_ISSUE, why=se.NAME_REFUSAL)
    days = _bounded(args.get("days"), 14, 90)
    uses = _bounded(args.get("max_uses"), 1, 100)
    if days is None or uses is None:
        return _REFUSED.format(
            tool=TOOL_ISSUE, why="days runs from 1 to 90 and max uses from 1 to 100."
        )
    try:
        result = se.issue(
            message_id=origin.message_id,
            admin=origin.sender_address,
            name=str(name),
            days=days,
            max_uses=uses,
        )
    except Exception as exc:  # noqa: BLE001 - a fault is a refusal, never a raise
        logger.warning("hermes-smd-enrollment: issue failed (%s)", type(exc).__name__)
        return _REFUSED.format(tool=TOOL_ISSUE, why="the code could not be issued and sent.")
    code_id = str(result.get("code_id") or "") if isinstance(result, dict) else ""
    if not code_id or result.get("sent") is not True:
        return _REFUSED.format(tool=TOOL_ISSUE, why="the code could not be issued and sent.")
    logger.info("hermes-smd-enrollment: code %s issued to an administrator", code_id)
    return _DONE.format(code_id=code_id, name=name)


_HANDLERS = {TOOL_ISSUE: issue_handler}


def register(ctx: Any) -> None:
    """Register the tool on a guide seat that switches enrollment on; nothing elsewhere."""
    try:
        from shared.customer_config import CustomerConfig

        enabled = se.enabled_in(CustomerConfig.from_volume().raw)
    except Exception:  # noqa: BLE001
        logger.warning("hermes-smd-enrollment: config unreadable; no tool")
        return
    if not enabled:
        logger.info("hermes-smd-enrollment: enrollment not switched on; no tool")
        return
    for name, (description, schema) in TOOLS.items():
        register_wrapped_tool(
            ctx,
            name=name,
            toolset="enrollment",
            schema=schema,
            handler=_HANDLERS[name],
            description=description,
            emoji="",
        )
    logger.info("hermes-smd-enrollment registered %d tool(s)", len(TOOLS))
