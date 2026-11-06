"""The install letter for a wording rule a queued job now reads (2026-11-06).

Until now every ``work_product`` wording rule was answered "recorded, but it is
not yet in effect", which was true: no job read it. With the seat's
``logic_in_code.wording_rules`` on, the drafting and demand lanes put a rule into
what they send the model for the kinds and the attorney it covers, so for those
kinds "in effect" is now true and is said. For a kind no job reads (a letter) the
letter still says recorded and SMD is still paged, because there it is still true.

WHAT MAY BE CLAIMED comes from :func:`shared.wording_rules.lane_truth` alone: the
installed file, read through the lanes' own reader, digest included, and the
switch. Never the rule's text and never the proposal row.

The templates live here so :mod:`shared.rule_dispatch` keeps one call site; its
composing helpers (the outbound-scan fallback, the clause letter, the page) are
passed in rather than imported, so neither module imports the other.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shared import wording_rules, wording_rules_store

_IN_EFFECT_SUBJECT = "Your rule is in effect [rule {proposal_id}]"
_PARTLY_SUBJECT = "Your rule is partly in effect [rule {proposal_id}]"

_WORDING_BODY = """{applied_by} applied the rule you asked for:

{readback}

It is in effect from the next {kinds}{whose}.
{unread}"""

_UNREAD = """
For {kinds} we have recorded the rule, but it is not yet in effect: that work is
produced by a part of the Operator that does not read the firm's rules yet, so the
rule has been passed to SMD to build into it.
"""


def _whose(attorney: Any) -> str:
    return f" in {attorney}'s name" if attorney else ""


def letter(
    proposal_id: str,
    readback: str,
    tag: str,
    by: str,
    output_class: str,
    *,
    compose: Callable[..., str],
    clause_truth: Callable[[str, str], dict[str, list[dict[str, Any]]] | None],
    clause_letter: Callable[..., tuple[str, str]],
    page: Callable[[str, str], None],
) -> tuple[str, str] | None:
    """``(subject, body)`` for a rule a lane now writes to, or ``None`` to fall
    through to the letter the seat sent before this existed."""
    truth = wording_rules.lane_truth(proposal_id, output_class)
    if truth is None:
        return None
    unread = truth["unread"]
    if unread:
        page(proposal_id, output_class)
    unread_text = _UNREAD.format(kinds=wording_rules_store.kinds_said(unread)) if unread else ""
    layout = clause_truth(proposal_id, output_class)
    if layout and layout["compiled"]:
        # A rule with layout clauses too: the layout clauses are applied by code,
        # and its prose clauses are now read by the lane, so every clause is said
        # as in effect, by the clause letter that already lists them.
        whole = {"compiled": [*layout["compiled"], *layout["prose"]], "prose": []}
        subject, body = clause_letter(proposal_id, readback, tag, by, output_class, whole)
        if unread:
            subject = _PARTLY_SUBJECT.format(proposal_id=proposal_id)
        return subject, body + unread_text
    subject = (_PARTLY_SUBJECT if unread else _IN_EFFECT_SUBJECT).format(proposal_id=proposal_id)
    body = compose(
        _WORDING_BODY,
        readback,
        tag,
        applied_by=by,
        kinds=_singular_list(truth["read"]),
        whose=_whose(truth["attorney"]),
        unread=unread_text,
    )
    return subject, body


def _singular_list(kinds: list[str]) -> str:
    """``mediation brief or memo``: what "the next ..." takes."""
    words = [wording_rules_store.KIND_WORDS[k][0] for k in wording_rules_store.KINDS if k in kinds]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " or " + words[-1]


__all__ = ["letter"]
