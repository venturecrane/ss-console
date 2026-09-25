"""Plan the task review and build the casework envelope. Pure; no I/O.

Input: the parsed pull (``pull.Snapshot``), the authored ``case_manager`` block
(``casework_view.CaseManager``), the casework ledger fold, the records-chase
resolutions, and the parsed customer.yaml for routing. Output: the envelope the
overlay's ``casework_finish`` reads (contract agreed with the overlay build,
2026-09-25), plus the provenance records the pre_run hands off.

WHAT GOES WHERE (references/classification.md):

* An Operator-own task (``case_manager.own_tasks``) the record shows DONE, at
  level ``handles``: a queued close. The overlay writes ``closed_by_record``
  and replays the stored write; the message says "Closed just now".
* Any other Operator-own task: handed over ONCE, to the matter's assisting
  staff when there is one. At ``prepares``/``handles`` with someone to assign
  it to, a numbered ``proposed`` reassign; otherwise a ``named`` notice.
  Money or a court date on it only changes the wording, never adds a close.
* A firm task (``case_manager.task_cleanup``) that is done or stale: a
  proposed close. Open: a proposed keep (answering it quiets the task for
  ``keep_quiet_days``). At ``surfaces``: done/stale only, as named notices,
  and nothing is ever offered for writing. ``at_stake``: never listed; the
  escalator keeps it.

Nothing changes on a firm task without a person's answer, at any level: the
spec's "propose, then act" holds for ``handles`` too. Only the Operator's own
done tasks close without asking, because closing its own finished work is the
job the firm hired it for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

_MAX_MESSAGES = 10
_ORDER = {"done": 0, "stale": 1, "handover": 2, "open": 3}


@dataclass
class Entry:
    """One task's place in the review."""

    task: Any  # pull.TaskPull (path-loaded sibling, so typed loosely)
    verdict: Any  # classify.Verdict
    own: bool
    event: str  # proposed | named | close
    action: str  # close | keep | reassign
    staff_id: str | None
    to_staff_id: str | None
    recipients: tuple = ()
    cc: tuple = ()
    leg: str = ""
    handover: bool = False
    text: str = ""
    key: str = ""


@dataclass
class Plan:
    messages: list = field(default_factory=list)
    records: dict = field(default_factory=dict)  # matter number -> [days rendered beside it]
    matter_ids: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    memos: list = field(default_factory=list)


def _note_pair(records: dict, number: str | None, *days) -> None:
    if not number:
        return
    bucket = records.setdefault(number, [])
    for day in days:
        if isinstance(day, date) and day.isoformat() not in bucket:
            bucket.append(day.isoformat())


class _Ctx:
    """Everything the per-task decisions read, bundled so helpers stay small."""

    def __init__(
        self,
        *,
        snapshot,
        cm,
        ledger,
        states,
        chaser_resolved,
        customer_yaml,
        routing,
        label,
        classify,
        view,
        lines,
        today,
        window_days,
    ):
        self.snapshot, self.cm, self.ledger, self.states = snapshot, cm, ledger, states
        self.chaser_resolved, self.yaml, self.routing = chaser_resolved, customer_yaml, routing
        self.label, self.classify, self.view, self.lines = label, classify, view, lines
        self.today, self.window_days = today, window_days
        raw_scope = customer_yaml.get("scope")
        scope: dict = raw_scope if isinstance(raw_scope, dict) else {}
        grants = scope.get("inbound_allow_from")
        self.grants = [g for g in grants if isinstance(g, str)] if isinstance(grants, list) else []

    def granted(self, staff) -> str | None:
        if staff is None:
            return None
        email = self.routing._usable_staff_email(staff.as_routing_record())
        return email if email and self.routing._granted(email, self.grants) else None


def _facts(ctx: _Ctx, task):
    matter = ctx.snapshot.matters.get(task.matter_id)
    c = ctx.classify
    tfacts = c.TaskFacts(
        task_id=task.task_id,
        matter_id=task.matter_id,
        subject=task.subject,
        due=task.due,
        created=task.created,
        event_id=task.event_id,
        chaser_resolved=task.task_id in ctx.chaser_resolved,
    )
    if matter is None:
        # Unread matter: no calendar, no documents. Cautious by construction.
        return tfacts, c.MatterFacts(task.matter_id, calendar_read=False)
    files = tuple(c.FileRef(name, day) for name, day in matter.files)
    return tfacts, c.MatterFacts(
        task.matter_id, matter.status, files, matter.court_days, matter.court_event_ids, matter.calendar_read
    )


def _entry(ctx: _Ctx, task, verdict) -> Entry | None:
    cm, view = ctx.cm, ctx.view
    state = view.task_state(ctx.ledger, ctx.states, task.matter_id, task.task_id)
    if view.awaiting_write(state) or state is not None and state.named:
        return None
    if view.is_quiet(ctx.ledger, state, ctx.today, cm.keep_quiet_days):
        return None
    matter = ctx.snapshot.matters.get(task.matter_id)
    owner = matter.responsible.staff_id if matter and matter.responsible else None
    own = bool(cm.own_level) and view.is_operator_task(cm, task.task_id, task.subject)
    if own:
        if verdict.klass == "done" and cm.own_level == "handles" and owner:
            return Entry(task, verdict, True, "close", "close", owner, None)
        if verdict.klass in ("done", "stale") and cm.own_level != "surfaces" and owner:
            return Entry(task, verdict, True, "proposed", "close", owner, None)
        if state is not None and state.last_raise is not None:
            return None  # a handover is never repeated, proposed or named
        return Entry(task, verdict, True, "named", "keep", owner, None, handover=True)
    if not cm.cleanup_level or verdict.klass == "at_stake":
        return None
    if cm.cleanup_level == "surfaces" or not owner:
        if verdict.klass == "open":
            return None
        return Entry(task, verdict, False, "named", "keep", owner, None)
    action = "keep" if verdict.klass == "open" else "close"
    return Entry(task, verdict, False, "proposed", action, owner, None)


def _route(ctx: _Ctx, entries: list[Entry]) -> list[Entry]:
    """Recipients per entry: the matter's routed recipients (case-alert routing,
    the firm's authored posture), the assisting staff copied; a handover goes
    to the assisting staff member when one is granted."""
    matter_ids = sorted({e.task.matter_id for e in entries})
    staff = {}
    for mid in matter_ids:
        m = ctx.snapshot.matters.get(mid)
        if m is not None:
            staff[mid] = {
                "responsible": m.responsible.as_routing_record() if m.responsible else None,
                "assisting": [a.as_routing_record() for a in m.assisting],
            }
    result = ctx.routing.resolve_case_alert_routing(ctx.yaml, staff, matter_ids)
    routed_entries = []
    for e in entries:
        routed = result.routed.get(e.task.matter_id)
        m = ctx.snapshot.matters.get(e.task.matter_id)
        assistant = next(((a, em) for a in (m.assisting if m else ()) if (em := ctx.granted(a))), None)
        if e.handover and assistant is not None:
            e.recipients, e.leg = (assistant[1],), "matter_staff_assisting"
            if ctx.cm.own_level != "surfaces":
                e.event, e.action, e.to_staff_id = "proposed", "reassign", assistant[0].staff_id
        elif routed is not None:
            e.recipients, e.leg = tuple(routed.emails), routed.routing_leg
            if (
                e.handover
                and ctx.cm.own_level != "surfaces"
                and m
                and m.responsible
                and routed.routing_leg == ctx.routing.LEG_RESPONSIBLE
            ):
                e.event, e.action, e.to_staff_id = "proposed", "reassign", m.responsible.staff_id
            if routed.routing_leg == ctx.routing.LEG_RESPONSIBLE and assistant is not None:
                e.cc = tuple(x for x in (assistant[1],) if x not in e.recipients)
        else:
            continue  # unroutable: the fail-closed floor; counted, never sent
        routed_entries.append(e)
    return routed_entries


def _text(ctx: _Ctx, e: Entry) -> None:
    L, v, t = ctx.lines, e.verdict, e.task
    label = ctx.label(t.subject)
    evidence = L.evidence_text(
        v.evidence, "the records chase for it is resolved" if v.reason == "records_chase_resolved" else None
    )
    if e.event == "close":
        e.text = L.close_line(L.matter_head(t.matter_number, t.matter_number_absent), label, t.due, evidence)
    elif e.handover:
        suggest = L.SUGGEST["reassign_you"] if e.action == "reassign" else None
        e.text = L.handover_line(label, t.due, ctx.classify.cant_finish_reason(t.subject), suggest)
    else:
        why = ctx.classify.REASON_TEXT[v.reason].format(phrase=v.evidence_phrase, day=v.evidence_day)
        if e.own:
            why = "I opened this task. " + why
        if e.event == "named":
            suggest = "You can close it in Smokeball."
        else:
            suggest = L.SUGGEST[e.action].format(days=ctx.cm.keep_quiet_days)
        e.text = L.item_line(label, t.due, why, suggest)
    e.key = ctx.ledger.item_key(matter_id=t.matter_id, kind="task", source_id=t.task_id)


def _payload(e: Entry) -> dict:
    return {
        "action": e.action,
        "staff_id": e.staff_id,
        "to_staff_id": e.to_staff_id,
        "class": e.verdict.klass,
        "reason": e.verdict.reason,
        "evidence": list(e.verdict.evidence),
    }


def _done_since(ctx: _Ctx, recipients_by_matter: dict) -> tuple[dict, dict]:
    """Job 3: unmentioned record-closes, routed to the person who gets this
    run's message on that matter, and the per-matter memos they earn."""
    if not ctx.cm.quiet_level:
        return {}, {}
    by_recipient: dict = {}
    memos: dict = {}
    for state in ctx.view.unmentioned_closes(ctx.ledger, ctx.states):
        target = recipients_by_matter.get(state.matter_id)
        if target is None:
            continue  # not this run's reader; waits for a message on that matter
        number = target[1]
        atoms = (state.record_payload or {}).get("evidence") or []
        evidence = ctx.lines.evidence_text(atoms)
        line = ctx.lines.done_since_line(ctx.lines.matter_head(number, None), state.last_completed_date, evidence)
        days = [state.last_completed_date, ctx.lines.evidence_day(atoms)]
        by_recipient.setdefault(target[0], []).append(
            {
                "item_key": state.item_key,
                "matter_id": state.matter_id,
                "task_id": state.source_id,
                "line": line,
                "_number": number,
                "_days": days,
            }
        )
        if ctx.cm.quiet_level == "handles":
            memos.setdefault(state.matter_id, []).append(evidence)
    return by_recipient, memos


def build(ctx: _Ctx) -> Plan:
    """The whole review: entries, routing, text, then one message per recipient."""
    c = ctx.classify
    all_facts = [_facts(ctx, t) for t in ctx.snapshot.tasks]
    dupes = c.duplicate_ids([tf for tf, _ in all_facts])
    entries: list[Entry] = []
    for task, (tf, mf) in zip(ctx.snapshot.tasks, all_facts):
        verdict = c.classify(tf, mf, today=ctx.today, window_days=ctx.window_days, duplicates=dupes)
        entry = _entry(ctx, task, verdict)
        if entry is not None:
            entries.append(entry)
    routed = _route(ctx, entries)
    for e in routed:
        _text(ctx, e)
    plan = Plan(counts={"candidates": len(entries), "routed": len(routed), "unroutable": len(entries) - len(routed)})
    groups: dict = {}
    for e in routed:
        groups.setdefault((e.recipients, e.cc, e.leg), []).append(e)
    recipients_by_matter = {}
    for (rcpt, _cc, _leg), es in groups.items():
        for e in es:
            recipients_by_matter.setdefault(e.task.matter_id, ((rcpt, _cc, _leg), e.task.matter_number))
    done_since, memos = _done_since(ctx, recipients_by_matter)
    for group_key in sorted(groups, key=lambda k: (k[2], k[0]))[:_MAX_MESSAGES]:
        message = _message(ctx, group_key, groups[group_key], done_since.get(group_key, []), plan)
        if message is not None:
            plan.messages.append(message)
    plan.memos = [{"matter_id": mid, "text": ctx.lines.memo_text(evs)} for mid, evs in sorted(memos.items())]
    return plan


def _message(ctx: _Ctx, group_key, es: list[Entry], since: list, plan: Plan) -> dict | None:
    recipients, cc, leg = group_key
    closes = [e for e in es if e.event == "close"]
    listed = sorted(
        (e for e in es if e.event != "close"),
        key=lambda e: (
            _ORDER["handover" if e.handover else e.verdict.klass],
            e.task.matter_number or "",
            e.task.due,
            e.task.task_id,
        ),
    )
    shown, overflow = listed[: ctx.cm.max_lines], len(listed) - min(len(listed), ctx.cm.max_lines)
    # Numbered in the order a reader sees them: grouped by matter, first appearance.
    order: dict = {}
    for e in shown:
        order.setdefault(e.task.matter_id, []).append(e)
    items, n = [], 0
    for mid_entries in order.values():
        for e in mid_entries:
            n += 1
            t = e.task
            head = ctx.lines.matter_head(t.matter_number, t.matter_number_absent)
            items.append(
                {
                    "n": n,
                    "group": head,
                    "line": e.text,
                    "event": e.event,
                    "item_key": e.key,
                    "matter_id": t.matter_id,
                    "task_id": t.task_id,
                    "payload": _payload(e),
                }
            )
            _note_pair(plan.records, t.matter_number, t.due, e.verdict.evidence_day)
    for e in closes:
        _note_pair(plan.records, e.task.matter_number, e.task.due, e.verdict.evidence_day)
    for row in since:
        _note_pair(plan.records, row.get("_number"), *row.get("_days", ()))
    plan.matter_ids.extend(sorted({e.task.matter_id for e in es}))
    if not items and not closes:
        return None
    review = f"The next review is on {ctx.cm.review_day}." if ctx.cm.review_day and overflow else None
    return {
        "recipients": list(recipients),
        "cc": list(cc),
        "routing_leg": leg,
        "subject": ctx.lines.subject_line(len(items)),
        "lead": ctx.lines.lead_text(overflow, review, handover_only=all(e.handover for e in listed)),
        "closes": [
            {
                "item_key": e.key,
                "matter_id": e.task.matter_id,
                "task_id": e.task.task_id,
                "staff_id": e.staff_id,
                "line": e.text,
                "evidence": list(e.verdict.evidence),
                "reason": e.verdict.reason,
            }
            for e in closes
        ],
        "items": items,
        "done_since": [{k: v for k, v in row.items() if not k.startswith("_")} for row in since],
        "footer": ctx.lines.FOOTER,
    }
