"""The narrow reading: what code cannot decide, read section by section by a
model that did not make the edit.

Only CHANGED sections are read, each as filed beside as revised, with the
items for it. The model answers, per item, whether the revision does what the
attorney asked, and lists any sentence in the revision that says something the
filed section did not (a new assertion). Code then acts:

* an ``other`` item takes the reading's verdict (``met`` / ``not_met``);
* a code-checked item keeps code's verdict; a reading that disagrees is
  carried to the attorney notes, never over code;
* a new assertion is a finding against the section, like a new figure.
"""

from __future__ import annotations

import json
from typing import Any

from ..verify.items import parse_json
from . import sections as sec
from .checklist import Item
from .checks import Status
from .edit import Ask

MAX_TOKENS = 4_000
SYSTEM = (
    "You check one revised section of a legal brief against the attorney's requested changes. You did not write "
    'the revision. Compare the section AS FILED with the section AS REVISED. Return ONLY JSON: {"items": '
    '[{"id": ..., "met": true|false, "why": <one sentence>}], "new_assertions": [{"sentence": <copied from '
    'the revision>, "why": <what it says that the filed section did not>}]}. A sentence is a new assertion only '
    "when it states a fact, figure, date, name, quotation or legal proposition the filed section does not support; "
    "shortening, merging, reordering and removing are not new assertions."
)


def user(before: sec.Section, after: sec.Section, items: list[Item]) -> str:
    asks = [{"id": i.id, "attorney_said": i.quote, "kind": i.kind, "params": i.params} for i in items]
    return (
        "THE CHANGES ASKED FOR:\n"
        + json.dumps(asks, indent=1, ensure_ascii=False)
        + "\n\nAS FILED:\n"
        + before.text
        + "\n\nAS REVISED:\n"
        + after.text
    )


def read(
    prior: list[sec.Section],
    edited: list[sec.Section],
    items_by_section: dict[str, list[Item]],
    changed_ids: list[str],
    ask: Ask,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """({item id: {met, why}}, {section id: [new-assertion findings]})."""
    verdicts: dict[str, dict[str, Any]] = {}
    findings: dict[str, list[str]] = {}
    before = {s.id: s for s in prior}
    for s in edited:
        if s.id not in changed_ids or s.id not in before:
            continue
        its = items_by_section.get(s.id) or []
        got = parse_json(ask("revise-read", SYSTEM, user(before[s.id], s, its), MAX_TOKENS))
        if not isinstance(got, dict):
            findings.setdefault(s.id, []).append("the reading of this section did not answer; it is unchecked")
            continue
        for v in got.get("items") or []:
            if isinstance(v, dict) and isinstance(v.get("met"), bool):
                verdicts[str(v.get("id"))] = {"met": v["met"], "why": str(v.get("why") or "")[:300]}
        for a in got.get("new_assertions") or []:
            if isinstance(a, dict) and str(a.get("sentence") or "").strip():
                findings.setdefault(s.id, []).append(
                    f"says what the filed brief did not: {str(a['sentence'])[:160]!r} ({str(a.get('why') or '')[:120]})"
                )
    return verdicts, findings


def settle(statuses: list[Status], verdicts: dict[str, dict[str, Any]]) -> tuple[list[Status], list[str]]:
    """Statuses after the reading, and the disagreements for the notes."""
    out, notes = [], []
    for st in statuses:
        v = verdicts.get(st.id)
        if st.status == "read" and st.how == "reading":
            if v is None:
                out.append(Status(st.id, "read", "not checked", "neither code nor the reading could decide it"))
            else:
                out.append(Status(st.id, "met" if v["met"] else "not_met", "reading", v["why"] or "read"))
            continue
        if v is not None and st.status == "met" and not v["met"]:
            notes.append(f"{st.id}: code found it done; the reading doubted it: {v['why']}")
        out.append(st)
    return out, notes
