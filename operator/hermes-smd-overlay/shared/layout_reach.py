"""A layout rule covers the kinds the firm said, never every kind (2026-11-06).

THE DEFECT. :func:`shared.layout_rules.compile_rule` reads a rule's document
kinds from its words, and a rule that names none compiles with ``doc_classes ==
[]``, which the drafting lane applies to EVERY kind it lays out
(``drafting/firm_rules.py`` ``for_class``). An attorney who says "double space
the headings" while correcting a brief has then changed his memos and his
discovery too: the widening the wording path had (drafting answer key rows 60
and 131), one compiler over.

THE RULE, the wording path's (:mod:`shared.rule_reach`): a rule's kinds stand
only if the requester's own words name them. Nothing in code records which job a
reply thread is about, so "the kind of the request it answered" is the kinds the
requester's own message names, and a rule that names none is sent back at
proposal with the instruction to ask which documents it is for. A confirmed rule
therefore carries its kinds (``doc_kinds``, ``shared/wording_rules.py``), and
this module compiles it for exactly those of them the drafting lane lays out. A
rule whose kinds include none of them (a demand letter, a letter) compiles no
layout clause at all.

EXISTING RULES ARE NOT REWRITTEN. A rule confirmed before its kinds were
recorded compiles exactly as :func:`shared.layout_rules.compile_adjustments`
always compiled it, so every rule installed on a seat keeps the kinds it has.
:func:`unscoped_report` lists the installed rules whose own words name no kind,
and what each applies to now, for a person to read before a seat is switched on.

THE SWITCH is the seat's ``logic_in_code.wording_rules``, the one the wording
path uses. Off, :func:`compile_adjustments` returns what
:func:`shared.layout_rules.compile_adjustments` returns, byte for byte.

STDLIB ONLY beyond the overlay's own shared modules.
"""

from __future__ import annotations

import hashlib
from typing import Any

from shared import layout_rules, logic_in_code, rule_reach

#: The manifest's record of whether layout.json was compiled with recorded kinds.
MANIFEST_KEY = "layout_reach"

#: Why a clause of a rule that covers no laid-out kind is not compiled.
NO_LAID_OUT_KIND = (
    "the rule covers no document the drafting lane lays out, so its layout is not applied"
)


def switched_on() -> bool:
    return logic_in_code.load_switch(logic_in_code.WORDING_RULES).enforcing


def compiled_as_switch(installed_manifest: dict[str, Any]) -> bool:
    """True iff the installed layout.json was compiled the way the switch now says.

    A manifest written before this existed was compiled as "off", which is what
    every seat's switch says today, so no seat recompiles on this alone.
    """
    return bool(installed_manifest.get(MANIFEST_KEY, False)) == switched_on()


def _laid_out(kinds: Any) -> list[str]:
    recorded = kinds if isinstance(kinds, (list, tuple)) else ()
    return [k for k in layout_rules.DOC_CLASSES if k in recorded]


def _scoped_rule(adj: dict[str, Any]) -> dict[str, Any]:
    """One rule compiled for the kinds it recorded, laid-out kinds only."""
    text, at = str(adj.get("text") or ""), str(adj.get("at") or "")
    kinds = _laid_out(adj.get("doc_kinds"))
    if kinds:
        return layout_rules.compile_rule(text, at, doc_classes=kinds)
    # Its own kinds, never ``[]``: an empty list is "every kind" to the lane.
    prose = [{"text": wrote, "reason": NO_LAID_OUT_KIND} for wrote, _ in layout_rules.clauses(text)]
    return {"doc_classes": list(adj.get("doc_kinds") or ()), "compiled": [], "prose": prose}


def compile_adjustments(adjustments: Any, reach_on: bool) -> dict[str, Any]:
    """The ``layout.json`` document for one class's adjustments, oldest first.

    Off, or for a rule that recorded no kinds, exactly
    :func:`shared.layout_rules.compile_adjustments`. On, a rule that recorded its
    kinds compiles for them and nothing else.
    """
    if not reach_on:
        return layout_rules.compile_adjustments(adjustments)
    rules: list[dict[str, Any]] = []
    for adj in adjustments or ():
        if not isinstance(adj, dict):
            continue
        rule_id, text = str(adj.get("id") or ""), str(adj.get("text") or "")
        if adj.get("doc_kinds"):
            body = _scoped_rule(adj)
        else:
            body = layout_rules.compile_rule(text, str(adj.get("at") or ""))
        rules.append(
            {
                "rule_id": rule_id,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                **body,
            }
        )
    return {"schema_version": layout_rules.SCHEMA_VERSION, "rules": rules}


def unscoped_report(layout_doc: Any, texts: dict[str, str]) -> list[dict[str, Any]]:
    """The installed rules whose own words name no laid-out kind, and what each
    applies to now. Read-only: it writes nothing and changes no rule.

    ``layout_doc`` is an installed ``layout.json``; ``texts`` maps each rule id to
    the firm's original sentence. A rule with no compiled clause applies to
    nothing and is left out; one whose text is missing is listed, because its
    words cannot be read to say it is scoped.
    """
    rules = layout_doc.get("rules") if isinstance(layout_doc, dict) else None
    out: list[dict[str, Any]] = []
    for rule in rules or ():
        if not isinstance(rule, dict) or not rule.get("compiled"):
            continue
        rule_id = str(rule.get("rule_id") or "")
        text = texts.get(rule_id)
        if text is not None and _laid_out(rule_reach.kinds_named(text)):
            continue
        classes = list(rule.get("doc_classes") or [])
        out.append(
            {
                "rule_id": rule_id,
                "text_known": text is not None,
                "applies_to": classes or list(layout_rules.DOC_CLASSES),
            }
        )
    return out


__all__ = [
    "MANIFEST_KEY",
    "NO_LAID_OUT_KIND",
    "compile_adjustments",
    "compiled_as_switch",
    "switched_on",
    "unscoped_report",
]
