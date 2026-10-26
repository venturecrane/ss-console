"""`validate`: a review record against the contract. Refuses, never repairs.

Ported from the file-review toolset's ``validate.py`` (strict mode),
``check_ids.py`` and ``check_medicals.py``, with the firm's words where the
toolset had them inline (``at_stake_terms``, ``statute_reminder_patterns``):

* the shape: every required key, of its type; the record's number is the
  matter's; a headline; a posture from the closed set with a sentence why;
* no em or en dash anywhere, no placeholder marker;
* every open task in the inventory has a disposition, every task id is the
  matter's (open ones in ``open_tasks``, completed ones in
  ``done_tasks_checked``), every action and check code is known;
* a statute reminder is ``leave_open``; a task naming money or a court date is
  never ``mark_complete``; a ``mark_complete`` names proof documents, each one
  READ in this run (text, not a scan, not unpulled);
* a NEW Medicals row carries a numeric charge and a read source document, and
  that charge appears in the source's text (1650.00, 1,650.00 or 1650): a
  figure that is nowhere in its source was read from bad text or inferred;
* every uuid the record cites is a document, task, contact or layout of THIS
  matter (a mistyped proof id once passed the shape checks).

A matter whose record fails here, after the reader's retries, is held alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .pack import SCAN_MARK, body_chars

DASHES = ("—", "–")
ACTIONS = {"mark_complete", "order_list", "draft_letter", "leave_open", "question"}
POSTURES = {
    "pre_demand",
    "demand_out",
    "offer_pending",
    "settled_open_items",
    "settled_disbursed",
    "litigation",
    "declined",
    "unknown",
}
CHECKS = {"confirmed", "confirmed_flag", "not_found", "not_confirmed"}
RECORDS = {"in", "not_in", "not_confirmed"}
LETTER_STATUS = {"draft", "needs_fact", "not_needed"}
PLACEHOLDER = re.compile(r"\bREVIEW\b")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REQUIRED = {
    "number": str,
    "clients": list,
    "headline": str,
    "facilities": list,
    "open_tasks": list,
    "done_tasks_checked": list,
    "records_to_order": list,
    "medicals": list,
    "insurance": dict,
    "injury_flags": list,
    "time_sensitive": list,
    "letters": list,
    "questions": list,
}


def walk_strings(obj: Any, path: str = "$"):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.<key {k}>", str(k)
            yield from walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")


def unreadable(meta: dict[str, Any], doc_id: Any) -> str | None:
    """None when the document's text was read in this run; else why not."""
    m = meta.get(str(doc_id).lower()) or meta.get(str(doc_id))
    if m is None:
        return "not a document on this matter"
    state = str(m.get("state") or "")
    if state.startswith("text "):
        return None
    if state.startswith("duplicate:"):
        return unreadable(meta, state.split(":", 1)[1])
    return state or "not read"


def charge_forms(c: float) -> set[str]:
    forms = {f"{c:,.2f}", f"{c:.2f}"}
    if c == int(c):
        forms |= {f"{int(c):,}", f"{int(c)}"}
    return forms


class _Checker:
    def __init__(self, number: str, inv: dict[str, Any], meta: dict[str, Any], firm: Any, text_of: Any) -> None:
        self.number, self.inv, self.meta, self.firm, self.text_of = number, inv, meta, firm, text_of
        self.v: list[str] = []
        self.open_ids = {str(t.get("id")) for t in inv.get("tasks_open") or []}
        self.done_ids = {str(t.get("id")) for t in inv.get("tasks_done") or []}

    def bad(self, rule: str, detail: str) -> None:
        self.v.append(f"{rule}: {detail}")

    def shape(self, rec: dict[str, Any]) -> None:
        for key, typ in REQUIRED.items():
            if key not in rec:
                self.bad("required", f"missing key {key!r}")
            elif not isinstance(rec[key], typ):
                self.bad("type", f"{key!r} is {type(rec[key]).__name__}, want {typ.__name__}")
        if isinstance(rec.get("number"), str) and rec["number"] != self.number:
            self.bad("number", f"record says {rec['number']!r}, the matter is {self.number!r}")
        if not str(rec.get("headline") or "").strip():
            self.bad("headline", "headline is empty")
        if rec.get("posture") not in POSTURES:
            self.bad("posture", f"posture {rec.get('posture')!r} not in {sorted(POSTURES)}")
        if not str(rec.get("posture_why") or "").strip():
            self.bad("posture", "posture_why is empty")
        for path, s in walk_strings(rec):
            if any(d in s for d in DASHES):
                self.bad("dash", f"em or en dash at {path}")
            if PLACEHOLDER.search(s):
                self.bad("placeholder", f"'REVIEW' marker at {path}")

    def open_task(self, i: int, t: Any, seen: set[str]) -> None:
        where = f"open_tasks[{i}]"
        if not isinstance(t, dict):
            self.bad("open_task", f"{where} is not an object")
            return
        tid, subj, action = str(t.get("task_id") or ""), str(t.get("subject") or ""), t.get("action")
        if not tid or tid not in self.open_ids:
            self.bad("task_id", f"{where} ({subj}) task_id {tid or None} is not an open task of this matter")
        seen.add(tid)
        for extra in t.get("task_ids") or []:
            seen.add(str(extra))
        if action not in ACTIONS:
            self.bad("action", f"{where} action {action!r} not in {sorted(ACTIONS)}")
        if not str(t.get("why") or "").strip():
            self.bad("why", f"{where} ({subj}) has an empty why")
        if any(p.fullmatch(subj.strip()) for p in self.firm.statute_rx) and action != "leave_open":
            self.bad("statute_reminder", f"{where} ({subj}) is a statute reminder and must be leave_open")
        if action == "mark_complete":
            self._closure(where, subj, t)

    def _closure(self, where: str, subj: str, t: dict[str, Any]) -> None:
        hit = self.firm.at_stake_rx.search(subj)
        if hit:
            self.bad("at_stake", f"{where} ({subj}) names {hit.group(1)!r} and cannot be mark_complete")
        proof = t.get("proof_doc_ids")
        if not isinstance(proof, list) or not proof:
            self.bad("proof", f"{where} ({subj}) is mark_complete with no proof_doc_ids")
            return
        for d in proof:
            why = unreadable(self.meta, d)
            if why:
                self.bad("proof_unread", f"{where} ({subj}) proof doc {d}: {why}")

    def done_task(self, i: int, t: Any) -> None:
        where = f"done_tasks_checked[{i}]"
        if not isinstance(t, dict):
            self.bad("done_task", f"{where} is not an object")
            return
        if str(t.get("task_id") or "") not in self.done_ids:
            self.bad("task_id", f"{where} ({t.get('subject')}) is not a completed task of this matter")
        if t.get("check") not in CHECKS:
            self.bad("check", f"{where} check {t.get('check')!r} not in {sorted(CHECKS)}")

    def medical(self, i: int, m: Any) -> None:
        where = f"medicals[{i}]"
        if not isinstance(m, dict) or not str(m.get("provider") or "").strip():
            self.bad("medicals", f"{where} has no provider")
            return
        if m.get("existing_row"):
            return
        charge, src = m.get("charge"), m.get("source_doc_id")
        if isinstance(charge, bool) or not isinstance(charge, (int, float)):
            self.bad("medicals", f"{where} ({m.get('provider')}) charge {charge!r} is not a number")
            return
        why = unreadable(self.meta, src) if src else "no source_doc_id"
        if why:
            self.bad("medicals_source", f"{where} ({m.get('provider')}) source doc {src}: {why}")
            return
        text = self.text_of(str(src)) or ""
        if not any(f in text for f in charge_forms(float(charge))):
            self.bad("medicals_charge", f"{where} ({m.get('provider')}) charge {charge} is not in its source's text")

    def rest(self, rec: dict[str, Any]) -> None:
        for i, f in enumerate(rec.get("facilities") or []):
            if not isinstance(f, dict) or f.get("records") not in RECORDS:
                self.bad("facility", f"facilities[{i}] records not in {sorted(RECORDS)}")
        for i, ltr in enumerate(rec.get("letters") or []):
            st = ltr.get("status") if isinstance(ltr, dict) else None
            if st not in LETTER_STATUS:
                self.bad("letter", f"letters[{i}] status {st!r} not in {sorted(LETTER_STATUS)}")
            elif st == "needs_fact" and not (isinstance(ltr.get("missing"), list) and ltr["missing"]):
                self.bad("letter", f"letters[{i}] is needs_fact with no missing fact named")

    def ids(self, rec: dict[str, Any]) -> None:
        known = {k.lower() for k in self.meta} | {u.lower() for u in UUID.findall(json.dumps(self.inv, default=str))}
        unknown = sorted({u.lower() for u in UUID.findall(json.dumps(rec))} - known)
        if unknown:
            self.bad("unknown_id", f"{len(unknown)} id(s) are not on this matter: " + ", ".join(u[:8] for u in unknown))


def _rows(rec: dict[str, Any], key: str) -> list[Any]:
    got = rec.get(key)
    return got if isinstance(got, list) else []


def check(
    rec: Any, number: str, inv: dict[str, Any], meta: dict[str, Any], firm: Any, text_of: Callable[[str], Any]
) -> list[str]:
    """Every violation as ``rule: detail``; empty means accepted."""
    if not isinstance(rec, dict):
        return ["shape: the record is not an object"]
    c = _Checker(number, inv, meta, firm, text_of)
    c.shape(rec)
    seen: set[str] = set()
    for i, t in enumerate(_rows(rec, "open_tasks")):
        c.open_task(i, t, seen)
    for i, t in enumerate(_rows(rec, "done_tasks_checked")):
        c.done_task(i, t)
    for i, m in enumerate(_rows(rec, "medicals")):
        c.medical(i, m)
    c.rest(rec)
    c.ids(rec)
    for tid in sorted(c.open_ids - seen):
        subj = next((t.get("subject") for t in inv.get("tasks_open") or [] if str(t.get("id")) == tid), "?")
        c.bad("undispositioned", f"open task {tid} ({subj}) has no open_tasks row")
    return c.v


def scanned_bill(meta: dict[str, Any], text_of: Callable[[str], Any], doc_id: str) -> bool:
    """A source the run could not read for its figures: a scan with no text layer."""
    text = text_of(doc_id) or ""
    return SCAN_MARK in text and body_chars(text) < 200


__all__ = ["ACTIONS", "POSTURES", "charge_forms", "check", "scanned_bill", "unreadable"]
