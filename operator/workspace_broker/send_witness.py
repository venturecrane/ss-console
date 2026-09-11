"""Did this broker actually deliver something to a person?

The witness behind ``escalation_ledger.validate_append``'s raise rule. A
``fired``/``chased`` claims an alarm reached a human; ``should_fire`` then reads
``last_raised_date`` off that row and suppresses the deadline for
``refire_days``. So a raise recorded for an alert nobody received silences a real
deadline — the alarm does not ring, and writes down that it rang. On
pilot-smokeball 2026-08-26 that happened five times in one turn whose only
delivery attempt was a refused memo, and on 2026-08-20 across 77 appends.

This asks a question about the broker's OWN past behaviour rather than trusting
anything the caller passed. The broker is the only transmit path on the Machine
(``entrypoint.sh`` unsets the send credentials before any agent-uid process
exists) and it writes ``CONFIRM_SEND_DISPATCHED`` itself, so the agent supplies
no evidence here and can forge none.

Semantics are deliberately those of the overlay's ``shared/heartbeat.py``
``_dispatched_to_a_person`` — the function that decides the matching
``no_send_attempted`` page. A raise this admits while the pager still calls the
run unsent would be the gate-regression shape all over again.

Lives apart from ``server.py`` because that module is under a size ratchet that
only tightens.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from . import escalation_ledger

logger = logging.getLogger(__name__)

#: Recipient prefix reserved for our own smoke and verification traffic. A
#: dispatch that reached ONLY these is not a routine reaching a human, so it must
#: not witness a raise — otherwise a prove-out clears its own falsifier. Mirrors
#: ``_PROBE_RECIPIENT_PREFIX`` in the overlay's shared/heartbeat.py.
PROBE_RECIPIENT_PREFIX = "ss-probe"

#: Deployment-order fallback ONLY. The overlay plumbs ``session_id`` onto the
#: append; a caller that predates that plumbing sends an event without one, and a
#: seat can carry a new image with an older pinned overlay. Refusing there would
#: break every raise on such a seat, so a sessionless event degrades to a bounded
#: recent-dispatch window instead — the same skill+time imprecision the heartbeat
#: already accepts for the pager, and still strictly stronger than the
#: no-check-at-all this replaces. Observed send->append gap on pilot: ~4s.
UNSESSIONED_WINDOW_SECONDS = 180

_SQL = "SELECT metadata FROM audit_log WHERE action_type = 'CONFIRM_SEND_DISPATCHED'"


def _reached_a_person(meta: dict[str, Any]) -> bool:
    recipients = meta.get("recipients")
    if not isinstance(recipients, list):
        return False
    return any(isinstance(r, str) and not r.strip().lower().startswith(PROBE_RECIPIENT_PREFIX) for r in recipients)


def dispatched_to_a_person(audit_db_path: str | None, event: dict[str, Any]) -> bool:
    """True iff this broker dispatched to a non-probe recipient for the event's
    session.

    Fails OPEN on an unreadable ledger, and only there. Everywhere else this
    control refuses, because a refused raise merely re-fires next tick; but a
    broker that cannot read its own audit DB would refuse EVERY raise forever,
    which is a worse failure than the one being prevented. Same reasoning for an
    audit-disabled image: it has no witness and never had one, so it must not
    become a seat that cannot escalate at all.
    """
    if not audit_db_path:
        return True
    session_id = str(event.get("session_id") or "").strip()
    # With a session id the scan needs NO time bound: the session does the
    # discriminating, a turn may outlast any window we would have picked, and
    # leaving the clock out is what lets the retro-falsifier replay real
    # historical rows instead of testing its own assumption about "now".
    sql, params = _SQL, ()
    if not session_id:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - UNSESSIONED_WINDOW_SECONDS))
        sql, params = _SQL + " AND substr(ts,1,19) >= ?", (cutoff,)
    try:
        conn = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        logger.warning("escalation raise witness: audit DB unreadable; allowing raise")
        return True
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        logger.warning("escalation raise witness: audit query failed; allowing raise")
        return True
    finally:
        conn.close()
    # Deliberately no json_extract(): a build without the JSON1 extension would
    # raise, and the handler above would silently turn this control off. Parsing
    # here also keeps the recipient test the shape heartbeat.py uses.
    for (metadata,) in rows:
        try:
            meta = json.loads(metadata or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        if session_id and str(meta.get("session_id") or "").strip() != session_id:
            continue
        if _reached_a_person(meta):
            return True
    return False


def append_escalation_event(broker: Any, request: dict[str, Any], peer_uid: int) -> dict[str, Any]:
    """The ``escalation_event_append`` verb: uid-gated, validated, server-stamped.

    Lifted out of ``server.py`` when the raise witness pushed that module past its
    size ratchet, and it belongs beside the witness anyway: this is the tree's only
    caller of ``validate_append``, and the witness is the argument it now has to
    supply.

    Caller shape is a cron ``pre_run`` or the agent's ``execute_code`` turn — agent
    uid, non-gateway PID. Validation is the broker's alone (the overlay tool is a
    socket courier, not a second validator), and both doors into silence are closed
    here: an ``acked`` with no prior raise, and a raise nothing witnessed. Serialized
    by the broker's instance lock so the tail-read and the append stay consistent on
    a threaded server. ``ts``/``id`` are stamped server-side, so a caller cannot
    backdate.
    """
    agent_uid = broker._resolve_agent_uid()
    if agent_uid is None or peer_uid != agent_uid:
        raise PermissionError("escalation_event_append requires a caller running as the agent uid")
    if not broker.escalation_ledger_path:
        raise ValueError("escalation ledger path not configured on this broker")
    event = request.get("event")
    if not isinstance(event, dict):
        raise ValueError("escalation_event_append requires an 'event' object")
    with broker._escalation_lock:
        existing = escalation_ledger.read_ledger(broker.escalation_ledger_path)
        escalation_ledger.validate_append(
            existing,
            event,
            send_witness=lambda ev: dispatched_to_a_person(broker.audit_db_path, ev),
        )
        stamped = escalation_ledger.stamp_event(event)
        escalation_ledger.append_line(broker.escalation_ledger_path, stamped)
    return {"ok": True, "id": stamped["id"]}
