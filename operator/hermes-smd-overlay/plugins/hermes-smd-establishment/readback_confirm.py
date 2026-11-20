"""A person's own rule, read back with no id and confirmed by the email replied to.

2026-11-19, the real day. An attorney answered "[rule A] yes and [rule B] yes"
and nothing was recorded; the Operator then quoted rule ids at him four times in
six minutes, and after he confirmed told him his rule "may not appear in the
result". Four pieces live here, all code, none chosen by the model:

* :func:`own_readback` renders what the person who stated a rule is shown: the
  sentence as the broker stored it, the document kinds and whose documents it
  covers (the reach read-back, behind the seat's switch), and "Reply yes to
  confirm." No tag, no id;
* :func:`owe` records with the broker, the moment a read-back is rendered, which
  proposals it carries for the email being answered; the relay's send binds it
  to the sent message's id in the same step (``shared/readback_send.py``), so a
  restart loses nothing. The broker refuses anything that is not the
  recipient's own open rule;
* :func:`pick_list` renders the numbered list a reply that binds to nothing gets
  ("Reply 1, 2 or all"), and that list is bound the same way, so "1 and 3" on the
  next reply resolves against the list that message showed;
* :func:`confirmed_say` renders the one reply to a confirmation, from the
  committed rows and whether a drafting lane reads them on this seat
  (:func:`shared.wording_rules.lane_reads`): "Got it. From the next memos: ..."
  or "Got it. That is recorded." Never "may not appear", never an explanation of
  how installing works.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared import (
    outbound_gate,
    rule_confirm_reply,
    rule_reach,
    turn_says,
    wording_rules,
)
from shared.inbound import SESSION_INBOUND_ORIGIN
from shared.wording_rules_store import kinds_said

logger = logging.getLogger(__name__)

#: The broker verbs (``workspace_broker/readback_bindings.py``).
VERB_OWED = "establish_readback_owed"
VERB_FOR = "establish_readback_for"
VERB_OBSERVE = "establish_block_observe"
VERB_HANDOFF = "establish_readback_handoff"

#: The last line of a single rule's read-back.
REPLY_YES = "Reply yes to confirm."

BrokerFn = Callable[[dict[str, Any]], dict[str, Any]]


def _fold(text: str) -> str:
    return " ".join(str(text or "").split())


def _subject_of(row: dict[str, Any]) -> dict[str, Any]:
    subject = row.get("subject")
    return subject if isinstance(subject, dict) else {}


def rule_sentence(row: dict[str, Any], reach_on: bool) -> str:
    """The rule as the person is shown it: the stored sentence, then its reach."""
    text = _fold(row.get("text") or "")
    if not text:
        return ""
    subject = _subject_of(row)
    return rule_reach.readback_with_reach(text, subject, reach_on)


def own_readback(sentence: str) -> str:
    """One rule's read-back to the person who stated it: no tag, no id."""
    return f"{sentence}\n\n{REPLY_YES}" if sentence else ""


_COUNT_WORDS = {
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def ask_line(count: int) -> str:
    """How a read-back of ``count`` rules asks for the answer: once, at the end."""
    if count <= 1:
        return REPLY_YES
    if count == 2:
        return "Reply yes to confirm both."
    return f"Reply yes to confirm all {_COUNT_WORDS.get(count, str(count))}."


def own_readbacks(sentences: list[str]) -> str:
    """The turn's read-back of every rule the person stated in it: each sentence,
    then one line asking for the answer, with the count rendered by code."""
    kept = [s for s in sentences if s]
    if not kept:
        return ""
    return "\n\n".join(kept) + "\n\n" + ask_line(len(kept))


def pick_list(sentences: list[str]) -> str:
    """The numbered list a reply that binds to no read-back gets.

    One rule is read back as a single read-back (a plain yes then binds to it);
    two or more are numbered, with the answers they take.
    """
    if len(sentences) == 1:
        return own_readback(sentences[0])
    lines = [f"{n}. {s}" for n, s in enumerate(sentences, start=1)]
    return "These rules are waiting for your yes:\n\n" + "\n".join(lines) + f"\n\n{PICK_END}"


#: The answer the numbered list asks for. Every rule on it is one the person
#: stated, so a plain yes to it confirms them all; numbers pick some.
PICK_END = "Reply all to confirm every one, or the numbers of the ones you want."


#: The relay's register slot this plugin writes: one sentence per turn, rewritten
#: as the turn's read-back grows, replaced in place rather than added again.
SAY_SLOT = "establishment"


def register_say(session_id: str, say: str) -> bool:
    """Hand the reply relay this turn's fixed sentence (``shared.turn_says``), so a
    body with any other words is held with the sentence quoted back. Keyed on
    the email this turn's own trusted prompt bound, as the relay keys it."""
    if not session_id or not say:
        return False
    try:
        origin = SESSION_INBOUND_ORIGIN.bound_this_turn(session_id)
    except Exception:  # noqa: BLE001 - no bound email: nothing the relay could key on
        return False
    message_id = str(getattr(origin, "message_id", "") or "") if origin is not None else ""
    return turn_says.note(session_id, message_id, say, slot=SAY_SLOT) if message_id else False


def can_register(session_id: str) -> bool:
    """Will the relay hold this turn's reply to a fixed sentence? Only then may
    a read-back be owed or a confirmation be committed: the person must see the
    exact words code rendered, never a paraphrase (review 2026-11-20). Needs
    the email this turn's own prompt bound, and the reply plugin loaded."""
    try:
        origin = SESSION_INBOUND_ORIGIN.bound_this_turn(session_id) if session_id else None
    except Exception:  # noqa: BLE001 - no bound email: nothing the relay could key on
        return False
    return origin is not None and bool(getattr(origin, "message_id", "")) and turn_says.published()


def confirmed_say(rows: list[dict[str, Any]], reach_on: bool | None = None) -> str:
    """The reply to a person who just confirmed their own rules, from the
    committed rows alone (critic issue 8: never the installed file, which the
    applier has not polled yet)."""
    lines: list[str] = []
    for row in rows:
        subject = _subject_of(row)
        sentence = _fold(row.get("text") or "")
        kinds = wording_rules.lane_reads(
            subject.get("output_class"), subject.get("doc_kinds"), reach_on
        )
        lines.append(f"From the next {kinds_said(list(kinds))}: {sentence}" if kinds else sentence)
    if len(rows) == 1:
        subject = _subject_of(rows[0])
        if wording_rules.lane_reads(
            subject.get("output_class"), subject.get("doc_kinds"), reach_on
        ):
            return f"Got it. {lines[0]}"
        return "Got it. That is recorded."
    return "Got it. These are recorded:\n\n" + "\n".join(f"- {line}" for line in lines)


def owe(
    broker: BrokerFn,
    inbound: str,
    recipient: str,
    ids: list[str],
    listed: bool,
    verified: list[str],
) -> bool:
    """Record with the broker the read-back this turn owes ``recipient`` for the
    email ``inbound``: durable from now, bound to the sent message by the relay's
    send. Logged at WARNING when it cannot be."""
    if not inbound or not recipient or not ids:
        logger.warning(
            "readback_confirm: no email or person to owe a read-back of %s to", ",".join(ids)
        )
        return False
    try:
        answer = broker(
            {
                "action": VERB_OWED,
                "inbound_message_id": inbound,
                "recipient": recipient,
                "proposal_ids": list(ids),
                "listed": listed,
                "verified_ids": list(verified),
            }
        )
    except Exception:  # noqa: BLE001 - the person is asked with a list instead
        logger.warning(
            "readback_confirm: read-back of %s not recorded", ",".join(ids), exc_info=True
        )
        return False
    if not (isinstance(answer, dict) and answer.get("ok")):
        logger.warning("readback_confirm: broker refused the read-back of %s", ",".join(ids))
        return False
    return True


def watch_install(broker: BrokerFn, proposal_id: str, run_id: str) -> None:
    """Ask the broker to watch this own rule's run until it installs."""
    if not proposal_id or not run_id:
        return
    try:
        broker({"action": "establish_install_watch", "proposal_id": proposal_id, "run_id": run_id})
    except Exception:  # noqa: BLE001 - logged; the commit stands
        logger.warning(
            "readback_confirm: install of %s not watched (run %s)",
            proposal_id,
            run_id,
            exc_info=True,
        )


def check_installs(broker: BrokerFn) -> list[str]:
    """The sweeper's pass: the broker reads each due run; a failure is logged at
    ERROR (what reaches SMD's monitoring), ids only. Returns the ids paged."""
    answer = broker({"action": "establish_install_check"})
    failed = answer.get("failed") if isinstance(answer, dict) else None
    paged: list[str] = []
    for item in failed if isinstance(failed, list) else []:
        if not isinstance(item, dict):
            continue
        logger.error(
            "own rule %s was confirmed and committed (run %s), but %s; the person "
            "was told it is recorded",
            item.get("proposal_id"),
            item.get("run_id"),
            item.get("why"),
        )
        paged.append(str(item.get("proposal_id") or ""))
    return paged


def fetch_binding(
    broker: BrokerFn, inbound_message_id: str, sender: str
) -> tuple[rule_confirm_reply.ReadbackBinding | None, dict[str, Any] | None]:
    """``(binding, extras)`` as the broker matched them from the reply's own
    headers. There is no fallback to a list sent earlier: no header naming a
    recorded read-back is no binding, and the person is listed again. ``extras``
    is ``verified`` (ids), ``block`` (his learned signature lines), ``rounds``
    (read-backs per open rule) and ``handed_off``; ``None`` when the broker
    could not be read at all (then nothing of his own rules moves this turn)."""
    if not inbound_message_id or not sender:
        return None, None
    try:
        answer = broker(
            {"action": VERB_FOR, "inbound_message_id": inbound_message_id, "sender": sender}
        )
    except Exception:  # noqa: BLE001 - not bound: the seat asks with a list
        logger.warning(
            "readback_confirm: binding lookup failed for %s", inbound_message_id, exc_info=True
        )
        return None, None
    if not isinstance(answer, dict) or not answer.get("ok"):
        return None, None

    def _ids(key: str) -> tuple[str, ...]:
        return tuple(str(p) for p in answer.get(key) or () if isinstance(p, str))

    ids = _ids("proposal_ids") if answer.get("bound") else ()
    binding = (
        rule_confirm_reply.ReadbackBinding(proposal_ids=ids, listed=bool(answer.get("listed")))
        if ids
        else None
    )
    rounds = answer.get("rounds") if isinstance(answer.get("rounds"), dict) else {}
    extras = {
        "verified": _ids("verified_ids"),
        "block": tuple(str(s) for s in answer.get("block") or () if isinstance(s, str)),
        "rounds": {str(k): int(v) for k, v in rounds.items() if isinstance(v, int)},
        "handed_off": _ids("handed_off"),
    }
    return binding, extras


def note_firm(sentences: list[str], session_id: str) -> None:
    """The broker's own sentences, exempt from the citation scan as written."""
    outbound_gate.note_firm_sentences(sentences, session_id=session_id)


def for_model(row: dict[str, Any], sender: str) -> dict[str, Any]:
    """A row as the model may see it: an own rule carries no id (R2.2).

    The model never needs one for the person's own rule (a confirmation is
    committed by code), and an id it can see is an id it can quote. Kept only
    on rows answered by tag: an act, an operations request, and a rule waiting
    on an administrator."""
    if not rule_confirm_reply.is_own_rule(row, sender):
        return row
    return {k: v for k, v in row.items() if k not in ("proposal_id", "duplicate_of")}


__all__ = [
    "PICK_END",
    "REPLY_YES",
    "VERB_FOR",
    "VERB_OWED",
    "ask_line",
    "check_installs",
    "confirmed_say",
    "fetch_binding",
    "for_model",
    "note_firm",
    "owe",
    "own_readback",
    "own_readbacks",
    "pick_list",
    "register_say",
    "rule_sentence",
    "watch_install",
]
