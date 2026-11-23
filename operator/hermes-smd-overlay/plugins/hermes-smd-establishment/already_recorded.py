"""A yes to rules this seat has already recorded (2026-11-21).

THE LIVE FAILURE (the A&P seat, 2026-11-21, 9:36 to 9:42 AM). An attorney
stated rules; the Operator read them back in two threads; his yes in one thread
recorded them. His yes in the other thread then found nothing open, the
Operator told him no rules were outstanding and that nothing was recorded, and
it proposed the same sentences again as new rules, asking for a yes to rules
already in force.

Two pieces, both code, neither chosen by the model:

* :func:`note`: a yes whose reply binds to a read-back (``establish_readback_for``)
  whose rules are all already committed, or, with nothing of his open and no
  binding, a plain yes within an hour of his rules being committed, is answered
  with one sentence code renders: "Got it. That is already recorded: ...". It is
  registered as the reply's required words, so the reply cannot say otherwise;
* :func:`propose_answer`: the broker answers a proposal of a sentence the same
  person already committed with ``already_recorded`` and creates nothing; the
  model is told so, and never reads it back or asks for a yes.

It also holds the turn's one binding lookup (:func:`binding`), so deciding this
costs no second read of the reply's headers.

A FOLLOW-UP (2026-11-23, the A&P seat). The attorney confirmed a rule at 11:01;
at 11:03 he wrote "Please apply this to this file". That turn saw nothing of
his (the rule was committed in an earlier turn, and each email is its own
session), the reach gate refused the re-proposal because his new email named no
kind of document, and he was told nothing was applied and asked which documents
the rule was for. At 11:07 a reworded copy of the same rule was proposed and
committed as a second rule. One more piece, in code:

* :func:`recent`: the sender's own rules committed in the last hour, read from
  the broker once per turn, so ``establish_pending`` shows them as committed and
  a turn that answers no read-back is told they are recorded
  (:func:`follow_up_note`).

A gate that blocked a proposal like a recorded rule was built and removed the
same day: word overlap could not tell a new rule from a restated one, and it
could replace the yes-turn confirmation sentence (bug review, 2026-11-23).
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

from shared import rule_confirm, rule_confirm_reply, rules_by_hand

from . import readback_confirm, reply_reading

logger = logging.getLogger(__name__.rpartition(".")[0])

#: session -> (email answered, sender, the broker's answer): this turn's binding.
_TURN: dict[str, tuple[str, str, Any]] = {}
_MAX_TURNS = 256

_NOTE = (
    "The person's yes answers rules this seat has ALREADY recorded: nothing is waiting on "
    "their yes. Do not call establish_propose for them, do not read them back, do not ask "
    "them to confirm anything, and never say a rule was not recorded. Your reply must "
    "contain this sentence exactly, as its own paragraph; answer anything else the email "
    "asked in other paragraphs; never describe how the work is done:\n\n{say}"
)

#: The same, when the relay cannot hold this turn's reply to a fixed sentence.
_LOOSE_NOTE = (
    "The person's yes answers rules this seat has ALREADY recorded: {rules}. Nothing is "
    "waiting on their yes. Tell them plainly that these are already recorded. Do not call "
    "establish_propose for them and never say a rule was not recorded."
)

#: The tool result for a proposal of a sentence the person already committed.
PROPOSE_INSTRUCTION = (
    "This rule is ALREADY recorded: the person confirmed this exact sentence earlier. "
    "Nothing was proposed and nothing is waiting on their yes. Do not read it back and do "
    'not ask them to confirm it. If your reply mentions it, say: "That is already recorded."'
)


def _plugin() -> ModuleType:
    return sys.modules[__name__.rpartition(".")[0]]


def binding(session_id: str, sender: str) -> tuple[Any, Any]:
    """``readback_confirm.fetch_binding`` for this turn's email, read once per turn."""
    inbound = readback_confirm.inbound_message_id(session_id)
    hit = _TURN.get(session_id)
    if hit is not None and inbound and hit[0] == inbound and hit[1] == sender:
        return hit[2]
    got = readback_confirm.fetch_binding(_plugin()._broker_request, inbound, sender)
    _TURN[session_id] = (inbound, sender, got)
    while len(_TURN) > _MAX_TURNS:
        _TURN.pop(next(iter(_TURN)))
    return got


def _fold(text: Any) -> str:
    return " ".join(str(text or "").split())


def say_for(rows: list[dict[str, Any]]) -> str:
    """The one sentence for rules already recorded, from their stored words."""
    sentences = [s for s in (_fold(r.get("text")) for r in rows) if s]
    if len(sentences) == 1:
        return f"Got it. That is already recorded: {sentences[0]}"
    return "Got it. These are already recorded:\n\n" + "\n".join(f"- {s}" for s in sentences)


def _assents(user_message: Any, count: int | None, who: Any, plain_only: bool) -> bool:
    if rule_confirm_reply.pure_affirmative(user_message, count, who):
        return True
    if plain_only:
        return False
    reading = rule_confirm.read_own_text(rule_confirm_reply.own_text(user_message))
    return reading.affirmative and not reading.negated


def _already_rows(
    session_id: str, sender: str, user_message: Any, pending: list[dict[str, Any]], extra_open: int
) -> list[dict[str, Any]]:
    """The committed rules this yes answers, or ``[]`` when it answers something else."""
    own_open = {
        str(p.get("proposal_id")) for p in pending if rule_confirm_reply.is_own_rule(p, sender)
    }
    if pending and not own_open:
        return []  # only acts or others' rules are open: their own paths answer
    bound, extras = binding(session_id, sender)
    if extras is None:
        return []
    who = reply_reading.who(user_message, extras.get("block", ()), sender)
    if bound is not None:
        if own_open & set(bound.proposal_ids):
            return []  # the read-back still has open rules: the yes confirms those
        rows = list(extras.get("committed", ()))
        plain_only = bool(bound.listed)
    elif not pending and not extra_open:
        rows, plain_only = list(extras.get("recent", ())), True
    else:
        return []
    if not rows or not _assents(user_message, len(rows), who, plain_only):
        return []
    return rows


def note(
    session_id: str, sender: str, user_message: Any, pending: list[dict[str, Any]], extra_open: int
) -> str | None:
    """The note for a yes to rules already recorded, or ``None`` (the turn goes on)."""
    _TURN.pop(session_id, None)
    if rules_by_hand.on():
        return None
    rows = _already_rows(session_id, sender, user_message, pending, extra_open)
    if not rows:
        return None
    say = say_for(rows)
    logger.info(
        "hermes-smd-establishment: a yes from %s answered rules already recorded (%s)",
        sender,
        ",".join(str(r.get("proposal_id")) for r in rows),
    )
    if readback_confirm.can_register(session_id) and readback_confirm.register_say(session_id, say):
        _plugin()._CONFIRMED_SAY[session_id] = say
        return _NOTE.format(say=say)
    return _LOOSE_NOTE.format(rules="; ".join(f'"{_fold(r.get("text"))}"' for r in rows))


def first(
    session_id: str, sender: str, user_message: Any, pending: list[dict[str, Any]], extra_open: int
) -> tuple[bool, str | None]:
    """``(decided, note)`` before the reply is read for a verdict: a yes to rules
    already recorded (:func:`note`), or, with nothing open, the lapsed-rule
    reopening (``own_rules.reopen_note``, R8). ``decided`` False: read on."""
    done = note(session_id, sender, user_message, pending, extra_open)
    if done is not None:
        return True, done
    if not pending and not extra_open:
        return True, _plugin().own_rules.reopen_note(session_id, sender, user_message)
    return False, None


def propose_answer(response: dict[str, Any]) -> dict[str, Any] | None:
    """The tool result when the broker said the sentence is already recorded."""
    if not response.get("already_recorded"):
        return None
    return {"ok": True, "already_recorded": True, "instruction": PROPOSE_INSTRUCTION}


# ---- the follow-up of a rule confirmed in an earlier turn (2026-11-23) -------------

#: session -> (email answered, sender, the sender's rules committed recently).
_RECENT: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}

_FOLLOW_UP_NOTE = (
    "These rules of the person's own are ALREADY recorded (they confirmed them earlier): "
    "{rules}. Never say they were not recorded or that nothing has been applied, never ask "
    "which documents they cover, and do not call establish_propose to record them again. "
    'If your reply mentions them, use these words exactly: "{say}"'
)


def _subject(row: dict[str, Any]) -> dict[str, Any]:
    subject = row.get("subject")
    return subject if isinstance(subject, dict) else {}


def forget(session_id: str) -> None:
    """A new turn: nothing read for the last one carries into this one."""
    _RECENT.pop(session_id, None)


def recent(session_id: str | None, sender: str) -> list[dict[str, Any]]:
    """The sender's own rules committed in the last hour (``[]`` on any fault),
    read from the broker once per turn."""
    inbound = readback_confirm.inbound_message_id(session_id) if session_id else ""
    hit = _RECENT.get(session_id or "")
    if session_id and hit is not None and hit[0] == inbound and hit[1] == sender:
        return hit[2]
    rows: list[dict[str, Any]] = []
    if sender:
        try:
            answer = _plugin()._broker_request(
                {
                    "action": "establish_pending",
                    "sender": sender,
                    "include_for_admin": False,
                    "include_recent_committed": True,
                }
            )
        except Exception:  # noqa: BLE001 - nothing read: the turn goes on as before
            logger.debug("hermes-smd-establishment: recent-rules lookup failed", exc_info=True)
            answer = None
        got = answer.get("recent_committed") if isinstance(answer, dict) else None
        rows = [r for r in got or () if isinstance(r, dict) and r.get("text")]
    if session_id:
        _RECENT[session_id] = (inbound, sender, rows)
        while len(_RECENT) > _MAX_TURNS:
            _RECENT.pop(next(iter(_RECENT)))
    return rows


def recent_as_own(session_id: str, sender: str) -> list[dict[str, Any]]:
    """The sender's rules recorded in the last hour, each marked as theirs (the
    broker answers recent rules for the sender named), for ``pending_view``."""
    return [{**r, "instructed_by": sender} for r in recent(session_id, sender)] if sender else []


def follow_up_note(session_id: str, sender: str) -> str | None:
    """Where a message could answer no read-back, so the broker read-back
    lookup is skipped: a follow-up of a rule confirmed in an earlier turn
    ("apply this to this file") is still told that rule is recorded
    (2026-11-23). No job sentence here: that turn may be about another matter,
    and nothing registers this text. On a turn that answers no read-back: the sender's rules recorded in the
    last hour, said to the model as recorded, with the words to use."""
    if rules_by_hand.on():
        return None
    rows = recent(session_id, sender)
    if not rows:
        return None
    rules = "; ".join(f'"{_fold(r.get("text"))}"' for r in rows)
    return _FOLLOW_UP_NOTE.format(rules=rules, say=say_for(rows))


__all__ = [
    "PROPOSE_INSTRUCTION",
    "binding",
    "first",
    "follow_up_note",
    "forget",
    "note",
    "propose_answer",
    "recent",
    "recent_as_own",
    "say_for",
]
