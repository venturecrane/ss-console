"""Rules recorded by hand: a stopgap of 2026-11-20 for one seat, behind
``logic_in_code.rules_by_hand`` (``shared/logic_in_code.py``).

When the switch is ``enforce``, a firm person who states a drafting rule, or
replies about one, is told :data:`SAY` and nothing else about rules: no id, no
read-back, no "reply yes", no list, no lapse or outcome letter. SMD is told
durably (``shared.smd_owed``, kind ``rule_stated``) and records the rule by
hand. If the broker cannot record it, nothing is said about the rule.

Only an exact ``enforce`` turns it on. Absent, ``observe``, or a config that
cannot be read is the behavior before this existed. Removed when rule
confirmation by reply ships.

Never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from shared import inbound, logic_in_code, provenance, smd_owed

logger = logging.getLogger(__name__)

#: The one sentence, the reply's required block (``shared.turn_says``).
SAY = "Got it. I'll confirm in this thread once that's in place."

#: What the model is told when the broker did not record the rule.
NONE_INSTRUCTION = (
    "Say nothing about this rule in your reply: no id, no read-back, no request to confirm, "
    "no promise. Answer anything else the email asked."
)

#: The turn's instruction when the email answered a rule read-back or carried a
#: rule tag, and the broker recorded it.
REPLY_NOTE = (
    "This email is about a drafting rule. Nothing was confirmed or changed. Your reply must "
    f'contain this sentence exactly, as its own paragraph: "{SAY}" Say nothing else about '
    "rules: no id, no tag, no read-back, no list, no request to confirm. Answer anything else "
    "the email asked."
)

#: The turn's instruction when that record could not be written.
REPLY_NONE_NOTE = (
    "This email is about a drafting rule. Nothing was confirmed or changed. " + NONE_INSTRUCTION
)

#: The standing note on every attributed turn while the switch is on, in place
#: of the read-back instructions.
NUDGE = (
    "When someone tells you how work should read from now on, call establish_propose with the "
    "rule in their words. It returns the one sentence your reply carries about it. Say nothing "
    "else about the rule: no id, no tag, no read-back, no request to confirm, no list of rules."
)

#: What establish_pending and a rule's establish_submit return while it is on.
NOTHING_NOTE = (
    "Nothing to list or commit: rules are recorded by hand on this seat. Say nothing about "
    "rules beyond the one sentence establish_propose returns."
)


def on() -> bool:
    """True only when the seat authors ``rules_by_hand: enforce``."""
    try:
        sw = logic_in_code.load_switch(logic_in_code.RULES_BY_HAND)
    except Exception:  # noqa: BLE001 - unreadable is off, today's behavior
        return False
    return sw.error is None and sw.mode == logic_in_code.ENFORCE


def _origin(session_id: Any) -> Any | None:
    try:
        resolved = provenance.resolve_session(session_id if isinstance(session_id, str) else None)
        return inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(resolved) if resolved else None
    except Exception:  # noqa: BLE001 - no origin records nothing
        return None


def record(session_id: Any, what: str, send: Any = None) -> bool:
    """Record the owed item against this turn's email. True only when written."""
    origin = _origin(session_id)
    if origin is None:
        return False
    ref = getattr(origin, "internet_message_id", "") or getattr(origin, "message_id", "")
    sender = str(getattr(origin, "sender_address", "") or "")
    detail = f"{what}; record it by hand; from {sender or '(sender unknown)'}"
    return smd_owed.record(smd_owed.KIND_RULE, ref, detail, send=send)


def propose_answer(session_id: Any, send: Any = None) -> dict[str, str]:
    """``establish_propose``'s whole result while the switch is on."""
    if record(session_id, "rule stated", send=send):
        return {"say": SAY}
    return {"say": "none", "instruction": NONE_INSTRUCTION}


def reply_note(session_id: Any, send: Any = None) -> str:
    """The pre-turn instruction for an email about a rule."""
    return REPLY_NOTE if record(session_id, "reply about a rule", send=send) else REPLY_NONE_NOTE


__all__ = ["NUDGE", "NOTHING_NOTE", "SAY", "on", "propose_answer", "record", "reply_note"]
