"""What the seat feeds the plain-yes grammar, and the read-back cap (2026-11-20).

Split out of ``own_rules`` under the module-size ratchet. Three things, none of
them read from the body of the reply being judged:

* :func:`who`: the sender's name from the firm's authored roster
  (``users[].full_name``, matched by exact address; on the Graph lane the
  ``from:`` line is the bare address), the Operator's persona names, and his
  learned signature block (``workspace_broker/sender_block``, as the broker's
  ``establish_readback_for`` returned it);
* :func:`observe`: hand the broker the last lines of an email of his own, so his
  block is learned and kept current. Called only after this reply was judged
  (or on an email no rule decision rides on), so a reply never vouches for
  itself;
* :func:`over_cap` and :func:`hand_off`: a rule read back twice
  (``workspace_broker/readback_rounds``) is not read back a third time; it is
  handed to SMD, and the person is told the stopgap sentence.
"""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from types import ModuleType
from typing import Any

from shared import reply_shape, rules_by_hand

from . import readback_confirm

logger = logging.getLogger(__name__.rpartition(".")[0])

#: The emails already handed to the broker this process (it keeps them; this
#: only spares the round trip).
_OBSERVED: OrderedDict[str, None] = OrderedDict()
#: session -> read-backs each of the sender's open own rules has had (this turn's answer).
ROUNDS: dict[str, dict[str, int]] = {}
_MAX_OBSERVED = 2048
#: The most lines of an email's end the broker is sent (its own cap).
_TAIL = 24

#: The note when a rule has had its two read-backs and is handed to SMD.
HANDED_OFF_NOTE = (
    "This email answered a rule that has already been read back twice without a plain yes. "
    "It is handed to SMD, who will confirm it with the person in this thread. Your reply must "
    f'contain this sentence exactly, as its own paragraph: "{rules_by_hand.SAY}" Say nothing '
    "else about the rule: no read-back, no list, no request to confirm. Answer anything else "
    "the email asked."
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


def observe(sender: str, message_id: str, user_message: Any) -> None:
    """Give the broker the end of this email of his (``establish_block_observe``).
    Never raises; a missed one costs nothing but a slower learning."""
    if not sender or not message_id or message_id in _OBSERVED:
        return
    lines = reply_shape.own_normal(user_message)[-_TAIL:]
    if not lines:
        return
    try:
        _plugin()._broker_request(
            {
                "action": readback_confirm.VERB_OBSERVE,
                "sender": sender,
                "message_id": message_id,
                "lines": lines,
            }
        )
    except Exception:  # noqa: BLE001 - the block is learned from the next one
        logger.debug("hermes-smd-establishment: block observe failed", exc_info=True)
        return
    _OBSERVED[message_id] = None
    while len(_OBSERVED) > _MAX_OBSERVED:
        _OBSERVED.popitem(last=False)


def over_cap(rounds: dict[str, int], ids: tuple[str, ...]) -> bool:
    """Has any of these rules had its two read-backs already?"""
    from shared.rule_confirm_reply import MAX_READBACKS

    return any(int(rounds.get(i, 0)) >= MAX_READBACKS for i in ids)


def hand_off(session_id: str, sender: str, ids: tuple[str, ...], request_ref: str) -> str:
    """Hand ``ids`` to SMD (``establish_readback_handoff``) and return the turn's
    note: the stopgap sentence, registered as the reply's required paragraph,
    or, when the broker did not record it, say nothing about the rule."""
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
        and readback_confirm.register_say(session_id, rules_by_hand.SAY)
    ):
        logger.info(
            "hermes-smd-establishment: rules %s handed to SMD after two read-backs", ",".join(ids)
        )
        return HANDED_OFF_NOTE
    logger.error(
        "hermes-smd-establishment: rules %s reached the read-back cap, not recorded", ",".join(ids)
    )
    return rules_by_hand.REPLY_NONE_NOTE


__all__ = ["HANDED_OFF_NOTE", "hand_off", "observe", "over_cap", "who"]
