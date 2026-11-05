"""The revision without the seat: request -> checklist -> edits -> checks.

Pure over its inputs and the three model doorways it is handed, so the job
(``run.py``) and the $0 rehearsal (``rehearse.py``) run the same code: the
rehearsal hands an answer key's checklist and the hand-made revision instead
of model calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import checklist as ck, checks, edit, reading, sections as sec
from .checklist import Item
from .checks import Status
from .edit import Ask


@dataclass
class Outcome:
    items: list[Item]
    edited: list[sec.Section]
    statuses: list[Status]
    findings: list[tuple[str, str]]  # (section id, finding), invariants and new assertions
    did: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rounds: int = 1

    @property
    def md(self) -> str:
        return sec.join(self.edited)

    def blocking(self) -> list[str]:
        """What stops the filing: every finding, and every item code could
        check and found not done. An ``other`` item the reading found not done
        (it needs what the brief does not carry) is the attorney's to see,
        never a stop."""
        out = [f for _sid, f in self.findings]
        for st, it in zip(self.statuses, self.items):
            if st.status == "not_met" and not (it.kind == "other" and st.how == "reading"):
                out.append(f"{st.id} ({it.kind}): {st.detail}")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "statuses": [s.to_dict() for s in self.statuses],
            "findings": [{"section": s, "finding": f} for s, f in self.findings],
            "did": self.did,
            "notes": self.notes,
            "rounds": self.rounds,
            "blocking": self.blocking(),
        }


def allowed_text(request_text: str, items: list[Item]) -> str:
    """The attorney's own words: what an edit may bring in that the brief lacks."""
    return request_text + "\n" + "\n".join(json.dumps(i.params, ensure_ascii=False) for i in items)


def make_checklist(request_text: str, prior: list[sec.Section], ask: Ask) -> list[Item]:
    problems: list[str] | None = None
    for _ in range(2):
        raw = ck.parse(ask("revise-checklist", ck.SYSTEM, ck.user(request_text, prior, problems), 8_000))
        try:
            return ck.validate(raw, request_text, prior)
        except ck.ChecklistError as exc:
            problems = exc.problems
    raise ck.ChecklistError(problems or ["no checklist"])


def evaluate(
    prior: list[sec.Section],
    edited: list[sec.Section],
    items: list[Item],
    request_text: str,
    read: Ask | None = None,
) -> tuple[list[Status], list[tuple[str, str]], list[str]]:
    """Code's checks, then (when ``read`` is given) the narrow reading."""
    statuses = [checks.check_item(i, prior, edited) for i in items]
    findings = checks.invariants(prior, edited, items, allowed_text(request_text, items))
    notes: list[str] = []
    if read is not None:
        per = _items_by_prior_section(prior, items)
        verdicts, asserted = reading.read(prior, edited, per, edit.changed(prior, edited), read)
        statuses, notes = reading.settle(statuses, verdicts)
        findings += [(sid, f) for sid, fs in asserted.items() for f in fs]
    return statuses, findings, notes


def _items_by_prior_section(prior: list[sec.Section], items: list[Item]) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {}
    for it in items:
        if it.kind == "formatting":
            continue
        for s in sec.find(prior, it.target):
            out.setdefault(s.id, []).append(it)
    return out


def revise(
    base_md: str,
    request_text: str,
    *,
    checklist_ask: Ask,
    edit_ask: Ask,
    read_ask: Ask | None,
    items: list[Item] | None = None,
) -> Outcome:
    """One revision: at most one re-edit of the sections a check refused."""
    prior = sec.split(base_md)
    items = items if items is not None else make_checklist(request_text, prior, checklist_ask)
    current, did, done = edit.code_edits(prior, items)
    todo = edit.by_section(current, items, done)
    edited, said = edit.model_edits(current, todo, edit_ask)
    statuses, findings, notes = evaluate(prior, edited, items, request_text, read_ask)
    out = Outcome(items, edited, statuses, findings, did + said, notes)
    if not out.blocking():
        return out
    retry = _retry_findings(out, todo)
    if not retry:
        return out
    edited, said = edit.model_edits(edited, {k: v for k, v in todo.items() if k in retry}, edit_ask, retry)
    statuses, findings, notes = evaluate(prior, edited, items, request_text, read_ask)
    return Outcome(items, edited, statuses, findings, out.did + said, notes, rounds=2)


def _retry_findings(out: Outcome, todo: dict[str, list[Item]]) -> dict[str, list[str]]:
    """Section id -> what code refused there, for the sections a model edited."""
    retry: dict[str, list[str]] = {}
    for sid, f in out.findings:
        if sid in todo:
            retry.setdefault(sid, []).append(f)
    by_item = {i.id: i for i in out.items}
    for st in out.statuses:
        if st.status != "not_met":
            continue
        for sid, its in todo.items():
            if any(i.id == st.id for i in its):
                retry.setdefault(sid, []).append(f"{st.id} ({by_item[st.id].kind}) is not done: {st.detail}")
    return retry
