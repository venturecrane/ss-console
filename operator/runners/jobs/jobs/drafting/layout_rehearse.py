"""A $0 rehearsal of the order of layout precedence (design 2026-11-06, piece 5)
on a seat's installed layout rules and an accepted library file: READ ONLY.

Given the seat's compiled layout rules file (as the spec applier installed it), the slot's
accepted file (the seat-kept copy) and the firm's drafting inputs, it lays the
rules the way a job lays them (``firm_rules.for_class``), decides each layout
property's winner (``layout_precedence.decide``), and asks the real format check
whether each holds on a fictional brief laid into the file
(``firm_rules.probe_new_fails``, the probe a job runs before any paid draft).
With ``edited``, a firm's later save of the file: it is taken as the firm's own
edit, later than every installed rule, met by the job that holds the copy it
replaces, which is the case the order is built for.

It prints counts, rule ids, key names and winners only; never a word of any
document, a name or a file name. Nothing is written anywhere. No model call.
"""

from __future__ import annotations

import copy
import json
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import firm as firm_mod, firm_rules, layout_into_library, layout_precedence as LP, layout_props as P, pleading

#: A rule's date is not in the compiled rules file; the rehearsal's firm save is later than all of them.
_LONG_AGO = "1970-01-01T00:00:00Z"


def _rules(layout_json: bytes) -> list[dict[str, Any]]:
    doc = json.loads(layout_json.decode("utf-8"))
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        raise ValueError("the compiled layout rules file has no rule list")
    return [r for r in rules if isinstance(r, dict) and isinstance(r.get("compiled"), list)]


def _check(where: Path, kind: str, fmt: dict[str, Any], paper: str, blob: bytes, values: dict[str, Any]) -> bool | None:
    """Whether ``values`` add no format-check finding on this file; None when
    the probe could not be laid in it at all."""
    path = where / f"base-{len(list(where.iterdir()))}.docx"
    path.write_bytes(blob)
    source = {"matter_id": "", "file_id": "rehearsal", "name": "the library file", "sha256": "", "version": ""}
    try:
        return not firm_rules.probe_new_fails(where, kind, fmt, paper, (path, source), values)
    except Exception:  # noqa: BLE001 - a probe that cannot be laid is reported as such, never as a pass
        return None


def _margins_hold(where: Path, kind: str, fmt: dict[str, Any], paper: str, blob: bytes) -> bool | None:
    from . import base_lay, format_check

    path = where / "margins.docx"
    path.write_bytes(blob)
    source = {"matter_id": "", "file_id": "rehearsal", "name": "the library file", "sha256": "", "version": ""}
    try:
        laid, _r, _l, checked = base_lay.lay_out(
            firm_rules.PROBE_MD, where / "m.docx", kind, fmt, paper, (path, source)
        )
        fails = format_check.check(laid, kind, checked, "", None, paper).fails
    except Exception:  # noqa: BLE001 - a probe that cannot be laid is reported as such, never as a pass
        return None
    return not any("margins" in f for f in fails)


def rehearse(
    kind: str, layout_json: bytes, accepted: bytes, inputs: str | None, edited: bytes | None = None
) -> dict[str, Any]:
    """Per property: its winner and whether the format check holds it. Counts only."""
    fmt = copy.deepcopy(firm_mod.load(inputs).data["format"])
    paper = pleading.paper_for(fmt, kind, "")
    rules = _rules(layout_json)
    layout, rows, _n = firm_rules.for_class(rules, kind, True, paper=paper, pitch=firm_rules._pitch(fmt), pins={})
    fmt.setdefault("layout", {}).setdefault(kind, {}).update(layout)
    applied = {str(r["key"]): (str(r["rule_id"]), r["value"]) for r in rows if r.get("applied") and "value" in r}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    file = edited if edited is not None else accepted
    margins = (fmt.get("pleading") or {}).get("margins_in") if paper == "pleading" else None
    facts = LP.Facts(
        cls=kind,
        paper=paper,
        rules=applied,
        rule_at={str(r.get("rule_id")): _LONG_AGO for r in rules},
        authored=dict(fmt["layout"][kind]),
        authored_margins=tuple(float(x) for x in margins) if margins else None,
        accepted_by="firm_made" if edited is not None else "admin",
        accepted_at=now,
        version="rehearsal",
        after=P.read(file, "marked", kind, paper),
        before=P.read(accepted, "marked", kind, paper) if edited is not None else None,
    )
    prec = LP.decide(facts)
    checked_fmt, laid = LP.apply(fmt, kind, layout, prec)
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        where = Path(tmp)
        for prop, won in sorted(prec.winners.items()):
            if prop == P.MARGINS:
                holds = _margins_hold(where, kind, checked_fmt, paper, file)
            elif prop in laid:
                holds = _check(where, kind, checked_fmt, paper, file, {prop: laid[prop]})
            else:
                holds = None
            out[prop] = {
                "winner": won["winner"],
                "format_check": {True: "pass", False: "fail", None: "not_checked"}[holds],
            }
        all_rules = _check(where, kind, checked_fmt, paper, file, laid) if laid else None
    return {
        "layout": {
            "paper": paper,
            "rules": len(rules),
            "rule_values_laid": len(applied),
            "properties": out,
            "by_winner": dict(Counter(v["winner"] for v in out.values())),
            "replaced": [f"{r['rule_id']}:{r['key']}" for r in prec.replaced],
            "unplaced": [f"{u['rule_id']}:{u['key']}" for u in prec.unplaced],
            "would_apply_into_file": [f"{w['rule_id']}:{w['key']}" for w in layout_into_library.would_apply(prec)],
            "all_rules_together": {True: "pass", False: "fail", None: "not_checked"}[all_rules],
        }
    }


__all__ = ["rehearse"]
