"""hermes-smd-partner-messages: a partner's message reaches the other partner (ADR 0097).

One tool, registered only on a seat that authors ``people:`` (ADR 0091):

``partner_message_send``  the words one partner asked the agent to tell the
    other. The recipient is the other person on the seat, decided in code
    from the turn's structural speaker; the model names nobody. Delivered at
    once to that person's own chat (Telegram DM, else email), under an
    attribution line, and recorded. No approval, no confirm ceiling.

One hook, ``pre_llm_call``: a private turn (a partner's DM or their own email)
is shown the messages passed between the two of them in the last week, because
a message the bot posted into the other partner's DM is in no Hermes session's
history, and "tell her thanks" has to mean something.

WHY NOT THE EMAIL TOOL. The seat's outbound email tool is built for firm seats
and sits under the confirm ceiling. On 2026-10-21 the agent reached for it,
the send was held, and the agent invented an "account owner" approval to
explain the hold (``shared.partner_messages`` module doc). A purpose-made tool
cannot name an address, a chat or a third person, and has no ceiling to hit.

WHO MAY SEND. Either partner, from their own conversation or the group; the
speaker is resolved from verified session fields only (never the prompt). A
scheduled run has no speaker and is refused. A turn that read outside content
is refused, the same injection defense the settings tool follows: a forwarded
email must not be able to put words in a partner's mouth.

The handler decides everything itself. It raises nothing to Hermes: every
refusal is a sentence for the model, and nothing is sent by one.
"""

from __future__ import annotations

import logging
from typing import Any

from shared import partner_messages as pm
from shared import space as space_mod
from shared.tool_registration import register_wrapped_tool

logger = logging.getLogger(__name__)

TOOL_SEND = "partner_message_send"

TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    TOOL_SEND: (
        "Pass a message from the partner you are talking with to the other "
        "partner. Use it when they ask you to tell, pass along, let the other "
        "know, or send the other something. The text is their message in their "
        "own words; you add no greeting, no sign-off and no commentary. It goes "
        "to the other partner's own chat at once, marked as from this partner. "
        "Nobody approves it and nothing waits. The result says it was delivered; "
        "restate that to the person in one line.",
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message, in the sender's own words, as they want it passed.",
                    "minLength": 1,
                    "maxLength": pm.MAX_TEXT,
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    ),
}

_REFUSED = "{tool} refused: {why}. Nothing was sent."
_DONE = "DONE: {line} Tell the person it reached them, in one line, and add nothing else."


def _turn(session_id: str) -> tuple[space_mod.SpaceConfig, space_mod.Space] | str:
    try:
        cfg = space_mod.space_config()
    except space_mod.SpaceConfigError:
        return "this seat's people could not be read"
    if cfg is None:
        return "this seat is not shared by people"
    current = space_mod.resolve_space(session_id or None, config=cfg)
    if current is None:
        return "this conversation's space could not be resolved"
    return cfg, current


def _tainted(session_id: str) -> bool:
    try:
        from shared.inbound import SESSION_TAINT

        return SESSION_TAINT.is_tainted(session_id)
    except Exception:  # noqa: BLE001 — unknown taint reads as tainted
        return True


def _speaker(
    cfg: space_mod.SpaceConfig, current: space_mod.Space, user_id: str, session_id: str
) -> space_mod.Person | None:
    """The person asking: the DM's or the private email's person, the authored
    person whose Telegram id spoke in the group, or the verified sender of a
    joint email thread. ``None`` for a cron run or anything else."""
    if current.source in (space_mod.SOURCE_TELEGRAM_DM, space_mod.SOURCE_EMAIL):
        return cfg.person(current.person_id or "")
    if current.source == space_mod.SOURCE_TELEGRAM_GROUP:
        return cfg.person_for_telegram(user_id)
    if current.source == space_mod.SOURCE_EMAIL_JOINT:
        from shared.inbound import SESSION_INBOUND_ORIGIN

        origin = SESSION_INBOUND_ORIGIN.get(session_id)
        return cfg.person_for_email(origin.sender_address) if origin is not None else None
    return None


def _send_handler(args: dict[str, Any], **kwargs: Any) -> str:
    session_id = str(kwargs.get("session_id") or "")
    turn = _turn(session_id)
    if isinstance(turn, str):
        return _REFUSED.format(tool=TOOL_SEND, why=turn)
    cfg, current = turn
    from shared import turn_principal

    speaker = _speaker(cfg, current, turn_principal.current().user_id, session_id)
    if speaker is None:
        return _REFUSED.format(
            tool=TOOL_SEND,
            why="a message is passed for one of the two of you, asked for in your own "
            "conversation or the shared one; a scheduled run cannot ask",
        )
    if _tainted(session_id):
        return _REFUSED.format(
            tool=TOOL_SEND,
            why="this conversation read outside content, so a message is not taken "
            "from it; ask the person to send the message in a new message of their own",
        )
    text = args.get("text") if isinstance(args, dict) else None
    if not isinstance(text, str):
        return _REFUSED.format(tool=TOOL_SEND, why="text must be the message as words")
    try:
        rec = pm.send(text, cfg=cfg, sender=speaker, session_id=session_id)
    except pm.PartnerMessageError as exc:
        return _REFUSED.format(tool=TOOL_SEND, why=str(exc))
    except Exception:  # noqa: BLE001 — a surprise is a refusal, never a raise
        logger.exception("hermes-smd-partner-messages: send failed")
        return _REFUSED.format(tool=TOOL_SEND, why="the message could not be delivered")
    logger.info(
        "hermes-smd-partner-messages: %s -> %s delivered by %s (%s)",
        rec.sender,
        rec.recipient,
        rec.channel,
        rec.id,
    )
    return _DONE.format(line=pm.describe(rec, cfg))


def on_pre_llm_call(**kwargs: Any) -> dict | None:
    """Show a private turn the messages recently passed between the partners."""
    try:
        session_id = str(kwargs.get("session_id") or "")
        cfg = space_mod.space_config()
        if cfg is None:
            return None
        current = space_mod.resolve_space(session_id or None, config=cfg)
        block = pm.context_block(current, cfg)
        return {"context": block} if block else None
    except Exception:  # noqa: BLE001 — never raise out of a hook
        logger.warning("hermes-smd-partner-messages: pre_llm_call failed", exc_info=True)
        return None


_HANDLERS = {TOOL_SEND: _send_handler}


def register(ctx: Any) -> None:
    """Register the tool and the hook on a seat that authors ``people:``; nothing elsewhere."""
    try:
        cfg = space_mod.space_config()
    except space_mod.SpaceConfigError:
        logger.warning("hermes-smd-partner-messages: people unreadable; no tool")
        return
    if cfg is None:
        logger.info("hermes-smd-partner-messages: no people on this seat; no tool")
        return
    for name, (description, schema) in TOOLS.items():
        register_wrapped_tool(
            ctx,
            name=name,
            toolset="partner_messages",
            schema=schema,
            handler=_HANDLERS[name],
            description=description,
            emoji="",
        )
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info("hermes-smd-partner-messages registered %d tool(s)", len(TOOLS))
