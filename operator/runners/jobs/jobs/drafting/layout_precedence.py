"""One order of precedence for a document's layout (design 2026-11-06, piece 5;
"What a firm experiences" paragraph 7).

THE ORDER, per layout property:

1. ``court_rule``: what a court rule requires comes first. On pleading paper the
   paper's line pitch holds every line (rule 2.108; the pitch keys), and a page
   margin under rule 2.107's floors is never used (``layout_props.COURT``);
2. between a rule the firm said (``firm_rule``: the rule id and when it was
   said) and a change the firm made in its own library Word file
   (``firm_edit``: the version and when code accepted it), the LATER wins,
   because both are the firm's own word;
3. ``authored``: the layout written down in the firm's drafting inputs, which
   either of the firm's later words beats.

NEVER SILENT. When a later firm edit changes a property a rule set, the rule is
``replaced`` by that edit, with the version and the date, in the record beside
the document. Whether the edit changed it is told in one of two ways only: the
job that first met the save holds the copy it replaces and reads both; a later
job finds the rule's own version in the ledger (``layout_rule:<id>``, written
by ``layout_into_library``) before the firm's. When neither can tell, the
ledger cannot place the edit against the rule: the rule STANDS, the record
says ``unplaced``, and the daemon pages SMD (``layout_into_library.UNPLACED``).

WHAT IT CHANGES. Only a document made in an accepted library file whose
current version is the firm's own edit can have a ``firm_edit`` winner, so
with nothing accepted (every seat today) this is never reached and the record
carries no ``layout_precedence`` key. :func:`apply` is the one place the
winners act: a property won by the firm's edit leaves the authored layout the
format check reads (and the rule layout laid onto the document), and a
firm-edit margin on pleading paper is the paper's margin for the render and
the check alike. No model call.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from . import layout_props as P, rule_times

COURT_RULE = "court_rule"
FIRM_RULE = "firm_rule"
FIRM_EDIT = "firm_edit"
AUTHORED = "authored"
WINNERS = (COURT_RULE, FIRM_RULE, FIRM_EDIT, AUTHORED)
#: The ``accepted_by`` of a version code wrote with layout rules in it.
LAYOUT_RULE = "layout_rule:"
#: The record's key (``library-resolution.json``), present only for an accepted file.
RECORD_KEY = "layout_precedence"
FIRM_MADE = "firm_made"


@dataclass(frozen=True)
class Facts:
    """Everything the order is decided from, read before it is decided."""

    cls: str
    paper: str
    #: key -> (rule id, value): the rules this job lays, later winning.
    rules: dict[str, tuple[str, Any]]
    #: rule id -> when it was said; None when the installed spec cannot be read.
    rule_at: dict[str, str] | None
    #: the firm's written-down layout for this class (``format.layout.<class>``).
    authored: dict[str, Any]
    #: the firm's pleading paper's margins, on pleading paper only.
    authored_margins: tuple[float, ...] | None
    accepted_by: str
    accepted_at: str
    version: str
    #: what the file says now (``layout_props.read``).
    after: dict[str, Any]
    #: what the copy the firm's save replaces said; only for the job that met the save.
    before: dict[str, Any] | None = None
    #: (accepted_by, accepted_at) of the slot's earlier versions, from the ledger.
    chain: tuple[tuple[str, str], ...] = ()


@dataclass
class Precedence:
    winners: dict[str, dict[str, Any]] = field(default_factory=dict)
    replaced: list[dict[str, Any]] = field(default_factory=list)
    unplaced: list[dict[str, Any]] = field(default_factory=list)
    into_library: dict[str, Any] | None = None

    def winner(self, prop: str) -> str:
        return str((self.winners.get(prop) or {}).get("winner") or "")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "winners": {k: dict(v) for k, v in sorted(self.winners.items())},
            "replaced": [dict(r) for r in self.replaced],
            "unplaced": [dict(u) for u in self.unplaced],
        }
        if self.into_library is not None:
            out["into_library"] = dict(self.into_library)
        return out


def _jsonable(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _edit_won(f: Facts, prop: str) -> dict[str, Any]:
    return {"winner": FIRM_EDIT, "version": f.version, "at": f.accepted_at, "value": _jsonable(f.after.get(prop))}


def _rule_placed_before(f: Facts, rule_id: str) -> bool:
    """Whether the ledger holds a version code wrote with this rule in it,
    earlier than the firm's current version."""
    now = rule_times.when(f.accepted_at)
    for who, at in f.chain:
        ids = who[len(LAYOUT_RULE) :].split(",") if who.startswith(LAYOUT_RULE) else []
        then = rule_times.when(at)
        if rule_id in ids and then is not None and now is not None and then < now:
            return True
    return False


def _rule(f: Facts, key: str, rule_id: str, value: Any, out: Precedence) -> None:
    at = (f.rule_at or {}).get(rule_id, "")
    won = {"winner": FIRM_RULE, "rule_id": rule_id, "at": at, "value": _jsonable(value)}
    won["in_file"] = None if f.after.get(key) is None else P.same(key, f.after.get(key), value)
    if f.paper == "pleading" and key in P.PITCH_KEYS:
        out.winners[key] = {**won, "winner": COURT_RULE, "court": P.COURT["pitch"]}
        return
    out.winners[key] = won
    if f.accepted_by != FIRM_MADE or P.same(key, f.after.get(key), value):
        return
    said, saved = rule_times.when(at), rule_times.when(f.accepted_at)
    if said is None:
        out.unplaced.append({"rule_id": rule_id, "key": key, "why": "the rule's date cannot be read"})
        return
    if saved is None or saved <= said:
        return  # the rule is the later word
    if f.before is not None:
        changed = not P.same(key, f.before.get(key), f.after.get(key))
    elif _rule_placed_before(f, rule_id):
        changed = True
    else:
        out.unplaced.append(
            {"rule_id": rule_id, "key": key, "why": "the ledger cannot say whether the firm's edit changed it"}
        )
        return
    if changed:
        out.winners[key] = {**_edit_won(f, key), "replaces_rule": rule_id}
        out.replaced.append({"rule_id": rule_id, "key": key, "by_version": f.version, "at": f.accepted_at})


def _margins(f: Facts, out: Precedence) -> None:
    if f.paper != "pleading" or f.authored_margins is None:
        return
    now = f.after.get(P.MARGINS)
    if f.accepted_by != FIRM_MADE or P.same(P.MARGINS, now, f.authored_margins):
        out.winners[P.MARGINS] = {"winner": AUTHORED, "value": list(f.authored_margins)}
    elif P.within_court(now):
        out.winners[P.MARGINS] = _edit_won(f, P.MARGINS)
    else:
        out.winners[P.MARGINS] = {
            "winner": COURT_RULE,
            "court": P.COURT[P.MARGINS],
            "value": list(f.authored_margins),
            "refused_edit": f.version,
        }


def _authored(f: Facts, key: str, out: Precedence) -> None:
    value = f.authored.get(key)
    if value is None:
        return
    if f.paper == "pleading" and key in P.PITCH_KEYS:
        out.winners[key] = {"winner": COURT_RULE, "court": P.COURT["pitch"], "value": value}
    elif f.accepted_by == FIRM_MADE and not P.same(key, f.after.get(key), value):
        out.winners[key] = _edit_won(f, key)
    else:
        out.winners[key] = {"winner": AUTHORED, "value": value}


def decide(f: Facts) -> Precedence:
    """The winner of each property the file, the rules or the authored layout speaks for."""
    out = Precedence()
    _margins(f, out)
    for key, (rule_id, value) in sorted(f.rules.items()):
        _rule(f, key, rule_id, value, out)
    for key in P.READ_KEYS:
        if key not in f.rules:
            _authored(f, key, out)
    return out


def apply(
    fmt: dict[str, Any], cls: str, rules: dict[str, Any], prec: Precedence | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(fmt, rules)`` this document is laid and checked by. With no precedence,
    the very objects given. A property the firm's edit won leaves the authored
    layout and the rule layout; a firm-edit margin is the paper's margin."""
    if prec is None:
        return fmt, rules
    edited = {k for k, w in prec.winners.items() if w.get("winner") == FIRM_EDIT}
    if not edited:
        return fmt, rules
    out = copy.deepcopy(fmt)
    lay = (out.get("layout") or {}).get(cls)
    for key in edited - {P.MARGINS}:
        if isinstance(lay, dict):
            lay.pop(key, None)
    if P.MARGINS in edited and isinstance(out.get("pleading"), dict):
        out["pleading"]["margins_in"] = list(prec.winners[P.MARGINS]["value"])
    return out, {k: v for k, v in (rules or {}).items() if k not in edited}


__all__ = [
    "AUTHORED",
    "COURT_RULE",
    "FIRM_EDIT",
    "FIRM_RULE",
    "LAYOUT_RULE",
    "RECORD_KEY",
    "WINNERS",
    "Facts",
    "Precedence",
    "apply",
    "decide",
]
