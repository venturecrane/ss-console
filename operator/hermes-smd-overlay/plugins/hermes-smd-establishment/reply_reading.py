"""What the seat feeds the plain-yes grammar, and the read-back cap (2026-11-20).

Split out of ``own_rules`` under the module-size ratchet. Three things, none of
them read from the body of the reply being judged:

* :func:`who`: the sender's name from the firm's authored roster
  (``users[].full_name``, matched by exact address; on the Graph lane the
  ``from:`` line is the bare address), the Operator's persona names, and his
  signature block as SMD's root seed stored it from his own past emails
  (``workspace_broker/sender_block``, returned by ``establish_readback_for``).
  Nothing is learned from a reply at runtime, so no email can teach the seat
  what a plain yes looks like;
* :func:`turn_readback` and :func:`owe_and_say`: the turn's read-back is owed
  FIRST and registered as the reply's required words only once the broker owed
  it;
* :func:`over_cap` and :func:`hand_off`: a rule read back twice
  (``workspace_broker/readback_rounds``) is not read back a third time; it is
  handed to SMD, and the person is told the stopgap sentence.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

from shared import reply_shape, rules_by_hand

from . import readback_confirm

logger = logging.getLogger(__name__.rpartition(".")[0])

#: session -> read-backs each of the sender's open own rules has had (this turn's answer).
ROUNDS: dict[str, dict[str, int]] = {}

#: The stopgap sentence's own slot among the reply's required paragraphs, so
#: it stands beside a read-back of another rule on the same turn.
HANDOFF_SLOT = "establishment-handoff"

#: The note when a rule has had its two read-backs and is handed to SMD.
HANDED_OFF_NOTE = (
    "This email answered a rule that has already been read back twice without a plain yes. "
    "It is handed to SMD, who will confirm it with the person in this thread. Your reply must "
    f'contain this sentence exactly, as its own paragraph: "{rules_by_hand.SAY}" Say nothing '
    "else about that rule: no read-back of it, no list, no request to confirm it. Answer "
    "anything else the email asked."
)


def _plugin() -> ModuleType:
    return sys.modules[__name__.rpartition(".")[0]]


def who(user_message: Any, block: Any = (), sender: str = "") -> reply_shape.People:
    """Whose names this reply may carry, and his learned block. Never the body."""
    addressed: list[str] = []
    full_name = ""
    try:
        cfg = _plugin().CustomerConfig.from_volume()
        personas = getattr(cfg, "personas", None) or []
        addressed = [str(p.get("name") or "") for p in personas if isinstance(p, dict)]
        full_name = (cfg.authored_person_name(sender) or "") if sender else ""
    except Exception:  # noqa: BLE001 - no config: the envelope's name only
        logger.debug("hermes-smd-establishment: no roster name for the reply reading")
    return reply_shape.people(user_message, addressed=addressed, block=block, full_name=full_name)


def turn_readback(
    session_id: str, sender: str, proposal_id: str, sentence: str
) -> tuple[str, bool]:
    """``(text, handed)``: the turn's whole read-back once this rule joins it,
    owed and registered; or, when the broker refuses this rule's read-back
    (read back twice already, or handed to SMD), ``(stopgap sentence, True)``:
    it is handed to SMD and never read back again; or, when the broker refused
    the owe for any other reason, ``("", True)``: nothing is owed, so nothing
    about this rule is said (a read-back asking for a yes that no send would
    bind is never sent).

    It replaces the block an earlier proposal on this turn owed: the reply is
    one read-back of every rule, word for word (the relay holds any other
    words, ``shared.turn_says``). Each proposal is bound on its own to the sent
    message, which carries its sentence and the line asking for the answer."""
    plug = _plugin()
    turn = plug._OWN_TURN.setdefault(session_id, {})
    owed = plug._READBACK_OWED.get(session_id) or []
    before = readback_confirm.own_readbacks(list(turn.values()))
    if before in owed:
        owed.remove(before)
    turn[proposal_id] = sentence
    whole = readback_confirm.own_readbacks(list(turn.values()))
    capped = owe_and_say(session_id, sender, list(turn), False, whole)
    if capped is None:  # refused, not at the cap: the turn's earlier read-back stands
        turn.pop(proposal_id, None)
        if before:
            plug._remember_readback(session_id, before)
        return "", True
    if not capped:
        return whole, False
    for gone in capped:
        turn.pop(gone, None)
    told = handed_to_smd(
        session_id, sender, capped, readback_confirm.inbound_message_id(session_id)
    )
    whole = readback_confirm.own_readbacks(list(turn.values())) if turn else ""
    if whole:  # the turn's other rules are still read back, without it
        plug._remember_readback(session_id, whole)
        owe_and_say(session_id, sender, list(turn), False, whole)
    if proposal_id in capped:
        return (rules_by_hand.SAY if told else ""), True
    return whole, False


def owe_and_say(
    session_id: str, sender: str, ids: list[str], listed: bool, say: str
) -> tuple[str, ...] | None:
    """Owe the read-back ``say`` FIRST, and register it as the reply's required
    words only once the broker owed it: a read-back the broker refused never
    goes out asking for a yes (review 2026-11-20). Nothing is owed unless the
    relay can hold the reply to these exact words. Returns ``()`` when owed (or
    when the relay cannot hold this turn, as before), the rules the broker
    refused at the read-back cap, or ``None`` when it refused for any other
    reason (a broker error, a lost socket)."""
    if not readback_confirm.can_register(session_id):
        return ()
    owed, capped = readback_confirm.owe(
        _plugin()._broker_request,
        readback_confirm.inbound_message_id(session_id),
        sender,
        ids,
        listed,
        sorted(_plugin()._VERIFIED.get(session_id) or ()),
    )
    if owed:
        readback_confirm.register_say(session_id, say)
        return ()
    return capped or None


def joined(*notes: str | None) -> str | None:
    """The turn's notes in order, one paragraph each; ``None`` when there are none."""
    kept = [n for n in notes if n]
    return "\n\n".join(dict.fromkeys(kept)) if kept else None


def over_cap(rounds: dict[str, int], ids: tuple[str, ...]) -> bool:
    """Has any of these rules had its two read-backs already?"""
    from shared.rule_confirm_reply import MAX_READBACKS

    return any(int(rounds.get(i, 0)) >= MAX_READBACKS for i in ids)


def handed_to_smd(session_id: str, sender: str, ids: tuple[str, ...], request_ref: str) -> bool:
    """Hand ``ids`` to SMD (``establish_readback_handoff``) and register the
    stopgap sentence as the reply's required paragraph. True only when both
    held: the person is promised a follow-up only when SMD was told."""
    try:
        answer = _plugin()._broker_request(
            {
                "action": readback_confirm.VERB_HANDOFF,
                "sender": sender,
                "proposal_ids": list(ids),
                "request_ref": request_ref,
            }
        )
    except Exception:  # noqa: BLE001 - not recorded: nothing is said about it
        logger.warning(
            "hermes-smd-establishment: hand-off of %s failed", ",".join(ids), exc_info=True
        )
        answer = None
    if (
        isinstance(answer, dict)
        and answer.get("smd_told")
        and readback_confirm.register_say(session_id, rules_by_hand.SAY, slot=HANDOFF_SLOT)
    ):
        logger.info(
            "hermes-smd-establishment: rules %s handed to SMD after two read-backs", ",".join(ids)
        )
        return True
    logger.error(
        "hermes-smd-establishment: rules %s reached the read-back cap, not recorded", ",".join(ids)
    )
    return False


def hand_off(session_id: str, sender: str, ids: tuple[str, ...], request_ref: str) -> str:
    """The turn's note once ``ids`` are handed to SMD: the stopgap sentence, or,
    when the broker did not record it, say nothing about the rule."""
    if handed_to_smd(session_id, sender, ids, request_ref):
        return HANDED_OFF_NOTE
    return rules_by_hand.REPLY_NONE_NOTE


__all__ = [
    "HANDED_OFF_NOTE",
    "HANDOFF_SLOT",
    "hand_off",
    "handed_to_smd",
    "joined",
    "over_cap",
    "owe_and_say",
    "turn_readback",
    "who",
]
