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


# ---- what the revision will cost, from its own section plan ---------------------------
#: A model's output is priced at about five times its input; the projection
#: counts output characters at that weight on the firm's read rate.
OUTPUT_WEIGHT = 5
#: A reading's answer: a verdict and one sentence per item, about this long.
READ_ANSWER_CHARS = 600


def plan_reads(base_md: str, request_text: str, items: list[Item]) -> dict[str, int]:
    """The model calls this checklist makes and the characters they read: the
    checklist's two reads, then for every section a model edits (a
    ``document`` target is every headed section) one edit and one reading,
    each twice (the one re-edit round). Pure: code's own edits are made here
    at $0 to know which sections are left for a model."""
    prior = sec.split(base_md)
    current, _did, done = edit.code_edits(prior, items)
    todo = edit.by_section(current, items, done)
    by_id = {s.id: s for s in current}
    titles = sum(len(s.title) + 3 for s in prior if s.heading)
    listed = len(json.dumps([i.to_dict() for i in items], ensure_ascii=False))
    chars = 2 * (len(ck.SYSTEM) + len(request_text) + titles + OUTPUT_WEIGHT * listed)
    calls = 2
    for sid, its in todo.items():
        asks = len(json.dumps([i.to_dict() for i in its], ensure_ascii=False))
        text = len(by_id[sid].text)
        edit_call = len(edit.SYSTEM) + text + asks + OUTPUT_WEIGHT * text
        read_call = len(reading.SYSTEM) + 2 * text + asks + OUTPUT_WEIGHT * READ_ANSWER_CHARS
        chars += 2 * (edit_call + read_call)
        calls += 4
    return {"calls": calls, "sections": len(todo), "characters": chars}


# ---- the attorney's markers, on revise ---------------------------------------------
def reserve(md: str, base_md: str, cls: str) -> str:
    """``render.reserve_judgment`` for a revision, never in a section the
    attorney already filled (its filed text carries no ``{{ATTORNEY`` marker).
    A section with no filed counterpart (a new sub-heading) follows the filed
    section it sits under."""
    from .. import render

    base = sec.split(base_md)
    filled = {sec.norm_title(s.title): "{{ATTORNEY" not in s.text for s in base if s.heading}
    as_filed = {s.text for s in base}
    out, parent_filled = [], False
    for s in sec.split(md):
        key = sec.norm_title(s.title)
        if s.heading and key in filled:
            parent_filled = filled[key]
        done = filled.get(key, parent_filled) if s.heading else False
        # A section the revision did not change is the filed text, as filed.
        out.append(s.text if done or s.text in as_filed else render.reserve_judgment(s.text, cls)[0])
    return "".join(out)
