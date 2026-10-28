"""The Case File Manager's half of the casework ledger: its numbered lines
raised on the thread its bound reply went out on, the lines a reply there may
apply, and their close once an apply job delivered.

EVERY ROW IS WITNESSED, never assumed (``casework_ledger.validate_append``):

* a ``proposed`` raise is witnessed by the broker's own bound-reply record
  (``bound_replies``): the delivered job's one reply was claimed and settled
  ``sent`` under its key, in this broker, before any line is raised;
* a ``completed`` close carries ``tool_call_id`` ``casefile:<apply job>:<n>``
  and is witnessed by the broker's case file ledger: that apply job is
  ``delivered`` and its recorded outcome for line ``n`` is a write the runner
  read back (or a record that already showed it). ``add_row`` and ``close``
  lines are both closed this way; no update_task call backs either, because
  the runner, not a turn, wrote them.

The agent's own ``casework_event_append`` door cannot forge either: its
witness reads the audit log for a TOOL_CALL_COMPLETED row, which no
``casefile:`` id ever names.

WHO MAY APPLY WHAT (``approved_lines``). Only the numbers the current reply
names, only lines raised on that reply's thread, and only lines whose
approval on that thread is recorded under the CURRENT replier's key: a line
another administrator approved is applied only on that administrator's reply,
so each write is made under the person who approved it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

from . import bound_replies, casework_ledger
from .broker_context import BrokerContext
from .casefile_ledger import APPLIED_OUTCOMES, NOTHING_TO_DO, SKILL_NAME
from .casefile_store import CasefileLedger
from .casework_verbs import casework_ledger_path
from .recipient_policy import sender_key

logger = logging.getLogger(__name__)

#: The prefix of a ``completed`` row's tool_call_id that this module witnesses.
WITNESS_PREFIX = "casefile:"
#: The outcomes an apply job records as made (or already so on the record).
DONE = (*APPLIED_OUTCOMES, *NOTHING_TO_DO)


def casework_event(line: dict[str, Any], event: str, **extra: Any) -> dict[str, Any]:
    """One casework row for a case file line (the broker stamps ts and id)."""
    return {
        "event": event,
        "skill": SKILL_NAME,
        "kind": line["kind"],
        "matter_id": line["matter_id"],
        "source_id": line["source_id"],
        "item_key": casework_ledger.item_key(
            matter_id=line["matter_id"], kind=line["kind"], source_id=line["source_id"]
        ),
        **extra,
    }


def casefile_ledger_of(broker: BrokerContext) -> CasefileLedger:
    """The ledger the reply binding reads (a read: the queue dir is never written)."""
    queue = os.environ.get("SMD_CASEFILE_QUEUE_DIR") or "/run/smd-medchron/casefile-queue"
    return CasefileLedger(str(broker.audit_db_path), queue)


def reply_sent(audit_db_path: str | None, binding_key: str) -> bool:
    """The raise witness: the delivered job's bound reply is settled ``sent``."""
    return bool(audit_db_path) and bound_replies.outcome(str(audit_db_path), binding_key) == "sent"


def applied_witness(store: CasefileLedger, event: dict[str, Any]) -> bool:
    """The close witness: ``casefile:<apply job>:<n>`` names a delivered apply
    job whose recorded outcome for line ``n`` is made (and read back)."""
    parts = str(event.get("tool_call_id") or "").split(":")
    if len(parts) != 3 or parts[0] + ":" != WITNESS_PREFIX:
        return False
    row = store.read(parts[1])
    if row is None or row.get("state") != "delivered" or row.get("mode") != "apply":
        return False
    try:
        outcomes = {int(o["n"]): o["outcome"] for o in json.loads(row.get("outcomes_json") or "[]")}
        return outcomes.get(int(parts[2])) in DONE
    except (ValueError, KeyError, TypeError):
        return False


def raise_lines(broker: BrokerContext, job_id: str, thread_ref: str, session_id: str, binding_key: str) -> int:
    """Every proposed line of a delivered job becomes a numbered ``proposed``
    row on the reply's thread, once. Returns the count raised."""
    verbs = getattr(broker, "casefile", None)
    path = casework_ledger_path(broker)
    if verbs is None or verbs.ledger is None or not path or not thread_ref:
        return 0
    if not reply_sent(broker.audit_db_path, binding_key):
        return 0
    dispatch = secrets.token_hex(16)
    lines = verbs.ledger.mark_raised(job_id, thread_ref, dispatch)
    raised = 0
    with broker._escalation_lock:
        existing = casework_ledger.read_ledger(path)
        for ln in lines:
            action = {"task": "close", "event": "add_event"}.get(ln["kind"], "add_row")
            ev = casework_event(
                ln, "proposed", n=int(ln["n"]), dispatch_ref=dispatch, thread_ref=thread_ref,
                payload={"action": action, "class": "done" if action == "close" else "open",
                         "reason": f"case file job {job_id} change {ln['n']}",
                         "evidence": [f"job:{job_id}", f"sha:{ln['sha256'][:16]}"]},
                **({"session_id": session_id} if session_id else {}),
            )  # fmt: skip
            try:
                casework_ledger.validate_append(
                    existing,
                    ev,
                    send_witness=lambda _e: reply_sent(broker.audit_db_path, binding_key),
                    audit_witness=lambda e: applied_witness(verbs.ledger, e),
                )
            except ValueError:
                logger.warning("case file line %s of %s not raised", ln["n"], job_id)
                continue
            stamped = casework_ledger.stamp_event(ev)
            casework_ledger.append_line(path, stamped)
            existing.append(stamped)
            raised += 1
    return raised


def after_bound_reply(broker: BrokerContext, v: Any, session_id: str) -> None:
    """A delivered case file job's bound reply just went out: raise its lines
    on that reply's thread. The broker raises them itself; the model never
    names a line. Never raises: the reply already went."""
    row = getattr(v, "row", None) or {}
    if getattr(v, "kind", "") != "casefile_job" or row.get("state") != "delivered" or row.get("mode") != "full":
        return
    try:
        raised = raise_lines(broker, v.job_id, v.conversation_id, session_id, v.key)
    except Exception:
        logger.exception("could not raise the case file lines of %s", v.job_id)
        return
    if raised and broker.ledger is not None:
        broker.ledger.append(
            {
                "action_type": "CASEFILE_LINES_PROPOSED",
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "metadata": json.dumps({"job_id": v.job_id, "lines": raised}, sort_keys=True, separators=(",", ":")),
            }
        )


def approved_lines(
    store: CasefileLedger, ledger_path: str, thread_ref: str, approver: str, numbers: list[int]
) -> tuple[str, list[dict[str, Any]], list[int]]:
    """(source job, lines this reply may apply, numbers named that were
    approved there by someone else). Raised on this thread, named in this
    reply, approved on this thread under THIS replier's key, not taken."""
    rows = [r for r in store.lines_on_thread(thread_ref) if r["state"] == "raised"]
    if not rows:
        return "", [], []
    source = rows[-1]["job_id"]
    mine = sender_key(approver)
    states = casework_ledger.derive_state(casework_ledger.read_ledger(ledger_path))
    out, others = [], []
    for r in rows:
        if r["job_id"] != source or int(r["n"]) not in numbers:
            continue
        key = casework_ledger.item_key(matter_id=r["matter_id"], kind=r["kind"], source_id=r["source_id"])
        st = states.get(key)
        d = st.decisions.get((thread_ref, int(r["n"]))) if st is not None else None
        if d is None or d.verdict != "approved":
            continue
        if (d.decided_by or {}).get("key") == mine:
            out.append({k: r[k] for k in ("n", "kind", "matter_id", "source_id", "sha256")})
        else:
            others.append(int(r["n"]))
    return source, out, others


def close_lines(broker: BrokerContext, store: CasefileLedger, row: dict[str, Any]) -> int:
    """Each line a delivered apply job ended: ``completed`` for a line made
    (or already so), ``write_failed`` for one refused, each witnessed."""
    path = casework_ledger_path(broker)
    if not path:
        return 0
    outcomes = {int(o["n"]): o["outcome"] for o in json.loads(row.get("outcomes_json") or "[]")}
    closed = 0
    with broker._escalation_lock:
        existing = casework_ledger.read_ledger(path)
        for ln in store.lines(row["source_job_id"]):
            if ln.get("apply_job_id") != row["id"] or ln["n"] not in outcomes:
                continue
            if outcomes[ln["n"]] in DONE:
                ev = casework_event(ln, "completed", tool_call_id=f"{WITNESS_PREFIX}{row['id']}:{ln['n']}")
            else:
                ev = casework_event(ln, "write_failed", error=f"case file apply: {outcomes[ln['n']]}")
            try:
                casework_ledger.validate_append(
                    existing,
                    ev,
                    send_witness=lambda _e: False,
                    audit_witness=lambda e: applied_witness(store, e),
                )
            except ValueError:
                continue  # no open authorization (already consumed): nothing to close
            stamped = casework_ledger.stamp_event(ev)
            casework_ledger.append_line(path, stamped)
            existing.append(stamped)
            closed += 1
    return closed


__all__ = [
    "WITNESS_PREFIX",
    "after_bound_reply",
    "applied_witness",
    "approved_lines",
    "casefile_ledger_of",
    "casework_event",
    "close_lines",
    "raise_lines",
    "reply_sent",
]
