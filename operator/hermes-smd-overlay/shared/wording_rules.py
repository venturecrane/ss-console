"""A firm's confirmed wording rules, rendered for the job lanes and judged for the letter.

WHY (2026-11-06). Only chat turns read a firm's ``work_product`` rules, and every
document a firm receives is made by a queued job, so a confirmed wording rule
bound to nothing the firm got. Three halves, all code:

* the RECORD: an adjustment may carry the document kinds and the attorney it
  covers (:func:`reach_fields`), validated the same way by the intake and the
  spec applier, which both import this function;
* the FILE: the root spec applier renders every ``work_product`` adjustment that
  carries a recorded reach into ``classes/work_product/wording.json``
  (:func:`compile_adjustments`), committed by the root-computed manifest exactly
  as ``layout.json`` is; the jobs runner reads it through its twin of
  :mod:`shared.wording_rules_store`;
* the TRUTH: :func:`lane_truth` answers, from the installed file read the way the
  lanes read it and from the seat's switch, which of the rule's kinds a job now
  writes to. The install letter says "in effect" from that and nothing else.

A RULE WITH NO RECORDED REACH IS NOT READ BY A LANE. Rules confirmed before the
reach was recorded say nothing a job could select on except their words, and
reading a reach out of words is how a tone rule for briefs reached memos
(2026-11-05). Those rules keep the letter they were sent ("recorded") until the
firm restates them.

THE SWITCH is the seat's ``logic_in_code.wording_rules`` (ADR 0104). In
``observe``, the default and every seat's value until it is switched on, the file
is still written but no lane reads it and no letter calls a rule in effect.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from shared import layout_rules, logic_in_code, wording_rules_store

#: The output class whose rules a job lane reads for wording.
RULE_CLASS = wording_rules_store.RULE_CLASS
#: The two spec properties a wording rule may be installed under.
PROPERTIES = ("voice", "format")
#: The seat switch (``shared.logic_in_code``).
SWITCH = logic_in_code.WORDING_RULES
#: Ceiling on one recorded attorney name.
MAX_ATTORNEY_CHARS = 120


def reach_fields(item: dict[str, Any], where: str) -> tuple[dict[str, Any], list[str]]:
    """``({doc_kinds, attorney, reader}, errors)``, each only when present.

    ``doc_kinds`` is a non-empty list of distinct kinds from
    :data:`shared.wording_rules_store.KINDS`; ``attorney`` is a non-empty name;
    ``reader`` is one of :data:`shared.wording_rules_store.READERS`. An attorney
    or a reader with no kinds is refused: which documents, whose and for whom is
    one reach, and part of it is not a rule a job could select on.
    """
    out: dict[str, Any] = {}
    errors: list[str] = []
    kinds = item.get("doc_kinds")
    if kinds is not None:
        if (
            not isinstance(kinds, list)
            or not kinds
            or len(set(kinds)) != len(kinds)
            or any(k not in wording_rules_store.KINDS for k in kinds)
        ):
            errors.append(
                f"{where}.doc_kinds: must be a non-empty list of distinct kinds from "
                f"{list(wording_rules_store.KINDS)}"
            )
        else:
            out["doc_kinds"] = [k for k in wording_rules_store.KINDS if k in kinds]
    attorney = item.get("attorney")
    if attorney is not None:
        if (
            not isinstance(attorney, str)
            or not attorney.strip()
            or len(attorney) > MAX_ATTORNEY_CHARS
            or "\n" in attorney
        ):
            errors.append(f"{where}.attorney: must be one line naming the attorney")
        elif kinds is None:
            errors.append(f"{where}.attorney: a rule naming an attorney must name its doc_kinds")
        else:
            out["attorney"] = " ".join(attorney.split())
    reader = item.get("reader")
    if reader is not None:
        if reader not in wording_rules_store.READERS:
            errors.append(f"{where}.reader: must be one of {list(wording_rules_store.READERS)}")
        elif kinds is None:
            errors.append(f"{where}.reader: a rule naming a reader must name its doc_kinds")
        else:
            out["reader"] = reader
    return out, errors


def _wording(prop: str, text: str) -> list[str]:
    """What the rule says in words: the whole sentence for a voice rule, and for a
    format rule the clauses the layout compiler left as prose (the rest is applied
    by code, and saying it to the model too would apply it twice)."""
    if prop != layout_rules.LAYOUT_PROPERTY:
        return [text]
    return [c["text"] for c in layout_rules.compile_rule(text)["prose"]]


def _rule(prop: str, adj: dict[str, Any]) -> dict[str, Any] | None:
    kinds = adj.get("doc_kinds")
    if not kinds:
        return None
    text = str(adj.get("text") or "")
    return {
        "rule_id": str(adj.get("id") or ""),
        "text": text,
        "wording": _wording(prop, text),
        "doc_kinds": list(kinds),
        "attorney": adj.get("attorney"),
        "reader": adj.get("reader"),
        "property": prop,
        "at": str(adj.get("at") or ""),
        "instructed_by": str(adj.get("instructed_by") or ""),
        "applied_by": adj.get("applied_by"),
    }


def compile_adjustments(by_property: dict[str, Any]) -> dict[str, Any] | None:
    """The ``wording.json`` document for the class's adjustments, or ``None``.

    ``by_property`` maps ``voice`` / ``format`` to that property's parsed
    adjustments. ``None`` when no adjustment carries a recorded reach: no file is
    then written, and a seat with none reads as having no wording rules.
    """
    rules = [
        rule
        for prop in PROPERTIES
        for adj in by_property.get(prop) or ()
        if isinstance(adj, dict) and (rule := _rule(prop, adj)) is not None
    ]
    if not rules:
        return None
    return {"schema_version": wording_rules_store.SCHEMA_VERSION, "rules": rules}


def render(doc: dict[str, Any]) -> bytes:
    """The bytes the applier writes, and hashes, for ``doc``."""
    return json.dumps(doc, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def switched_on(path: str | None = None) -> bool:
    """True iff this seat's switch says the lanes read wording rules."""
    return logic_in_code.load_switch(SWITCH, path).enforcing


def reach_required(output_class: str, adjustment: dict[str, Any] | None) -> list[str]:
    """Why a confirmed rule may not install, switched on: a rule about the firm's
    documents that records no kinds would otherwise compile for every kind, the
    widening the reach rule ends (``shared/rule_reach.py``). Off, ``[]`` always,
    so a seat installs exactly as before."""
    if adjustment is None or output_class != RULE_CLASS or not switched_on():
        return []
    if adjustment.get("doc_kinds"):
        return []
    return [
        "a rule about the firm's documents must name the kinds of document it covers; "
        "ask the person which documents it is for, then propose it again"
    ]


def lane_truth(
    rule_id: str, output_class: str, spec_dir: str | Path | None = None
) -> dict[str, Any] | None:
    """``{read, unread, attorney}`` for an installed rule a lane now reads, else ``None``.

    ``read`` is the rule's kinds a queued job makes and now writes to; ``unread``
    the rest. ``None`` when the switch is off, the class is not the lanes', the
    store is absent or untrustworthy, the rule is not in it, it says nothing in
    words, or no kind it covers is a lane's: the letter then says what it said
    before. Read through the same reader the lanes use, digest included.
    """
    if str(output_class or "").strip().lower() != RULE_CLASS or not rule_id:
        return None
    if not switched_on():
        return None
    root = spec_dir or os.environ.get("SMD_SPEC_DIR") or ""
    if not root:
        return None
    store = wording_rules_store.read(root)
    if store.status != wording_rules_store.OK:
        return None
    for rule in store.rules:
        if rule.get("rule_id") != rule_id or not rule.get("wording"):
            continue
        kinds = list(rule["doc_kinds"])
        read = [k for k in kinds if k in wording_rules_store.LANE_KINDS]
        if not read:
            return None
        unread = [k for k in kinds if k not in wording_rules_store.LANE_KINDS]
        return {"read": read, "unread": unread, "attorney": rule.get("attorney")}
    return None


__all__ = [
    "MAX_ATTORNEY_CHARS",
    "PROPERTIES",
    "RULE_CLASS",
    "SWITCH",
    "compile_adjustments",
    "lane_truth",
    "reach_fields",
    "reach_required",
    "render",
    "switched_on",
]
