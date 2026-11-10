"""hermes-smd-desk: the rep desk on a guide seat (ss-console #3946).

Registers ONLY on a seat whose persona enables the ``rep-desk`` skill; every
other seat gets nothing from this plugin (``tests/test_authored_seat_registration_unchanged.py``).

``reminder_set`` (only when ``rep-desk`` ``settings.reminder_store`` names a
store authored ``partition: sender`` and ``managed: true``). The model passes
the person's words for ``when`` and ``what``; code resolves and checks the
date, writes the reminder into the asking person's own partition, and returns
the exact receipt line the reply uses (``shared.reminders``). The person is the
turn's strict verified sender, keyed by the session id Hermes hands this
handler, and on the roster now. A scheduled turn, a chat turn, a turn that read
outside content and an unlisted sender set nothing.

Because the date was resolved by code and kept in the person's own store, the
handler adds the kept record to this session's identifier register, exactly the
bytes a ``record_store_read`` of it would return and seed (``record_store_read``
is a source of record, ``shared/provenance.py``). Without that, the reply that
repeats the receipt line would be held for a date "no tool read this turn".

``post_llm_call`` (on any seat that enables ``rep-desk``): counts the product
library's fixed not-covered answer on product-question turns by closed topic
key (``shared.gap_report``). Observer only.

Handlers raise a sentence Hermes surfaces; the hook never raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared import gap_report, reminders
from shared.tool_registration import register_wrapped_tool

logger = logging.getLogger(__name__)

TOOL_SET = "reminder_set"

TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    TOOL_SET: (
        "Keep a dated reminder for the person who emailed you, in their own private "
        "reminders. A meeting is a reminder with kind meeting. Pass their own words: when "
        "is what they said about the date and time (for example 'Thursday at 2pm', "
        "'November 12', 'tomorrow morning at 9 am', 'in two weeks'); what is what to be "
        "reminded of, without the date or time. The seat works out the date and checks "
        "it. On success the result gives a receipt line: put it in your reply exactly "
        "as written. On a refusal, tell the person what it says and ask for what is "
        "missing. It sends no calendar invitation and emails nobody else.",
        {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "The person's own words for the date and time.",
                    "minLength": 1,
                    "maxLength": reminders.MAX_WHEN_CHARS,
                },
                "what": {
                    "type": "string",
                    "description": "The person's own words for the reminder, one line, no date or time.",
                    "minLength": 1,
                    "maxLength": reminders.MAX_WHAT_CHARS,
                },
                "kind": {
                    "type": "string",
                    "enum": list(reminders.KINDS),
                    "description": "meeting for a meeting, otherwise reminder.",
                },
            },
            "required": ["when", "what"],
            "additionalProperties": False,
        },
    ),
}

NO_PERSON = (
    "reminder_set refused: reminders are kept for the person who emailed, and this turn "
    "has no verified sender listed on this seat. Nothing was kept."
)
TAINTED = (
    "reminder_set refused: this turn read outside content, so no reminder is taken from "
    "it. Ask the person to send the reminder in a message of their own. Nothing was kept."
)
NO_STORE = "reminder_set refused: this seat keeps no reminders. Nothing was kept."
#: A sender listed only through an ``@domain`` entry: the digest goes to people
#: listed by their own address, so a reminder kept for them would never arrive.
DOMAIN_ONLY = (
    "reminder_set refused: reminders are kept only for people listed on this seat by "
    "their own address, and this sender is not. Nothing was kept."
)


def _tainted(session_id: str) -> bool:
    try:
        from shared.inbound import SESSION_TAINT

        return SESSION_TAINT.is_tainted(session_id)
    except Exception:  # noqa: BLE001 - unknown taint reads as tainted
        return True


def _seed(session_id: str, record_text: str) -> None:
    """Add the kept record to this turn's identifier register (see module doc)."""
    try:
        from shared import provenance

        provenance.record_read(provenance.resolve_session(session_id), record_text)
    except Exception:  # noqa: BLE001 - a missed seed costs a held reply, never the write
        logger.warning("hermes-smd-desk: provenance seed failed", exc_info=True)


def _set_handler(args: dict[str, Any], **kwargs: Any) -> str:
    from shared.customer_config import CustomerConfig

    session_id = kwargs.get("session_id")
    sid = session_id if isinstance(session_id, str) else ""
    cfg = CustomerConfig.from_volume()
    store = reminders.reminder_store(cfg)
    if store is None:
        raise RuntimeError(NO_STORE)
    person = reminders.verified_person(sid, config=cfg)
    if person is None:
        raise RuntimeError(NO_PERSON)
    if not reminders.listed_by_address(cfg, person):
        raise RuntimeError(DOMAIN_ONLY)
    if _tainted(sid):
        raise RuntimeError(TAINTED)
    now_local = reminders.local_now(reminders.seat_zone(cfg))
    try:
        built = reminders.build(
            str(args.get("when") or ""), str(args.get("what") or ""), args.get("kind"), now_local
        )
        kept = reminders.keep(store, person, built, config=cfg)
    except (reminders.WhenError, reminders.ReminderError) as exc:
        raise RuntimeError(f"reminder_set refused: {exc}. Nothing was kept.") from exc
    _seed(sid, kept.to_json())
    receipt = reminders.receipt_line(kept)
    logger.info("hermes-smd-desk: reminder kept (%s)", kept.kind)
    return json.dumps(
        {
            "kept": True,
            "receipt": receipt,
            "instruction": "Put the receipt line in your reply exactly as written.",
        },
        ensure_ascii=False,
    )


def on_post_llm_call(**kwargs: Any) -> None:
    """Count a not-covered product answer (``shared.gap_report``). Observer only."""
    try:
        gap_report.note_turn(
            kwargs.get("session_id"),
            kwargs.get("user_message"),
            kwargs.get("conversation_history"),
            kwargs.get("assistant_response"),
        )
    except Exception:  # noqa: BLE001 - never raise out of a hook
        logger.warning("hermes-smd-desk: post_llm_call failed", exc_info=True)


_HANDLERS = {TOOL_SET: _set_handler}


def register(ctx: Any) -> None:
    """The hook on a seat that enables rep-desk; the tool when it also names a
    usable reminder store. Nothing anywhere else."""
    from shared.customer_config import CustomerConfig, CustomerConfigError

    try:
        cfg = CustomerConfig.from_volume()
    except CustomerConfigError as exc:
        logger.info("hermes-smd-desk: customer.yaml unreadable (%s); nothing registered", exc)
        return
    if reminders.desk_settings(cfg) is None:
        return
    ctx.register_hook("post_llm_call", on_post_llm_call)
    store = reminders.reminder_store(cfg)
    if store is None:
        logger.warning(
            "hermes-smd-desk: rep-desk names no usable reminder store "
            "(settings.reminder_store, partition sender, managed true); no reminder_set"
        )
        return
    for name, (description, schema) in TOOLS.items():
        register_wrapped_tool(
            ctx,
            name=name,
            toolset="rep_desk",
            schema=schema,
            handler=_HANDLERS[name],
            description=description,
            emoji="",
        )
    logger.info("hermes-smd-desk registered %s over store %s", TOOL_SET, store)


__all__ = ["TOOLS", "TOOL_SET", "on_post_llm_call", "register"]
