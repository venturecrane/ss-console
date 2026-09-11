"""The fourteen establishment verbs (ADR 0085, ss#2161/#2162, ss-console#2529).

Conversational establishment: the narrow verbs by which an instructed
voice/shape submission, a rule, an act, or an operations ask crosses the
agent -> broker trust boundary into the root intake's spool. The peer gate
(agent uid, non-gateway PID: an execute_code turn) is declared on the verb
table in ``verbs.py`` and enforced there before anything below runs.

EVERYTHING STORED IS REBUILT. ``establishment_store.py`` reads a bounded field
set off each request, computes every hash server-side, and refuses (never
sanitizes) a malformed field. The agent's uid has no access to the spool at
any level; these verbs are the only door, and the root intake independently
re-verifies uid and hashes on the other side.

One pinned ``action_type`` per WRITING verb, so none can forge another row:
RULE_PROPOSED on propose, SUBMITTED on submit, RESULT on status, ACT_PROPOSED
on act_propose, ACT_COMMITTED on act_commit (ss-console#2536), RULE_DECLINED on
decline and RULE_LAPSED on the lapse report (ss-console#2546; nothing at all
when the outcome reported is a decline, because RULE_DECLINED already recorded
it), OPS_REQUEST_RECORDED / OPS_REQUEST_RESOLVED / OPS_REQUEST_LAPSED on the
three operations verbs (ss-console#2546, ADR 0085 as amended 2026-08-22: a
routine, a schedule, a channel, a memory setting, an autonomy level or an
on/off is SMD's to change, so the firm cannot confirm one). ``establish_pending``,
``establish_notify_claim``, ``establish_notify_release`` and ``ops_ask_sent``
write no row: which of our processes is speaking, or being asked again, is not
a decision about the firm's work.

The two notify verbs exist because the seat runs the establishment plugin in
TWO processes, the gateway and its webhook-gate child, each with its own
sweeper, so an in-process once-guard was two guards and the requester was
mailed the same outcome letter twice (pilot-smokeball 2026-08-23, overlay
fc8f88c1, vfy_01M0QK1927KP54R7J13J2TH3WZ). The claim now lives in the one
process both share.

Every call runs under the broker's establishment lock and starts with an
opportunistic TTL sweep: the broker is the only principal that can expire
staging sets and unread results (the intake owns only run dirs), and the
pending-rules TTL rides the same sweep so the table stays bounded with no
timer.
"""

from __future__ import annotations

from typing import Any

#: verb -> EstablishmentStore method name. ``establish_status`` is the
#: fall-through the old dispatcher used for the last name in its list.
_METHODS: dict[str, str] = {
    "establish_stage_document": "stage_document",
    "establish_propose": "propose",
    "establish_pending": "pending_rules",
    "establish_submit": "submit",
    "establish_status": "status",
    "act_propose": "act_propose",
    "act_commit": "act_commit",
    "establish_decline": "decline",
    "establish_lapse_notified": "lapse_notified",
    "establish_notify_claim": "notify_claim",
    "establish_notify_release": "notify_release",
    "ops_propose": "ops_propose",
    "ops_resolve": "ops_resolve",
    "ops_ask_sent": "ops_ask_sent",
}

VERBS: tuple[str, ...] = tuple(_METHODS)


def establish(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    if broker.ledger is None:
        raise ValueError("audit ledger not configured on this broker")
    if broker.establishment is None:
        raise ValueError("establishment spool not configured on this broker")
    method = getattr(broker.establishment, _METHODS[action])
    with broker._establish_lock:
        broker.establishment.sweep()
        return method(request)
