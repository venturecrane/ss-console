"""The ``casework_event_append`` verb: the one door into the casework ledger.

The casework ledger (``casework_ledger.py``) records what the Operator proposed
about a firm's task list or an upcoming court date, what a person answered, and
what the Operator then wrote to the firm's record. An ``approved`` row is a
license to change a task in Smokeball, so, like the escalation ledger, the file
is broker-owned: the agent uid reads it and cannot forge a row. Every write
comes through here, is validated, and is stamped server-side.

The broker supplies the evidence the caller cannot: that a raise rode a message
this broker sent to a person (``send_witness.dispatched_to_a_person``), which
thread that message went out on (``digest_ref``, joined by ``dispatch_ref``
within the session; a caller's ``thread_ref`` on a raise is always dropped),
and that a ``completed`` names a successful update_task call in this session's
own audit log (``update_task_witnessed``), and that a ``step_ran`` names the
create_memo call the step's routine made in this session (same witness).

Three kinds of row also go into the hash-chained audit log, because each is a
decision about the firm's record that a person may later ask about:
``CASEWORK_APPROVED`` and ``CASEWORK_HELD`` (a verified person answered a
numbered line) and ``CASEWORK_CLOSED_BY_RECORD`` (the Operator closed its own
task on the record's evidence, at a level the firm set). The rest are
bookkeeping about messages and writes the audit log already holds.

Lives apart from ``send_witness.py`` so each ledger's verb sits beside its own
rules, and apart from ``casework_ledger.py``, whose bytes are pinned against the
overlay's copy.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import casework_ledger, digest_ref
from .broker_context import BrokerContext
from .send_witness import dispatched_to_a_person

logger = logging.getLogger(__name__)

#: The audit row each decision kind writes; every other kind writes none.
AUDIT_ACTION_TYPES: dict[str, str] = {
    "approved": "CASEWORK_APPROVED",
    "held": "CASEWORK_HELD",
    "closed_by_record": "CASEWORK_CLOSED_BY_RECORD",
}

_SQL = "SELECT metadata FROM audit_log WHERE action_type = 'TOOL_CALL_COMPLETED' AND metadata LIKE ?"


def casework_ledger_path(broker: BrokerContext) -> str | None:
    """The ledger file: the explicit env override, else beside the escalation
    ledger (the same broker-written, agent-readable directory). None when the
    broker has no ledger directory at all, which keeps the verb fail-closed."""
    explicit = os.environ.get(casework_ledger.LEDGER_PATH_ENV)
    if explicit:
        return explicit
    if not broker.escalation_ledger_path:
        return None
    return str(Path(broker.escalation_ledger_path).parent / "casework-ledger.jsonl")


def update_task_witnessed(audit_db_path: str | None, event: dict[str, Any]) -> bool:
    """True iff the audit log holds a successful call of the tool that backs
    this event kind (``casework_ledger.WITNESS_TOOLS``: update_task for a
    ``completed``, create_memo for a ``step_ran``) with this event's
    ``tool_call_id`` in this event's session.

    Same failure posture as the send witness: an audit-disabled broker has no
    witness and never had one, so it allows; an unreadable DB allows with a
    warning. Everywhere else it refuses, and a refused ``completed`` costs one
    item re-checked on the next run.
    """
    if not audit_db_path:
        return True
    call_id = str(event.get("tool_call_id") or "")
    session_id = str(event.get("session_id") or "").strip()
    # The LIKE below is a prefilter; the parse is the match. A quote or a "%"
    # cannot be in a real tool-call id and would only widen or break the filter.
    if not call_id or '"' in call_id or "%" in call_id:
        return False
    try:
        conn = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        logger.warning("casework completed witness: audit DB unreadable; allowing")
        return True
    try:
        rows = conn.execute(_SQL, (f"%{call_id}%",)).fetchall()
    except sqlite3.Error:
        logger.warning("casework completed witness: audit query failed; allowing")
        return True
    finally:
        conn.close()
    for (metadata,) in rows:
        try:
            meta = json.loads(metadata or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict) or meta.get("tool_call_id") != call_id:
            continue
        tool = casework_ledger.WITNESS_TOOLS.get(str(event.get("event")), casework_ledger.UPDATE_TASK_TOOL)
        if meta.get("tool") != tool or meta.get("outcome") != "ok":
            continue
        if session_id and str(meta.get("session_id") or "").strip() != session_id:
            continue
        return True
    return False


def _audit_row(stamped: dict[str, Any]) -> dict[str, Any] | None:
    action_type = AUDIT_ACTION_TYPES.get(str(stamped.get("event")))
    if action_type is None:
        return None
    metadata: dict[str, Any] = {
        "casework_id": stamped["id"],
        "skill": stamped.get("skill"),
        "kind": stamped.get("kind"),
        "item_key": stamped.get("item_key"),
        "matter_id": stamped.get("matter_id"),
        "session_id": stamped.get("session_id"),
    }
    for key in ("n", "thread_ref", "decided_by"):
        if stamped.get(key) is not None:
            metadata[key] = stamped[key]
    payload = stamped.get("payload")
    if isinstance(payload, dict):
        metadata["action"] = payload.get("action")
        metadata["evidence"] = payload.get("evidence")
    return {
        "action_type": action_type,
        "actor": "operator",
        "actor_role": "agent",
        "skill_name": stamped.get("skill"),
        "matter_ref": stamped.get("matter_id"),
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }


def append_casework_event(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, uid: int | None
) -> dict[str, Any]:
    """Validate, stamp and append one casework row; agent uid only.

    The verb table gates the caller first; the uid re-check here is the same
    sentence, defence in depth. Serialized by the escalation lock so the tail
    read and the append stay consistent, and so the two ledgers never write in
    the same instant from two threads.
    """
    agent_uid = broker._resolve_agent_uid()
    if agent_uid is None or uid != agent_uid:
        raise PermissionError("casework_event_append requires a caller running as the agent uid")
    path = casework_ledger_path(broker)
    if not path:
        raise ValueError("casework ledger path not configured on this broker")
    event = request.get("event")
    if not isinstance(event, dict):
        raise ValueError("casework_event_append requires an 'event' object")
    event = dict(event)
    if event.get("event") in casework_ledger.RAISING_EVENTS:
        # The thread is the broker's to name on a raise. A verdict's thread_ref
        # is checked against a raise's broker-stamped one inside validate_append.
        digest_ref.stamp_thread_ref(broker.audit_db_path, event, raising_events=casework_ledger.RAISING_EVENTS)
    with broker._escalation_lock:
        existing = casework_ledger.read_ledger(path)
        casework_ledger.validate_append(
            existing,
            event,
            send_witness=lambda ev: dispatched_to_a_person(broker.audit_db_path, ev),
            audit_witness=lambda ev: update_task_witnessed(broker.audit_db_path, ev),
        )
        stamped = casework_ledger.stamp_event(event)
        casework_ledger.append_line(path, stamped)
    row = _audit_row(stamped)
    if row is not None and broker.ledger is not None:
        broker.ledger.append(row)
    return {"ok": True, "id": stamped["id"], "thread_ref": stamped.get("thread_ref")}
