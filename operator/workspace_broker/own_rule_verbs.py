"""A person's own rule after the read-back: re-opened, restated, and watched to install.

Three things a person's OWN rule (kind ``rule``, not waiting on an administrator)
needs once confirm-by-reply exists, each a verb under the establishment lock
(review 2026-11-19):

* ``establish_reopen_lapsed``: a plain "yes" arriving after the person's own rule
  lapsed silently (the sweep ends an unconfirmed own rule with no letter) has
  nothing open to confirm. The newest such rule of theirs from the last
  :data:`REOPEN_WINDOW_DAYS` is proposed again, fresh, from its stored row, and
  the seat reads it back. Each lapsed row is re-opened at most once. Rows are
  kept only ``TERMINAL_RETENTION_SECONDS`` after they end (seven days), so that
  is the real reach.
* ``establish_restated``: the person stated a rule again, and the model marked the
  new proposal as restating an older OPEN one of theirs. When both are theirs and
  open, of the same scope and the same document kinds, the older one is ended,
  silently, with its ``RULE_LAPSED`` record naming what replaced it. Nothing is
  closed on similarity alone.
* ``establish_install_watch`` / ``establish_install_check``: an own rule the seat
  committed by code is watched until its run says installed. The check reads each
  due run's status here (``EstablishmentStore.status``) and returns the ones
  that ended in anything else, can no longer be read, or are still not installed
  after :data:`INSTALL_GIVE_UP_S`; the seat logs those at ERROR. The watch lives
  in the audit DB beside the bindings, so a restart does not forget it.

Ids, addresses and times only; never a rule's words beyond the stored row they
already live in.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .broker_context import BrokerContext
from .establishment_constants import RULE_LAPSED_ACTION_TYPE
from .establishment_validation import (
    EstablishmentValidationError,
    _require_id,
    _require_proposal_id,
    require_address,
)
from . import readback_bindings, smd_owed
from .readback_bindings import _open_own_rule, _pending_store

logger = logging.getLogger(__name__)

REOPEN_WINDOW_DAYS = 30
INSTALL_CHECK_AFTER_S = 5 * 60
INSTALL_GIVE_UP_S = 25 * 60

VERB_REOPEN = "establish_reopen_lapsed"
VERB_RESTATED = "establish_restated"
VERB_WATCH = "establish_install_watch"
VERB_CHECK = "establish_install_check"
VERBS: tuple[str, ...] = (VERB_REOPEN, VERB_RESTATED, VERB_WATCH, VERB_CHECK)

_CREATE = (
    """CREATE TABLE IF NOT EXISTS own_rule_reopened (
  proposal_id TEXT PRIMARY KEY, reopened_as TEXT NOT NULL, created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS own_rule_install_watch (
  run_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, committed_at REAL NOT NULL, done_at REAL)""",
)


def _connect(pending: Any) -> Any:
    conn = pending._connect()
    for sql in _CREATE:
        conn.execute(sql)
    return conn


def reopen_lapsed(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Propose again the sender's newest silently lapsed own rule, or report none.

    Only a rule the yes REPLIES TO (review 2026-11-19): the reply's
    In-Reply-To/References must name the read-back message that carried it
    (``readback_bindings``). A late, unrelated yes reopens nothing."""
    pending = _pending_store(broker)
    sender = require_address(request.get("sender"), "sender")
    if not str(request.get("inbound_message_id") or "").strip():
        return {"ok": True, "reopened": False}
    bound = readback_bindings.readback_for(
        broker, {"sender": sender, "inbound_message_id": request.get("inbound_message_id")}
    )
    carried = [str(p) for p in bound.get("proposal_ids") or ()] if bound.get("bound") else []
    if not carried:
        return {"ok": True, "reopened": False}
    since = time.time() - REOPEN_WINDOW_DAYS * 86400
    conn = _connect(pending)
    try:
        lapsed = conn.execute(
            "SELECT proposal_id FROM pending_rules WHERE kind='rule' AND for_admin=0 "
            "AND instructed_by=? AND consumed_at IS NULL AND declined_at IS NULL "
            "AND lapsed_at IS NOT NULL AND lapsed_at >= ? "
            "AND proposal_id NOT IN (SELECT proposal_id FROM own_rule_reopened) "
            "ORDER BY lapsed_at DESC",
            (sender, since),
        ).fetchall()
    finally:
        conn.close()
    row = next((r for r in lapsed if str(r[0]) in carried), None)
    if row is None:
        return {"ok": True, "reopened": False}
    old = pending.get(str(row[0]))
    if old is None:
        return {"ok": True, "reopened": False}
    store = broker.establishment
    if store is None:
        raise ValueError("establishment spool not configured on this broker")
    fresh = store.propose(
        {
            "scope": old["scope"],
            "subject": old["subject"],
            "text": old["text"],
            "instructed_by": sender,
            "source_ref": str(request.get("source_ref") or "reopened")[:200],
            "for_admin": False,
        }
    )
    conn = _connect(pending)
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO own_rule_reopened (proposal_id, reopened_as, created_at) VALUES (?, ?, ?)",
                (old["proposal_id"], str(fresh.get("proposal_id") or ""), time.time()),
            )
    finally:
        conn.close()
    return {**fresh, "reopened": True}


def restated(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """End an older open own rule the newer one restates, when both qualify."""
    pending = _pending_store(broker)
    sender = require_address(request.get("sender"), "sender")
    old_id = _require_proposal_id(request.get("old_proposal_id"))
    new_id = _require_proposal_id(request.get("new_proposal_id"))
    if old_id == new_id:
        raise EstablishmentValidationError("a proposal does not restate itself")
    now = time.time()
    old, new = pending.get(old_id), pending.get(new_id)
    for proposal_id, row in ((old_id, old), (new_id, new)):
        why = _open_own_rule(row, sender, now)
        if why:
            raise EstablishmentValidationError(f"{proposal_id} cannot be restated: {why}")
    if old is None or new is None:  # unreachable: _open_own_rule refused a missing row
        raise EstablishmentValidationError("no such proposal")
    if old["scope"] != new["scope"] or (old["subject"] or {}).get("doc_kinds") != (new["subject"] or {}).get(
        "doc_kinds"
    ):
        raise EstablishmentValidationError("a restatement has the same scope and the same document kinds")
    conn = pending._connect()
    try:
        with conn:
            closed = conn.execute(
                "UPDATE pending_rules SET lapsed_at=?, lapse_notified_at=? WHERE proposal_id=? "
                "AND consumed_at IS NULL AND declined_at IS NULL AND lapsed_at IS NULL",
                (now, now, old_id),
            ).rowcount
    finally:
        conn.close()
    if closed and broker.ledger is not None:
        broker.ledger.append(
            {
                "action_type": RULE_LAPSED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "proposal_id": old_id,
                        "superseded_by": new_id,
                        "instructed_by": sender,
                        "scope": old["scope"],
                        "for_admin": False,
                        "text_sha256": old["text_sha256"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return {"ok": True, "closed": bool(closed)}


def install_watch(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Watch one own rule's run until it says installed."""
    pending = _pending_store(broker)
    proposal_id = _require_proposal_id(request.get("proposal_id"))
    run_id = _require_id(request.get("run_id"), "run_id")
    conn = _connect(pending)
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO own_rule_install_watch (run_id, proposal_id, committed_at) VALUES (?, ?, ?)",
                (run_id, proposal_id, time.time()),
            )
    finally:
        conn.close()
    return {"ok": True}


def _installed(pending: Any, proposal_id: str) -> bool:
    """Did another reader of the run's one-shot result already see it install?
    ``status`` stamps ``installed_at`` on the proposal whoever reads it."""
    row = pending.get(proposal_id) or {}
    return row.get("installed_at") is not None


def _verdict(store: Any, run_id: str, age: float, pending: Any = None, proposal_id: str = "") -> str:
    """``""`` installed, ``"wait"`` not yet due to give up, else why it failed."""
    try:
        answer = store.status({"run_id": run_id})
    except (EstablishmentValidationError, ValueError):
        # 2026-11-23: the result is one-shot; a run another reader collected
        # is installed when its proposal says so, not "lost".
        if pending is not None and proposal_id and _installed(pending, proposal_id):
            return ""
        return "its run can no longer be read"
    result = answer.get("result") if isinstance(answer, dict) else None
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        return "" if status == "installed" else f"its install ended {status[:40] or 'without a status'}"
    return "it is still not installed" if age >= INSTALL_GIVE_UP_S else "wait"


def _owe_install(broker: BrokerContext, pending: Any, proposal_id: str, why: str) -> None:
    """The person was told the rule is recorded and its install failed: work SMD
    owes, recorded here so the triage lists it until closed (review 2026-11-19).
    Keyed on the email that confirmed it (the proposal's source_ref)."""
    row = pending.get(proposal_id) or {}
    detail = f"rule {proposal_id} was confirmed and recorded, but {why}"
    append = broker.ledger.append if broker.ledger is not None else None
    if not smd_owed.record(
        broker.audit_db_path, append, smd_owed.KIND_INSTALL, str(row.get("source_ref") or ""), detail
    ):
        logger.error("own_rule_verbs: an install failure of %s could not be recorded as owed", proposal_id)


def install_check(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Read every due run once; return the failures, and mark each one settled."""
    pending = _pending_store(broker)
    store = broker.establishment
    now = time.time()
    conn = _connect(pending)
    try:
        due = conn.execute(
            "SELECT run_id, proposal_id, committed_at FROM own_rule_install_watch "
            "WHERE done_at IS NULL AND committed_at <= ?",
            (now - INSTALL_CHECK_AFTER_S,),
        ).fetchall()
    finally:
        conn.close()
    failed: list[dict[str, str]] = []
    settled: list[str] = []
    for run_id, proposal_id, committed_at in due:
        why = _verdict(store, str(run_id), now - float(committed_at), pending, str(proposal_id))
        if why == "wait":
            continue
        settled.append(str(run_id))
        if why:
            failed.append({"proposal_id": str(proposal_id), "run_id": str(run_id), "why": why})
            _owe_install(broker, pending, str(proposal_id), why)
    if settled:
        conn = _connect(pending)
        try:
            with conn:
                conn.executemany(
                    "UPDATE own_rule_install_watch SET done_at=? WHERE run_id=?",
                    [(now, run_id) for run_id in settled],
                )
        finally:
            conn.close()
    return {"ok": True, "failed": failed}


HANDLERS = {
    VERB_REOPEN: reopen_lapsed,
    VERB_RESTATED: restated,
    VERB_WATCH: install_watch,
    VERB_CHECK: install_check,
}

__all__ = [
    "HANDLERS",
    "INSTALL_CHECK_AFTER_S",
    "INSTALL_GIVE_UP_S",
    "REOPEN_WINDOW_DAYS",
    "VERBS",
    "install_check",
    "install_watch",
    "reopen_lapsed",
    "restated",
]
