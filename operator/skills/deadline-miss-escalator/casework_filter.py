"""The escalator's half of the case-manager work: stop alarming on what the
case manager already owns (docs/specs/operator/case-manager-deadline-work.md).

Runs in ``pre_run.run_once`` after the escalation-ledger join and before the
wake decision, so a deadline the task-list-keeper or the date-prep brief holds
neither wakes the ladder nor fills the digest. EVERYTHING HERE IS GATED ON AN
AUTHORED ``case_manager:`` BLOCK. Without one, ``apply`` returns its input
untouched and the run is byte-identical to the escalator before this file
existed (``test_case_manager_golden.py`` pins that).

With the block:

* An Operator-own task (``own_tasks`` authored) leaves the digest: closing or
  handing it over is the task-list-keeper's job, once, never a recurring alarm.
* A task the keeper holds leaves the digest: a proposal is out, a person said
  leave it, a write is on its way, or it was handed over (``casework_view``).
* A court date with a date-prep brief leaves the digest, EXCEPT the backstop:
  while any of the brief's decisions is unanswered, the date fires again once
  it is inside ``notify_days``. A brief nobody answered must not silence a
  court date.
* When ``task_cleanup`` is authored, the digest carries a ``task_review``
  marker, and each recipient's overdue tasks past the top five collapse into
  one line naming the review (``digest_items.extract_task_review``).

Nothing here is written anywhere: the filter reads the casework ledger (the
agent-readable JSONL the broker writes) and the trusted customer.yaml.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from datetime import date

SKILL = "deadline-miss-escalator"


@dataclass
class Filtered:
    deadlines: list
    task_review: dict | None = None
    stats: dict = field(default_factory=dict)

    def annotate(self, decision):
        """The wake decision with the review marker on its digest (read by
        dispatch_envelope.split_digest) and the drop counts on its metadata.
        Unconfigured seats have neither, and the decision comes back as is."""
        if self.task_review is not None and decision.digest is not None:
            decision.digest["task_review"] = self.task_review
        if self.stats:
            return replace(decision, extra_metadata={**decision.extra_metadata, **self.stats})
        return decision


def _modules(helpers, anchor: str):
    view = helpers.load_sibling(SKILL, anchor, "casework_view.py", "escalator_casework_view")
    ledger = helpers.load_sibling(SKILL, anchor, "casework_ledger.py", "escalator_casework_ledger")
    return view, ledger


def _drop_reason(d, *, cm, view, ledger, states, today: date, notify_days: int) -> str | None:
    if d.label == "task-deadline":
        stamp = "[Operator]" if getattr(d, "operator_stamped", False) else ""
        if cm.own_level and d.task_id and view.is_operator_task(cm, d.task_id, stamp):
            return "own_task"
        state = view.task_state(ledger, states, d.matter_id, d.task_id)
        if view.keeper_owns_task(ledger, state, today, cm.keep_quiet_days):
            return "in_task_review"
        return None
    if d.label == "court-date":
        status = view.brief_status(view.date_state(ledger, states, d.matter_id, d.task_id))
        if status == "answered":
            return "briefed"
        if status == "unanswered" and (d.authored_date - today).days > notify_days:
            return "briefed_awaiting_answer"
    return None


def apply(
    deadlines, *, helpers, anchor: str, today: date, notify_days: int, customer_yaml=None, events=None
) -> Filtered:
    """The deadlines left for the escalator, the review marker, and the drop
    counts for the EMITTED_WAKE row. Unauthored block or an unloadable module:
    the input, untouched (a filter that cannot run must not silence a date)."""
    doc = customer_yaml if customer_yaml is not None else helpers.load_customer_yaml(None)
    if not isinstance(doc, dict) or not isinstance(doc.get("case_manager"), dict) or not doc["case_manager"]:
        return Filtered(list(deadlines))
    view, ledger = _modules(helpers, anchor)
    if view is None or ledger is None:
        sys.stderr.write("[pre_run] casework modules missing; deadlines unfiltered\n")
        return Filtered(list(deadlines))
    cm = view.load_case_manager(doc)
    if cm is None:
        return Filtered(list(deadlines))
    states = ledger.derive_state(events if events is not None else ledger.read_ledger())
    kept, stats = [], {}
    for d in deadlines:
        reason = _drop_reason(d, cm=cm, view=view, ledger=ledger, states=states, today=today, notify_days=notify_days)
        if reason is None:
            kept.append(d)
        else:
            stats[reason] = stats.get(reason, 0) + 1
    review = {"day": cm.review_day} if cm.cleanup_level else None
    return Filtered(kept, review, {"casework_dropped": stats} if stats else {})
