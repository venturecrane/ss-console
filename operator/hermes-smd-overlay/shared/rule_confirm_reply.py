"""Confirm-by-reply: what a person's reply does to their OWN open rules (2026-11-20).

The half of :mod:`shared.rule_confirm` for rules a person stated about their
own say (not an act, not an operations request, not a rule waiting on an
administrator). Acts and administrators' rows keep the tag path of
:func:`shared.rule_confirm.resolve` exactly; nothing here reaches them.

WHY A WRONG CONFIRMATION IS IMPOSSIBLE BY CONSTRUCTION. Four ways the earlier
build recorded a rule the person did not agree to, and what closes each:

* CONTENT UNDER A SIGN-OFF ("Yes / Thanks, / Actually make that 12pt"). Assent
  is read by :mod:`shared.reply_shape`, a whitelist: every line of the sender's
  own text must be a plain yes, a courtesy, or signature-shaped. There is no
  list of words that change an answer, so nothing unknown is ever skipped.
* A TAG IN QUOTED HISTORY OVERRIDING THE BINDING. Only the sender's own text is
  read for an own rule's tag (:func:`own_tags`). A tag quoted below the reply
  names nothing; the binding (the read-back the reply answers, matched by the
  broker from its headers) decides which rules.
* AN ACT CONFIRMED BY ITS PHRASE. There is no act shortcut. An act is answered
  on the tag path of :func:`shared.rule_confirm.resolve`, unchanged.
* A BARE YES TO A LIST CONFIRMING A RULE AND ITS RESTATEMENT. A restatement
  closes the older proposal at propose time (``own_rule_verbs.restated``), and
  only open rows are ever listed or confirmed (:func:`resolve_bound`), so the
  older one cannot be on the list a yes answers.

What a reply bound to a read-back confirms: a pure yes confirms every rule it
carried that is still open; a pick from a numbered list confirms those numbers.
Anything else with assent in it is read back again ("restated"). A reply with no
binding that is a pure yes or a pick gets the numbered list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared import reply_shape
from shared import rule_confirm as rc


@dataclass(frozen=True)
class ReadbackBinding:
    """The read-back a reply answers, as the broker matched it.

    The broker records which of a person's OWN proposals each read-back it sent
    carried, keyed by the sent message's id, and matches an inbound reply's
    ``In-Reply-To`` (else its last ``References`` entry) against it, from a
    reply whose ``From`` is the person it was sent to. ``proposal_ids`` is that
    message's proposals in the order it showed them; ``listed`` is True when
    that message was the numbered list. The binding names WHICH rules; the
    person's own words decide WHETHER.
    """

    proposal_ids: tuple[str, ...]
    listed: bool = False


#: The longest list code renders, and so the largest number a pick may name.
MAX_PICK = 20
#: Read-backs a rule may be sent before it is handed to SMD (the broker enforces
#: the same number: workspace_broker/readback_rounds.MAX_READBACKS).
MAX_READBACKS = 2


def is_own_rule(row: dict[str, Any], sender: Any) -> bool:
    """A RULE the sender stated about their own say: the only rows this module
    reads. Acts, operations requests and rules waiting on an administrator keep
    the tag path exactly."""
    kind = str(row.get("kind") or "rule")
    if kind in ("tool_call", rc.OPS_REQUEST_KIND) or bool(row.get("for_admin")):
        return False
    stated_by = str(row.get("instructed_by") or "").strip().lower()
    return bool(stated_by) and stated_by == str(sender or "").strip().lower()


def _ids(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(r["proposal_id"]) for r in rows)


def _confirmed(ids: tuple[str, ...], candidates: tuple[str, ...]) -> rc.Verdict:
    return rc.Verdict(
        kind=rc.CONFIRMED, proposal_id=ids[0], candidates=candidates, proposal_ids=ids
    )


def _ask(reason: str, ids: tuple[str, ...]) -> rc.Verdict:
    return rc.Verdict(kind=rc.ASK, reason=reason, candidates=ids)


def own_text(message: Any) -> str:
    """The sender's own lines (quoted history removed), for tags and assent."""
    return "\n".join(reply_shape.own_lines(message))


def own_tags(message: Any) -> tuple[str, ...]:
    """The rule/act tags the sender typed in their OWN text: never quoted history."""
    return rc.find_tags(own_text(message))


def pure_affirmative(
    message: Any, count: int | None = None, who: reply_shape.People | None = None
) -> bool:
    """Is the sender's own text ONLY a plain yes (and a signature)?

    ``count`` is how many rules the read-back answered carried ("Yes to both"
    agrees with a read-back of two). Tags in the sender's own text are read as
    nothing here; the caller decides what they name."""
    who = who or reply_shape.people(message)
    return reply_shape.is_pure_yes(reply_shape.strip_tags(message, rc.RULE_TAG), who, count)


def read_pick(
    message: Any, listed: int, who: reply_shape.People | None = None
) -> tuple[int, ...] | None:
    """The numbers a reply picked from a list of ``listed`` (``()`` for "none"),
    or ``None`` when it is not a pick (:func:`shared.reply_shape.pick`)."""
    return reply_shape.pick(message, who or reply_shape.people(message), listed)


def _assent(message: Any) -> bool:
    """Any affirmative or defeater in the sender's own text: a reply that is
    about the read-back at all, as opposed to a new email in the thread."""
    reading = rc.read_own_text(own_text(message))
    return reading.affirmative or reading.negated


def resolve_bound(
    message: Any,
    rows: list[dict[str, Any]],
    binding: ReadbackBinding,
    address: str,
    who: reply_shape.People,
) -> rc.Verdict | None:
    """A reply to a read-back the broker recorded. ``None`` falls through.

    Only rules still open and still the sender's own are in play, so a binding
    can never reach a rule of somebody else's, a rule waiting on an
    administrator, an act, or a proposal a restatement already closed."""
    by_id = {str(r["proposal_id"]): r for r in rows}
    carried = binding.proposal_ids
    bound_open = [by_id[p] for p in carried if p in by_id and is_own_rule(by_id[p], address)]
    if not bound_open:
        return None
    open_ids = _ids(bound_open)
    if binding.listed:
        picked = read_pick(message, len(carried), who)
        if picked is None and pure_affirmative(message, len(carried), who):
            # The list asks "Reply all to confirm every one": a plain yes is all.
            picked = tuple(range(1, len(carried) + 1))
        if picked == ():
            return rc.Verdict(kind=rc.NONE)
        if picked is not None:
            if any(n < 1 or n > len(carried) for n in picked):
                return _ask(rc.ASK_PICK, open_ids)
            chosen = tuple(carried[n - 1] for n in picked if carried[n - 1] in open_ids)
            return _confirmed(chosen, open_ids) if chosen else _ask(rc.ASK_PICK, open_ids)
        if reply_shape.looks_like_pick(message, who):
            # "both" on a list of three, "one", "1 only for Craig": list again.
            return _ask(rc.ASK_PICK, open_ids)
    elif pure_affirmative(message, len(carried), who):
        return _confirmed(open_ids, open_ids)
    reading = rc.read_own_text(own_text(message))
    if reading.negated and not reading.affirmative:
        return rc._declined_or_ask(bound_open, open_ids, reading)
    if reading.affirmative or reading.negated or reply_shape.looks_like_pick(message, who):
        # Assent with anything else in it ("Yes\nfor letters to Craig", a pick
        # of a read-back that was not a list): read it back again.
        return _ask(rc.ASK_QUALIFIED, open_ids)
    return None


def _tagged_own(
    message: Any, own: list[dict[str, Any]], address: str, who: reply_shape.People
) -> rc.Verdict | None:
    """Tags of the sender's own rules typed in their OWN text ("[rule A] yes and
    [rule B] yes", read-backs of the old release quoted into the reply)."""
    typed = set(own_tags(message))
    named = [r for r in own if str(r["proposal_id"]) in typed]
    if not named:
        return None
    ids = _ids(named)
    reading = rc.read_own_text(own_text(message))
    if reading.apply_others:
        # "apply that" is an administrator putting SOMEONE ELSE's rule in force.
        return _ask(rc.ASK_NOT_THEIRS, ids)
    if pure_affirmative(message, len(ids), who):
        return _confirmed(ids, ids)
    if reading.negated and not reading.affirmative:
        return rc._declined_or_ask(named, ids, reading)
    return _ask(rc.ASK_QUALIFIED if reading.affirmative else rc.ASK_NEEDS_AFFIRMATIVE, ids)


def own_verdict(
    message: Any,
    rows: list[dict[str, Any]],
    address: str,
    binding: ReadbackBinding | None,
    who: reply_shape.People | None = None,
) -> tuple[rc.Verdict | None, list[dict[str, Any]], tuple[str, ...], bool]:
    """``(verdict, rows, tags, mixed)`` for :func:`shared.rule_confirm.resolve`.

    A verdict means this module decided the reply. Otherwise the tag path gets
    ``rows`` (those that are NOT the sender's own rules), ``tags`` (every tag in
    the message but an own rule's: an own rule never reaches the tag path, so a
    quoted tag can never confirm one), and ``mixed`` (an own rule's tag sits
    beside the others, which asks which, as two named things always did)."""
    decided, others = _own_decision(
        message, rows, address, binding, who or reply_shape.people(message)
    )
    own = set(own_ids(rows, address))
    every = rc.find_tags(message)
    return decided, others, tuple(t for t in every if t not in own), bool(own & set(every))


def _own_decision(
    message: Any,
    rows: list[dict[str, Any]],
    address: str,
    binding: ReadbackBinding | None,
    who: reply_shape.People,
) -> tuple[rc.Verdict | None, list[dict[str, Any]]]:
    own = [r for r in rows if is_own_rule(r, address)]
    others = [r for r in rows if not is_own_rule(r, address)]
    if not own:
        return None, rows
    if typed := _tagged_own(message, own, address, who):
        return typed, others
    if binding is not None and binding.proposal_ids:
        if (bound := resolve_bound(message, rows, binding, address, who)) is not None:
            return bound, others
    ids = _ids(own)
    if pure_affirmative(message, None, who) or read_pick(message, MAX_PICK, who):
        return _ask(rc.ASK_PICK, ids), others
    quoted = set(rc.find_tags(message)) & set(ids)
    if quoted and _assent(message):
        # Quoted tags of their own rules (an old read-back) and assent that is
        # not plain: read those back again rather than guess.
        return _ask(rc.ASK_QUALIFIED, tuple(i for i in ids if i in quoted)), others
    return None, others


def own_ids(rows: list[dict[str, Any]], address: str) -> tuple[str, ...]:
    """The ids of the sender's own open rules among ``rows``, in their order."""
    return _ids([r for r in rows if is_own_rule(r, address)])


__all__ = [
    "MAX_PICK",
    "ReadbackBinding",
    "is_own_rule",
    "own_ids",
    "own_tags",
    "own_text",
    "own_verdict",
    "pure_affirmative",
    "read_pick",
    "resolve_bound",
]
