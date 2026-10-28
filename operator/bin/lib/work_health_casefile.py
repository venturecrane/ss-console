"""The Case File Manager's standing schedule in SMD's work digest.

The broker pages team@ ONCE per episode when a scheduled case file run cannot
start (``workspace_broker/casefile_schedule.py``: the Smokeball connection about
to lapse, a day missed at the cutoff, a refused submit). After that page, the
cron's own SUPPRESSED_WAKE row on every later fire is what keeps the matter in
front of SMD: this module turns the newest such row of the last day into ONE
digest line (no page), so a paused schedule is seen every day it stays paused
without paging every weekday.

The row's ``decision_basis`` is ``casefile_scheduled:<status>`` (the pre_run's
verdict word). Only the statuses below are work; ``queued``, ``deferred``,
``already_queued_today`` and ``not_business_day`` are the schedule working.

Pure: rows and a clock in, items out.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bin.lib.work_health import Item, hhmm, item, meta, stamped

SKILL = "case-file-manager"
PREFIX = "casefile_scheduled:"
WINDOW = timedelta(hours=26)
WORDS = {
    "token_gate": "is paused: the Smokeball connection needs renewing (or its age is unknown)",
    "missed": "missed a caseload today: no job started before the firm's cutoff",
    "refused": "was refused by the broker (budget, filing target or an unauthored setting)",
    "broker_unreachable": "could not reach the seat's broker",
    "broker_error": "got an error from the seat's broker",
}
TODO = {
    "token_gate": "Re-consent the Smokeball connection for this seat (memory: smokeball token program).",
    "missed": "Look at the caseload's last job; the alert email has the date and the caseload key.",
}


def casefile_schedule_items(seat: str, rows: list[dict], now: datetime) -> list[Item]:
    """One digest line when the newest scheduled fire of the last day is work."""
    newest = None
    for ts, row in stamped(rows):
        if row.get("action_type") != "SUPPRESSED_WAKE" or row.get("skill_name") != SKILL or now - ts > WINDOW:
            continue
        basis = str(meta(row).get("decision_basis") or "")
        if basis.startswith(PREFIX):
            newest = (ts, basis[len(PREFIX) :])
    if newest is None or newest[1] not in WORDS:
        return []
    ts, status = newest
    return [
        item(
            seat,
            "schedule_paused",
            f"casefile:{status}:{ts.date().isoformat()}",
            ts,
            f"The Case File Manager's weekday run {WORDS[status]} (last fire {hhmm(ts)}).",
            TODO.get(status, "Read the seat's audit row for the fire; SMD was paged once when this began."),
        )
    ]


__all__ = ["casefile_schedule_items"]
