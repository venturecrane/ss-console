"""A rule-confirmation turn the way the seat runs one (2026-11-20).

Since the review of 2026-11-20 a person's own rule is owed, bound and committed
only on a turn whose reply the relay holds to the exact sentence code renders:
the email this turn's own prompt bound (``SESSION_INBOUND_ORIGIN.bound_this_turn``)
and the reply plugin's register published (``shared.turn_says``). These helpers
set up exactly that, through the real objects, so a test drives the path the
seat drives. Names and ids are invented.
"""

from __future__ import annotations

from typing import Any

from shared import turn_says
from shared.inbound import SESSION_INBOUND_ORIGIN, InboundOrigin


def bound_turn(session: str, sender: str, message_id: str, inbox: str = "op@x") -> None:
    """The email ``message_id`` from ``sender`` opened THIS turn of ``session``
    (a fresh session each call: the session's earlier turns are forgotten)."""
    SESSION_INBOUND_ORIGIN._origins.pop(session, None)
    SESSION_INBOUND_ORIGIN._prompt_ids_seen.pop(session, None)
    SESSION_INBOUND_ORIGIN.record(
        session, InboundOrigin(sender_address=sender, message_id=message_id, inbox_id=inbox)
    )
    SESSION_INBOUND_ORIGIN.note_turn_prompt(session, message_id)


def relay_register(monkeypatch: Any) -> turn_says.TurnSays:
    """The reply plugin's register, published as the plugin publishes it."""
    register = turn_says.TurnSays()
    monkeypatch.setattr(turn_says, "_NOTE", turn_says.writer_for(register))
    return register
