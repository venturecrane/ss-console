"""`rank`: how urgent each file is TODAY, decided in code from the record.

The model reads and records; it never ranks. The triage contract's priority
rules, with the firm's thresholds (``firm.rank``), over the structured record
and the matter's own calendar and tasks. The FIRST rule that fits wins:

1. **Pull today.** A statute passed or within ``statute_pull_days`` with no
   complaint on file (posture ``litigation``) and the firm not out of the case
   (``declined``, ``settled_disbursed``); a court event within
   ``court_pull_days``; a government-claim deadline (an event or open task the
   firm's ``gov_claim_patterns`` name) within ``gov_claim_pull_days``.
2. **This week.** A statute within ``statute_week_days`` with no complaint; a
   settled file with items open, or a Pending status (settled, money not fully
   out: never priority 4); a court event within ``court_week_days``; an open
   task carrying money or a court date past its due date; a demand or offer
   with no activity in ``stale_negotiation_days``.
3. **This month.** Active workup: records still to order, a letter drafted or
   waiting on a fact, an overdue task, a change proposed.
4. **On track.**

Every reason names its date and source; nothing here invents a date. Rules the
triage contract states that need a judgment the record does not carry (an
offer's response deadline, money in trust ready to disburse, an unanswered
communication) are left to the reader's ``time_sensitive`` and ``questions``,
which the workbook shows under "Needs you".
"""

from __future__ import annotations

import datetime as dt
from typing import Any

LABEL = {1: "1 Pull today", 2: "2 This week", 3: "3 This month", 4: "4 On track"}
OUT_OF_CASE = ("declined", "settled_disbursed")


def _date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}/{str(d.year)[2:]}"


def events_matching(inv: dict[str, Any], patterns: list[Any], today: dt.date) -> list[tuple[dt.date, str]]:
    out = []
    for e in inv.get("events") or []:
        d, subj = _date(e.get("startTime") or e.get("start")), str(e.get("subject") or "")
        if d and d >= today and any(p.search(subj) for p in patterns):
            out.append((d, subj))
    return sorted(out)


def tasks_due(inv: dict[str, Any], patterns: list[Any]) -> list[tuple[dt.date, str]]:
    out = []
    for t in inv.get("tasks_open") or []:
        d, subj = _date(t.get("dueDate") or t.get("dueDateOnly")), str(t.get("subject") or "")
        if d and any(p.search(subj) for p in patterns):
            out.append((d, subj))
    return sorted(out)


def last_activity(inv: dict[str, Any], own: set[str]) -> str:
    """The newest dated thing on the file that the Operator did not write."""
    ds = [str(f.get("created") or "") for f in inv.get("files") or [] if str(f.get("id")) not in own]
    ds += [str(t.get("completedDate") or "") for t in inv.get("tasks_done") or [] if str(t.get("id")) not in own]
    ds += [str(m.get("dateCreated") or m.get("createdDate") or "") for m in inv.get("memos") or []]
    ds = [x[:10] for x in ds if x[:10]]
    return max(ds) if ds else ""


def _statute(rec: dict[str, Any], firm: Any, today: dt.date) -> tuple[int, str, dt.date] | None:
    d = _date(rec.get("statute_date"))
    if d is None or rec.get("posture") in ("litigation", *OUT_OF_CASE):
        return None
    days = (d - today).days
    when = "passed" if days < 0 else f"runs {_md(d)}"
    if days <= int(firm.rank("statute_pull_days")):
        return 1, f"Statute {when} ({_md(d)}) with no complaint in the file.", d
    if days <= int(firm.rank("statute_week_days")):
        return 2, f"Statute runs {_md(d)} with no complaint in the file.", d
    return None


def _calendar(inv: dict[str, Any], firm: Any, today: dt.date) -> list[tuple[int, str, dt.date]]:
    out: list[tuple[int, str, dt.date]] = []
    court = firm.rx(firm.rank("court_event_patterns"))
    for d, subj in events_matching(inv, court, today):
        days = (d - today).days
        if days <= int(firm.rank("court_pull_days")):
            out.append((1, f"{subj} on {_md(d)} (calendar).", d))
        elif days <= int(firm.rank("court_week_days")):
            out.append((2, f"{subj} on {_md(d)} (calendar).", d))
    gov = firm.rx(firm.rank("gov_claim_patterns"))
    for d, subj in [*events_matching(inv, gov, today), *tasks_due(inv, gov)]:
        if (d - today).days <= int(firm.rank("gov_claim_pull_days")):
            out.append((1, f"Government claim deadline {_md(d)}: {subj}.", d))
    return out


def _money_overdue(inv: dict[str, Any], firm: Any, today: dt.date) -> list[tuple[int, str, dt.date]]:
    out = []
    for t in inv.get("tasks_open") or []:
        d, subj = _date(t.get("dueDate") or t.get("dueDateOnly")), str(t.get("subject") or "")
        if d and d < today and firm.at_stake_rx.search(subj):
            out.append((2, f"Open task past due {_md(d)}: {subj}.", d))
    return out


def _settled(rec: dict[str, Any], inv: dict[str, Any]) -> list[tuple[int, str, dt.date | None]]:
    status = str((inv.get("matter") or {}).get("status") or "")
    if rec.get("posture") == "settled_open_items":
        return [(2, "Settled with items still open (" + str(rec.get("posture_why") or "")[:160] + ")", None)]
    if status == "Pending":
        return [(2, "Pending: settled, and the money is not fully distributed.", None)]
    return []


def _stale(rec: dict[str, Any], firm: Any, today: dt.date, last: str) -> list[tuple[int, str, dt.date | None]]:
    d = _date(last)
    if rec.get("posture") in ("demand_out", "offer_pending") and d is not None:
        if (today - d).days >= int(firm.rank("stale_negotiation_days")):
            return [(2, f"Demand or offer with no activity since {_md(d)}.", d)]
    return []


def _workup(rec: dict[str, Any], proposed: int) -> list[tuple[int, str, None]]:
    out: list[tuple[int, str, None]] = []
    if rec.get("records_to_order"):
        out.append((3, f"{len(rec['records_to_order'])} facilities' records still to order.", None))
    letters = [
        x for x in rec.get("letters") or [] if isinstance(x, dict) and x.get("status") in ("draft", "needs_fact")
    ]
    if letters:
        out.append((3, f"{len(letters)} letters drafted or waiting on a fact.", None))
    if proposed:
        out.append((3, f"{proposed} changes proposed for approval.", None))
    return out


def rank(
    rec: dict[str, Any], inv: dict[str, Any], firm: Any, today: dt.date, own: set[str], proposed: int = 0
) -> dict[str, Any]:
    """``{priority, label, reason, key_dates, last_activity}``."""
    last = last_activity(inv, own)
    hits: list[tuple[int, str, Any]] = []
    st = _statute(rec, firm, today)
    if st is not None:
        hits.append(st)
    hits += _calendar(inv, firm, today) + _money_overdue(inv, firm, today)
    hits += _settled(rec, inv) + _stale(rec, firm, today, last) + _workup(rec, proposed)
    hits.sort(key=lambda h: (h[0], h[2] or dt.date.max))
    key_dates = sorted({(h[2].isoformat(), h[1]) for h in hits if h[2] is not None})
    if not hits:
        why = (
            "Nothing due soon; nothing waiting on the firm."
            if rec.get("posture") not in OUT_OF_CASE
            else ("Out of the case (" + str(rec.get("posture")) + "); no deadline or money item is live.")
        )
        return {"priority": 4, "label": LABEL[4], "reason": why, "key_dates": [], "last_activity": last}
    top = hits[0]
    return {
        "priority": top[0],
        "label": LABEL[top[0]],
        "reason": top[1],
        "key_dates": [{"date": d, "what": w} for d, w in key_dates],
        "last_activity": last,
    }


def order_key(r: dict[str, Any]) -> tuple[Any, ...]:
    """Urgent files by their first date; the rest by the longest-quiet file first."""
    rk = r["rank"]
    first = rk["key_dates"][0]["date"] if rk["key_dates"] else "9999"
    if rk["priority"] in (1, 2):
        return (rk["priority"], first, str(r.get("number")))
    return (rk["priority"], rk.get("last_activity") or "0000", str(r.get("number")))


__all__ = ["LABEL", "last_activity", "order_key", "rank"]
