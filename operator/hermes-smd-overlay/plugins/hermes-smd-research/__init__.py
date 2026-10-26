"""hermes-smd-research: the sealed web reader, ``web_research`` (ADR 0099).

One tool, registered only on a seat whose trusted customer.yaml authors
``connectors.WebSearch.sealed_reader: true``. Every other seat registers
nothing, so this plugin is inert on them by construction.

The reading, the size and address limits, the model call and the validation
all live in ``shared.sealed_reader``. This module wires it to Hermes: it
resolves the session, builds the seat cost breaker once (the same breaker the
audit plugin meters interactive turns with), and turns every refusal into a
sentence for the model. It raises nothing to Hermes.

What the model is told about the turn afterwards matters as much as the items:
the result says the conversation is now web-derived, so a send to anyone but
the seat's admins will be refused, and the agent should draft instead.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from shared import sealed_reader
from shared.tool_registration import register_wrapped_tool

logger = logging.getLogger(__name__)

TOOL_NAME = sealed_reader.TOOL_NAME

DESCRIPTION = (
    "Research the public web or a public YouTube channel without reading raw "
    "pages yourself. Code searches and reads the pages; you get back up to 8 "
    "short summaries, each with its title, date and the exact source link. Use "
    "mode 'web' with a question, or mode 'youtube_channel' with a question and "
    "the channel_id (UC followed by 22 characters) to see that channel's recent "
    "videos. Never put a client's name, a case or matter number, or anything "
    "from the firm's records in the question: such a question is refused. After "
    "this tool runs, this conversation is web-derived: you may still read and "
    "draft, and you may email the seat's administrators, but nothing else is "
    "sent from it."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "What to find out, in plain words. No client details.",
            "minLength": 1,
            "maxLength": sealed_reader.MAX_QUESTION_CHARS,
        },
        "mode": {"type": "string", "enum": list(sealed_reader.MODES)},
        "channel_id": {
            "type": "string",
            "description": "YouTube channel id, only for mode youtube_channel.",
        },
        "max_items": {"type": "integer", "minimum": 1, "maximum": sealed_reader.MAX_ITEMS},
    },
    "required": ["question", "mode"],
    "additionalProperties": False,
}

#: The tools this plugin can register (``name -> (description, schema)``).
#: Registration itself is conditional; see :func:`register`.
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {TOOL_NAME: (DESCRIPTION, SCHEMA)}

_NOTE = (
    "These are summaries of public web material, checked by code: each link is "
    "one the search returned. Treat them as leads to verify, not facts. This "
    "conversation is now web-derived: emails to the seat's administrators are "
    "allowed; any other send, booking or change is refused, so draft instead."
)

_BREAKER: Any = None
_BREAKER_INIT = False
_BREAKER_LOCK = threading.Lock()


def _breaker() -> Any:
    """The Machine-wide cost breaker, built once. None when it cannot be built,
    which makes the reader refuse its model call (no unmetered nested spend)."""
    global _BREAKER, _BREAKER_INIT
    with _BREAKER_LOCK:
        if _BREAKER_INIT:
            return _BREAKER
        _BREAKER_INIT = True
        try:
            from shared.audit_client import audit_client_from_env
            from shared.cost_breaker import build_breaker
            from shared.customer_config import CustomerConfig

            slug = os.environ.get("SMD_CUSTOMER_SLUG") or os.environ.get("CUSTOMER_SLUG")
            if not slug:
                logger.error("hermes-smd-research: no customer slug; cost breaker NOT built")
                return None
            config = None
            try:
                config = CustomerConfig.from_volume()
            except Exception:  # noqa: BLE001 - defaults still protect
                logger.warning("hermes-smd-research: customer.yaml unreadable; breaker defaults")
            _BREAKER = build_breaker(
                customer=slug,
                persona="_machine",
                audit_client=audit_client_from_env(customer_slug=slug),
                config=config,
            )
        except Exception:  # noqa: BLE001
            logger.exception("hermes-smd-research: cost breaker construction failed")
            _BREAKER = None
        return _BREAKER


def _sessions(kwargs: dict[str, Any]) -> tuple[str, str]:
    """``(raw, resolved)`` session ids. The trust gate reads taint under the
    resolved id (``shared.provenance``, overlay #141); the raw id is what the
    inbound plugin keys under. Both are marked, since marking only tightens."""
    raw = str(kwargs.get("session_id") or "")
    try:
        from shared import provenance

        return raw, provenance.resolve_session(raw)
    except Exception:  # noqa: BLE001
        return raw, raw


def _marker(raw: str) -> Any:
    def _mark(session_id: str) -> None:
        from shared.inbound import SESSION_TAINT, TRUST_CLASS_WEB_DERIVED

        for sid in {raw, session_id}:
            SESSION_TAINT.mark(sid, TRUST_CLASS_WEB_DERIVED)

    return _mark


def _handler(args: dict[str, Any], **kwargs: Any) -> str:
    raw, session_id = _sessions(kwargs)
    try:
        result = sealed_reader.research(
            args, session_id=session_id, breaker=_breaker(), mark_session=_marker(raw)
        )
    except sealed_reader.ReaderError as exc:
        return f"{TOOL_NAME} refused: {exc}. Nothing was read into this conversation."
    except Exception as exc:  # noqa: BLE001 - a surprise is a refusal, never a raise
        # StickyStopError (the breaker at its cap) lands here too.
        logger.warning("hermes-smd-research: research failed (%s)", type(exc).__name__)
        return f"{TOOL_NAME} could not complete ({type(exc).__name__}). Nothing was read."
    return json.dumps(
        {"items": result.items, "dropped": result.dropped, "note": _NOTE},
        ensure_ascii=False,
    )


def register(ctx: Any) -> None:
    """Register ``web_research`` only where the trusted config authors it."""
    try:
        from shared.customer_config import CustomerConfig

        config = CustomerConfig.from_volume()
    except Exception:  # noqa: BLE001 - unreadable config means no tool
        logger.info("hermes-smd-research: customer.yaml unreadable; no tool")
        return
    if not sealed_reader.enabled_in(config):
        logger.info("hermes-smd-research: sealed_reader not authored; no tool")
        return
    register_wrapped_tool(
        ctx,
        name=TOOL_NAME,
        toolset="research",
        schema=SCHEMA,
        handler=_handler,
        description=DESCRIPTION,
        emoji="",
    )
    logger.info("hermes-smd-research registered %s", TOOL_NAME)
