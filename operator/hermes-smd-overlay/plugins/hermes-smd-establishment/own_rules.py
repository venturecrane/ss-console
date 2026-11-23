"""The person's own rules: the turn's read-back, the numbered list, and the commit.

Split out of the establishment plugin's ``__init__`` (2026-11-19, the module-size
ratchet): the confirm-by-reply half of R2.1 to R2.3. Every function reads the
plugin's own state through :func:`_plugin` at call time, so the registers it
writes are the plugin's and a test that patches the plugin patches these.
"""

from __future__ import annotations

import json
import logging
import sys
from types import ModuleType
from typing import Any

from shared import (
    act_broker,
    reply_shape,
    rule_confirm,
    rule_confirm_reply,
    rules_by_hand,
    smd_owed,
    wording_rules,
)
from shared.inbound import SESSION_INBOUND_ORIGIN

from . import already_recorded, readback_confirm, reply_reading
from .turn_handles import (  # noqa: F401 - the plugin calls them here
    real_id,
    turn_handle,
    with_real_id,
)

logger = logging.getLogger(__name__.rpartition(".")[0])

#: The two tools these notes name (the plugin's own constants, which this module
#: cannot import while the plugin is still loading).
TOOL_SUBMIT = "establish_submit"
TOOL_STATUS = "establish_status"


#: The propose tool's ``restates`` argument (R9): the older open rule of the
#: same person this one replaces, closed silently by code.
RESTATES_SCHEMA = {
    "type": ["string", "null"],
    "description": (
        "Only when this rule REPLACES one the same person stated earlier and "
        "has not confirmed: that rule's number in this turn's read-back "
        '("1"), or its sentence exactly as read back. The older one is '
        "then withdrawn. Leave it out otherwise."
    ),
}


def _plugin() -> ModuleType:
    """The establishment plugin module this copy serves, read at call time."""
    return sys.modules[__name__.rpartition(".")[0]]


#: The person confirmed their own rules and the seat committed them (2026-11-19,
#: R2.3). The reply is the seat's sentence: it is written from the committed
#: rows and whether a drafting lane on this seat reads them, and nothing the
#: model adds about installing is true or needed.
#: How every fixed sentence this module hands the model is to be used (the
#: relay holds a reply that breaks it, ``shared.turn_says``).
BLOCK_RULE = "your reply must contain this sentence exactly, as its own paragraph; answer anything else the email asked in other paragraphs; never describe how the work is done"

#: The plain sentence for a confirmed rule the seat could not record, in the
#: same reply as the ones it did: the person must know that rule is not in force.
#: The Operator's own voice, naming no one (owner's ruling, 2026-11-19). It is
#: said only once the broker recorded the rule as work SMD owes
#: (``shared.smd_owed``); a rule whose record also failed is left out of the
#: reply (its failure is logged), and the rules that did commit are still
#: confirmed (review 2026-11-19).
NOT_RECORDED = 'One more, "{sentence}", is not recorded yet. I\'m looking into it and will reply in this thread.'

_OWN_CONFIRMED_NOTE = (
    "The person just confirmed, and this seat has already recorded it: there is "
    f"nothing to commit and no {TOOL_SUBMIT} or {TOOL_STATUS} to call. "
    f"{BLOCK_RULE[0].upper()}{BLOCK_RULE[1:]}:\n\n{{say}}"
)


#: Nothing this turn confirmed could be committed or recorded as owed.
_NOTHING_TO_SAY_NOTE = (
    "The person confirmed a rule, and the seat could not record it. Say nothing about the rule "
    "in your reply, and answer anything else the email asked."
)

#: A confirmed own rule the seat could not commit itself, because a gate stood
#: in the way: the path every confirmation took before. The handle is for the
#: tool argument only.
_OWN_FALLBACK_NOTE = (
    'The person confirmed their rule "{text}", and the seat could not record it '
    f"itself. Commit it with {TOOL_SUBMIT}, passing exactly: scope '{{scope}}', "
    "proposal_id '{proposal_id}', instructed_by, source_ref (and append true on a "
    "personal rule). That proposal_id is a handle for the tool only: never write "
    "it, or any id, in a reply. The seat said: {gate}"
)


#: A reply that agreed with something but binds to no read-back the seat sent,
#: while the person has rules of their own open. The list is code's.
_PICK_NOTE = (
    "The person answered in the affirmative, but their reply does not say which "
    "of their rules it is about, so nothing was recorded. "
    f"{BLOCK_RULE[0].upper()}{BLOCK_RULE[1:]}:\n\n{{block}}"
)


#: A reply bound to a read-back (or naming the person's own rules) that was not a
#: plain yes: "Yes / Thanks, / Actually make that 12pt", "Yes / and draft the
#: Jones demand". Nothing is recorded; the rule is read back again, by code.
_RESTATE_NOTE = (
    "The person's reply was not a plain yes to their rule, so nothing was recorded. "
    f"{BLOCK_RULE[0].upper()}{BLOCK_RULE[1:]}:\n\n{{block}}\n\n"
    "If their email changes the rule, call establish_propose with the rule as they now "
    'state it and `restates` set to its number in that block ("1"); the block is then '
    "rewritten for you. Answer anything else the email asked."
)


def own_ask_note(
    session_id: str,
    sender: str,
    is_admin: bool,
    pending: list[dict[str, Any]],
    verdict: rule_confirm.Verdict,
) -> str | None:
    """The note for an ASK about the person's own rules, rendered by code: the
    numbered list (``ASK_PICK``), or the read-back again (``ASK_QUALIFIED``).
    ``None`` for every other verdict, which keeps the plugin's own templates."""
    if verdict.kind != rule_confirm.ASK or verdict.reason not in (
        rule_confirm.ASK_PICK,
        rule_confirm.ASK_QUALIFIED,
    ):
        return None
    by_id = {str(p.get("proposal_id")): p for p in pending}
    own = tuple(
        c for c in verdict.candidates if rule_confirm_reply.is_own_rule(by_id.get(c, {}), sender)
    )
    rounds = reply_reading.ROUNDS.get(session_id, {})
    capped = tuple(c for c in own if reply_reading.over_cap(rounds, (c,)))
    # Two read-backs and no plain yes: no third. SMD confirms THOSE by hand; the
    # rest of the reply's rules are listed or read back as before.
    handed = (
        reply_reading.hand_off(
            session_id, sender, capped, readback_confirm.inbound_message_id(session_id)
        )
        if capped
        else ""
    )
    rest = tuple(c for c in verdict.candidates if c not in capped)
    note = None
    if verdict.reason == rule_confirm.ASK_PICK and rest:
        note = _pick_note(session_id, sender, is_admin, pending, rest)
    elif verdict.reason == rule_confirm.ASK_QUALIFIED and rest:
        note = restate_note(session_id, sender, pending, rest)
    return reply_reading.joined(handed, note)


def restate_note(
    session_id: str, sender: str, pending: list[dict[str, Any]], candidates: tuple[str, ...]
) -> str | None:
    """Read the person's own rules in ``candidates`` back again, rendered, owed
    and registered by code exactly as at propose time. ``None`` when none of
    them is the sender's own rule (the caller's template then applies)."""
    by_id = {str(p.get("proposal_id")): p for p in pending}
    rows = [
        by_id[c]
        for c in candidates
        if c in by_id and rule_confirm_reply.is_own_rule(by_id[c], sender)
    ]
    reach_on = wording_rules.switched_on()
    block, handed_note = "", None
    for row in rows:
        sentence = readback_confirm.rule_sentence(row, reach_on)
        if not sentence:
            continue
        text, handed = reply_reading.turn_readback(
            session_id, sender, str(row["proposal_id"]), sentence
        )
        if not handed:
            block = text
        elif text:
            handed_note = reply_reading.HANDED_OFF_NOTE
        else:
            handed_note = handed_note or rules_by_hand.REPLY_NONE_NOTE
    restated = None
    if block:  # the rules that remain are read back, owed and registered
        _plugin()._remember_readback(session_id, block)
        _note_verified(session_id, rows)
        restated = _RESTATE_NOTE.format(block=block)
    return reply_reading.joined(handed_note, restated)


def _note_verified(session_id: str, rows: list[dict[str, Any]]) -> None:
    """Exempt from the citation scan only the sentences code found verbatim in
    the person's own email (R2.4, review 2026-11-19)."""
    verified = _plugin()._VERIFIED.get(session_id) or set()
    readback_confirm.note_firm(
        [str(r.get("text") or "") for r in rows if str(r.get("proposal_id")) in verified],
        session_id,
    )


def _could_answer(user_message: str) -> bool:
    """Could this message be an answer at all? Skip the broker when not."""
    who = reply_shape.people(user_message)
    return bool(
        rule_confirm.find_tags(user_message)
        or rule_confirm.read_own_text(user_message).affirmative
        # "1 and 3", "both", "one", "none" answer a numbered list with no yes in them.
        or reply_shape.looks_like_pick(user_message, who)
    )


def _resolve_reply(
    session_id: str,
    sender: str,
    is_admin: bool,
    user_message: str,
    pending: list[dict[str, Any]],
    extra_open: int,
) -> rule_confirm.Verdict:
    """The verdict on this reply. WHICH of the person's own rules it answers is
    the email it replies to, matched by the broker from the reply's own headers
    (R2.1); asked only when they have one open. Acts and rules waiting on an
    administrator never consult it."""
    plug = _plugin()
    if rules_by_hand.on():
        # The rules-by-hand stopgap (shared/rules_by_hand.py) owns every rule of
        # the person's own: nothing here binds, lists, restates or confirms one.
        # Acts and rules waiting on an administrator keep the tag path.
        pending = [p for p in pending if not rule_confirm_reply.is_own_rule(p, sender)]
    binding, extras = None, None
    if any(rule_confirm_reply.is_own_rule(p, sender) for p in pending):
        # Only the reply's own headers bind it (no fallback to an earlier list).
        binding, extras = already_recorded.binding(session_id, sender)
        # Unreadable, or handed to SMD after two read-backs: his rule does not move here.
        gone = None if extras is None else set(extras["handed_off"])
        pending = [
            p
            for p in pending
            if not (
                rule_confirm_reply.is_own_rule(p, sender)
                and (gone is None or str(p.get("proposal_id")) in gone)
            )
        ]
        if extras is not None:
            plug._VERIFIED.setdefault(session_id, set()).update(extras["verified"])
            reply_reading.ROUNDS[session_id] = extras["rounds"]
    who = reply_reading.who(user_message, (extras or {}).get("block", ()), sender)
    return rule_confirm.resolve(
        user_message, pending, sender, is_admin, extra_open=extra_open, binding=binding, who=who
    )


def _confirmed_note(
    session_id: str,
    sender: str,
    is_admin: bool,
    pending: list[dict[str, Any]],
    verdict: rule_confirm.Verdict,
) -> str | None:
    """A CONFIRMED verdict: an act's call, the person's own rules committed by
    code, or a rule waiting on an administrator stashed for its submit."""
    plug = _plugin()
    proposal_id = str(verdict.proposal_id or "")
    row = next((p for p in pending if str(p.get("proposal_id")) == proposal_id), {})
    if str(row.get("kind") or "rule") == act_broker.KIND_TOOL_CALL:
        return plug._act_confirmation_note(session_id, sender, is_admin, proposal_id, row)
    ids = verdict.proposal_ids or (proposal_id,)
    rows = [p for i in ids for p in pending if str(p.get("proposal_id")) == i]
    own = [r for r in rows if rule_confirm_reply.is_own_rule(r, sender)]
    if own:
        return _commit_own_rules(session_id, sender, is_admin, own)
    stash_confirmed(session_id, proposal_id, str(row.get("text") or ""))
    logger.info("hermes-smd-establishment: rule %s confirmed by %s", proposal_id, sender)
    return plug._CONFIRMED_NOTE.format(
        proposal_id=proposal_id,
        text=str(row.get("text") or ""),
        scope=str(row.get("scope") or "firm_adjust"),
    ) + plug._unread_class_note(row)


def _name_candidates(
    pending: list[dict[str, Any]], candidates: tuple[str, ...], sender: str
) -> str:
    """The candidates as a note may name them: a person's own rule by its
    sentence in quotes (never its id: R2.2), anything else by its id."""
    by_id = {str(p.get("proposal_id")): p for p in pending}
    names: list[str] = []
    for proposal_id in candidates:
        row = by_id.get(proposal_id)
        if row is not None and rule_confirm_reply.is_own_rule(row, sender):
            names.append(f'"{" ".join(str(row.get("text") or "").split())}"')
        else:
            names.append(proposal_id)
    return ", ".join(names)


def _owed_recorded(session_id: str, refused: list[dict[str, Any]]) -> bool:
    """Record each confirmed rule the seat could not record as work SMD owes.
    True only when every one was recorded (nothing refused is trivially True)."""
    try:
        origin = SESSION_INBOUND_ORIGIN.bound_this_turn(session_id) or SESSION_INBOUND_ORIGIN.get(
            session_id
        )
    except Exception:  # noqa: BLE001 - no origin: nothing to key the record on
        origin = None
    ref = getattr(origin, "internet_message_id", "") or getattr(origin, "message_id", "")
    return all(
        smd_owed.record(
            smd_owed.KIND_NOT_RECORDED, ref, f"rule {r.get('proposal_id')} confirmed, not recorded"
        )
        for r in refused
    )


def _pick_note(
    session_id: str,
    sender: str,
    is_admin: bool,
    pending: list[dict[str, Any]],
    candidates: tuple[str, ...],
) -> str | None:
    """A reply that binds to no read-back: say the person's open rules back,
    numbered by code, and bind that list so the next answer resolves on it."""
    plug = _plugin()
    by_id = {str(p.get("proposal_id")): p for p in pending}
    rows = [by_id[c] for c in candidates if c in by_id][: rule_confirm_reply.MAX_PICK]
    reach_on = wording_rules.switched_on()
    sentences = [readback_confirm.rule_sentence(r, reach_on) for r in rows]
    pairs = [(r, s) for r, s in zip(rows, sentences, strict=True) if s]
    if not pairs:
        return None
    block = readback_confirm.pick_list([s for _r, s in pairs])
    ids = tuple(str(r.get("proposal_id")) for r, _s in pairs)
    plug._remember_readback(session_id, block)
    _note_verified(session_id, [r for r, _s in pairs])
    # Anything else this sender could be answering (an act, a rule waiting on
    # an administrator) keeps its tag path: it is restated under the list with
    # its own tag, so the one reply covers everything the "yes" might have meant.
    others: list[str] = []
    for row in pending:
        if str(row.get("proposal_id")) in ids or rule_confirm_reply.is_own_rule(row, sender):
            continue
        if rule_confirm.may_confirm(row, sender, is_admin):
            answer = "yes"
        elif rule_confirm.may_confirm(row, sender, is_admin, apply_others=True):
            answer = "apply that"
        else:
            continue
        tag = f"[{rule_confirm.tag_word(row)} {row.get('proposal_id')}]"
        readback = str(row.get("readback") or "").strip()
        if not readback.startswith(tag):
            readback = f"{tag} {' '.join(str(row.get('text') or '').split())}"
        others.append(
            f"Also waiting for your answer:\n\n{readback}\n\nReply `{tag} {answer}` to that one."
        )
    if others:
        block = block + "\n\n" + "\n\n".join(others)
        plug._remember_readback(session_id, block)
    # Owed first; the list is the reply's required words only once owed.
    capped = reply_reading.owe_and_say(session_id, sender, list(ids), len(ids) > 1, block)
    if capped is None:  # refused, not at the cap: no list that no send would bind
        return rules_by_hand.REPLY_NONE_NOTE
    if capped:  # those to SMD; the rest listed again without them
        ref = readback_confirm.inbound_message_id(session_id)
        rest = tuple(c for c in candidates if c not in capped)
        again = _pick_note(session_id, sender, is_admin, pending, rest) if rest else None
        return reply_reading.joined(reply_reading.hand_off(session_id, sender, capped, ref), again)
    return _PICK_NOTE.format(block=block)


def _finish_commit(
    session_id: str,
    committed: list[dict[str, Any]],
    refused: list[dict[str, Any]],
    notes: list[str],
) -> None:
    """The turn's one sentence for what the code commit did (or ``none``)."""
    plug = _plugin()
    plug._COMMITTED_TURN.setdefault(session_id, []).extend(committed)
    owed = [r for r in refused if _owed_recorded(session_id, [r])]
    say = (
        "\n\n".join(
            ([readback_confirm.confirmed_say(committed)] if committed else [])
            + [NOT_RECORDED.format(sentence=_fold(r.get("text"))) for r in owed]
        )
        or "none"
    )
    plug._CONFIRMED_SAY[session_id] = say
    _note_verified(session_id, committed + refused)
    if say == "none":
        # Nothing committed and nothing could be recorded: no sentence, and
        # the reply says nothing about the rule (logged as an ERROR).
        logger.error("hermes-smd-establishment: confirmed rules neither committed nor recorded")
        notes.insert(0, _NOTHING_TO_SAY_NOTE)
    else:
        readback_confirm.register_say(session_id, say)
        notes.insert(0, _OWN_CONFIRMED_NOTE.format(say=say))


def _commit_own_rules(
    session_id: str, sender: str, is_admin: bool, rows: list[dict[str, Any]]
) -> str:
    """Commit each of the person's own confirmed rules, here, by code.

    The model does not call a commit tool per rule (2026-11-19): what was
    confirmed is the seat's decision, and so is recording it, exactly as a
    decline already was. A gate that stops the commit (the possession challenge
    on a seat whose mail custody is not tenant-authenticated) leaves THAT row to
    the path every confirmation took before: it is stashed, and the model is told
    to commit it with ``establish_submit``, which runs the gate and says what it
    needs. Personal rules are committed with ``append`` so one never replaces
    another the person stated in the same breath.
    """
    plug = _plugin()
    if not readback_confirm.can_register(session_id):
        # The relay cannot hold this reply to the confirmation's sentence (no
        # email bound this turn, or no reply plugin): nothing is committed, so
        # nothing is ever recorded that the person is not told exactly.
        logger.warning(
            "hermes-smd-establishment: a confirmation arrived on a turn that cannot say it"
        )
        return _NOTHING_TO_SAY_NOTE
    source_ref = (readback_confirm.inbound_message_id(session_id) or "reply-confirmation")[:200]
    committed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    notes: list[str] = []
    for row in rows:
        proposal_id = str(row.get("proposal_id") or "")
        scope = str(row.get("scope") or "firm_adjust")
        text = str(row.get("text") or "")
        if scope == "person":
            gate = plug._person_possession_gate(sender, TOOL_SUBMIT)
        elif is_admin:
            gate = plug._possession_gate(sender, TOOL_SUBMIT)
        else:
            gate = {"action": "block", "message": plug._REFUSAL_MESSAGE}
        if gate is not None:
            stash_confirmed(session_id, proposal_id, text)
            notes.append(
                _OWN_FALLBACK_NOTE.format(
                    text=text,
                    scope=scope,
                    proposal_id=turn_handle(session_id, proposal_id),
                    gate=str(gate.get("message") or ""),
                )
            )
            continue
        submit = {
            "scope": scope,
            "proposal_id": proposal_id,
            "instructed_by": sender,
            "source_ref": source_ref,
            **({"append": True} if scope == "person" else {}),
        }
        try:
            answer = json.loads(plug._submit(submit, session_id=session_id))
        except Exception:  # noqa: BLE001 - an unreachable broker committed nothing
            logger.warning("hermes-smd-establishment: own-rule commit failed", exc_info=True)
            answer = {"ok": False, "message": "the seat could not reach its rule store"}
        if isinstance(answer, dict) and answer.get("ok"):
            committed.append(row)
            readback_confirm.watch_install(
                plug._broker_request, proposal_id, str(answer.get("run_id") or "")
            )
            plug._note_confirmed_text(session_id, text)
            logger.info(
                "hermes-smd-establishment: own rule %s committed for %s", proposal_id, sender
            )
        else:
            logger.warning(
                "hermes-smd-establishment: own rule %s confirmed but not committed: %s",
                proposal_id,
                str((answer or {}).get("message") or "")[:200] if isinstance(answer, dict) else "",
            )
            refused.append(row)
    if committed or refused:
        _finish_commit(session_id, committed, refused, notes)
    return " ".join(notes)


def own_readback_for(
    session_id: str, args: dict[str, Any], response: dict[str, Any], readback: str
) -> tuple[bool, str, dict[str, str] | None]:
    """``(own, readback, handed)`` for a proposal the broker accepted. ``handed``
    is the whole tool result when the rule was read back twice already (the
    broker returns that same open rule for a restatement of it): the stopgap
    sentence, or say nothing when SMD could not be told; never a read-back.

    A person's OWN rule (not waiting on an administrator) is shown its sentence,
    its reach and how to answer, with no tag and no id; the turn's read-back of
    every such rule is owed and registered (:func:`_own_turn_readback`), and the
    sent message is bound to each once the relay has sent it. Either way the
    broker's own sentence is exempt from the citation scan as written (R2.4).
    A sentence the same person already committed is answered "already recorded"
    and read back to nobody (2026-11-21)."""
    if (done := already_recorded.propose_answer(response)) is not None:
        return False, "", done
    proposal_id = str(response.get("proposal_id") or "")
    own = bool(proposal_id) and not bool(response.get("for_admin"))
    text = str(args.get("text") or "")
    plug = _plugin()
    if _said_by_them(session_id, text):
        # The model's argument earns the citation exemption only when code finds
        # it verbatim in the person's own email this turn (R2.4).
        plug._VERIFIED.setdefault(session_id, set()).add(proposal_id)
        readback_confirm.note_firm([text], session_id)
    if own and readback:
        sender = plug._normalize_address(args.get("instructed_by"))
        _restate(session_id, sender, args, proposal_id)
        readback, handed = reply_reading.turn_readback(session_id, sender, proposal_id, readback)
        if handed:
            return own, readback, ({"say": readback} if readback else _SAY_NONE)
    return own, readback, None


_SAY_NONE = {"say": "none", "instruction": rules_by_hand.NONE_INSTRUCTION}


def _said_by_them(session_id: str, text: str) -> bool:
    entry = _plugin()._ADMIN_STASH.get(session_id) or {}
    own_text = entry.get("own_text") if isinstance(entry, dict) else ""
    folded = " ".join(text.split())
    return bool(folded) and folded in " ".join(str(own_text or "").split())


def _restate(session_id: str, sender: str, args: dict[str, Any], new_id: str) -> None:
    """``restates`` names an older open own proposal (its turn number, or its
    sentence): the broker ends it silently when scope and kinds match (R9)."""
    value = str(args.get("restates") or "").strip()
    if not value or not new_id:
        return
    plug = _plugin()
    turn = plug._OWN_TURN.get(session_id) or {}
    old = ""
    if value.isdigit() and 0 < int(value) <= len(turn):
        old = list(turn)[int(value) - 1]
    else:
        wanted = " ".join(value.split()).rstrip(".").lower()
        for row in plug._fetch_pending(sender, False):
            same = " ".join(str(row.get("text") or "").split()).rstrip(".").lower() == wanted
            if same and rule_confirm_reply.is_own_rule(row, sender):
                old = str(row.get("proposal_id") or "")
                break
    if not old or old == new_id:
        return
    try:
        answer = plug._broker_request(
            {
                "action": "establish_restated",
                "sender": sender,
                "old_proposal_id": old,
                "new_proposal_id": new_id,
            }
        )
    except Exception:  # noqa: BLE001 - nothing closed: both stay open, as before
        logger.warning("hermes-smd-establishment: restatement not recorded", exc_info=True)
        return
    if isinstance(answer, dict) and answer.get("closed"):
        turn.pop(old, None)


def reopen_note(session_id: str, sender: str, user_message: Any) -> str | None:
    """A plain yes with nothing open: the sender's own rule that lapsed silently
    is proposed again and read back (R8). Never says it lapsed."""
    if rules_by_hand.on():
        return None  # the stopgap owns rules: nothing is re-opened or read back
    plug = _plugin()
    _binding, extras = already_recorded.binding(session_id, sender)
    block = (extras or {}).get("block", ())
    if not rule_confirm_reply.pure_affirmative(
        user_message, None, reply_reading.who(user_message, block, sender)
    ):
        return None
    try:
        answer = plug._broker_request(
            {
                "action": "establish_reopen_lapsed",
                "sender": sender,
                # The broker reopens only a rule the read-back THIS email replies to.
                "inbound_message_id": readback_confirm.inbound_message_id(session_id),
                "source_ref": readback_confirm.inbound_message_id(session_id) or "reopened",
            }
        )
    except Exception:  # noqa: BLE001 - nothing reopened: the model answers as before
        logger.debug("hermes-smd-establishment: reopen lookup failed", exc_info=True)
        return None
    if not isinstance(answer, dict) or not answer.get("reopened"):
        return None
    row = {"text": answer.get("readback") or "", "subject": answer.get("subject")}
    sentence = readback_confirm.rule_sentence(row, wording_rules.switched_on())
    block, handed = reply_reading.turn_readback(
        session_id, sender, str(answer.get("proposal_id") or ""), sentence
    )
    if handed:
        return reply_reading.HANDED_OFF_NOTE if block else rules_by_hand.REPLY_NONE_NOTE
    plug._remember_readback(session_id, block)
    return _PICK_NOTE.format(block=block)


def without_ids(response: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in response.items() if k not in ("proposal_id", "duplicate_of")}


def pending_view(response: Any, args: dict[str, Any], session_id: str) -> Any:
    """``establish_pending`` as the model sees it (R2.2, R2.3): the rules this
    turn committed, and the person's own rules committed in the last hour by an
    earlier turn (2026-11-23), appended as ``committed`` (the broker's open list
    no longer carries them, and a confirmed rule must not read as gone), and no
    id on a row of the person's own."""
    if not isinstance(response, dict) or not isinstance(response.get("pending"), list):
        return response
    sender = _plugin()._normalize_address(args.get("sender"))
    rows = [r for r in response["pending"] if isinstance(r, dict)]
    seen = {str(r.get("proposal_id") or "") for r in rows}
    earlier = already_recorded.recent_as_own(session_id, sender)
    for row in [*(_plugin()._COMMITTED_TURN.get(session_id) or ()), *earlier]:
        if str(row.get("proposal_id") or "") not in seen:
            seen.add(str(row.get("proposal_id") or ""))
            rows.append({**row, "state": "committed"})
    return {**response, "pending": [readback_confirm.for_model(r, sender) for r in rows]}


def unproven(session_id: str) -> tuple[str, str, bool]:
    """``(proposal id, how a note may name it, committed)`` of the first rule
    confirmed or committed this turn that the seat has not seen install, or
    ``("", "", False)``. A person's own rule committed by code is named by its
    sentence, never its id, and ``committed`` says so: the gate then tells the
    model the rule IS recorded (2026-11-21; its text used to say the commit had
    failed, and the attorney was told nothing was recorded)."""
    plug = _plugin()
    committed = plug._COMMITTED_TURN.get(session_id) or []
    confirmed = set(plug._CONFIRMED_STASH.get(session_id) or ())
    confirmed |= {str(r.get("proposal_id") or "") for r in committed}
    installed = plug._INSTALLED_RULES.get(session_id, set())
    pending = sorted(p for p in confirmed if p and p not in installed)
    if not pending:
        return "", "", False
    for row in committed:
        if str(row.get("proposal_id") or "") == pending[0]:
            return pending[0], f'"{" ".join(str(row.get("text") or "").split())}"', True
    return pending[0], pending[0], False


def quoted_runs(session_id: str) -> list[str]:
    """The seat's own sentences a reply quotes this turn: never read as claims."""
    plug = _plugin()
    return [
        *(plug._CONFIRMED_TEXT.get(session_id) or []),
        plug._CONFIRMED_SAY.get(session_id) or "",
    ]


def stash_confirmed(session_id: str, proposal_id: str, text: str) -> None:
    """This turn's sender confirmed ``proposal_id``; a submit naming it may go."""
    if not session_id or not proposal_id:
        return
    plug = _plugin()
    plug._CONFIRMED_STASH.setdefault(session_id, set()).add(proposal_id)
    plug._note_confirmed_text(session_id, text)


def flush_bindings(with_installs: bool = False) -> None:
    """From the sweeper: ask the broker about the installs of own rules
    committed by code (bindings form at the send now, nothing to flush).
    Never raises."""
    if not with_installs:
        return
    try:
        readback_confirm.check_installs(_plugin()._broker_request)
    except Exception:  # noqa: BLE001 - the install watch never costs a turn
        logger.debug("hermes-smd-establishment: the install watch failed", exc_info=True)


def confirmed_here(session_id: Any, proposal_id: str) -> bool:
    """Did THIS turn's sender confirm ``proposal_id``? The only id a submit may name."""
    stash = _plugin()._CONFIRMED_STASH
    return isinstance(session_id, str) and proposal_id in (stash.get(session_id) or ())


def _fold(text: Any) -> str:
    return " ".join(str(text or "").split()).rstrip(".")
