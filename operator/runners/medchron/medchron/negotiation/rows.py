"""The rows one new document adds to a Negotiation Details tab, in the shape the
firm approved on 2026-10-09. Pure: no client, no model, no clock.

THE APPROVED SHAPE (the backfill's ``rows.py``, carried over):

* our demand (or counter, or 998) and the offer that answered it share a row
  ONLY when both arrive in the same run into an empty row. A row already on the
  tab is never paired into (its note was written for what it held, and would
  turn false): an offer answering a demand already on the tab gets its own
  offer-only row, its note naming what it answers ("Progressive offer; answers
  our demand of 9/1/26; from 'Offer 10-07.pdf' letter"); an offer with no open
  demand, or a demand, starts the next row;
* the row's note names who and what and the source ("Progressive offer, per
  'Offer 10-07.pdf' letter"); an amount the reader could not confirm in the
  document is NOT entered: the date goes in and the note says the amount is to
  be checked against the letter;
* a joint offer to every plaintiff is entered once, on the first plaintiff's
  tab, its note opening "Joint, all plaintiffs.";
* a carrier repeating an offer, acceptances, rejections and mediator's
  proposals add no row.

WHAT IS NEVER WRITTEN. A tab the firm keeps itself: one that already has rows
and whose summary is not the Operator's ("Entered <date> ..."). Every row the
Operator ever wrote went in under that summary, so a tab without it was filled
by the firm, and its figures are the firm's to keep. The connector's own
add-only rule (nothing already entered is changed) holds underneath this.

Duplicates are decided HERE, with the connector's own ``match_existing``, before
anything is sent: ``already_present`` adds nothing and tells nobody;
``possible_duplicate`` (the same amount on another date) is not written and the
firm is asked to check the letter.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from smokeball_connector.layout_sections import ROWS, match_existing

OURS = {"demand": "Our demand", "counter_offer": "Our counter", "998_offer_by_us": "Our 998 offer"}
THEIRS = {
    "offer": "offer",
    "counter_offer": "counter-offer",
    "998_offer_to_us": "998 offer",
    "policy_limits_tender": "policy-limits tender",
}
#: The kinds that are an offer TO the firm's client: each new one is a notice.
OFFER_KINDS = frozenset({"offer", "998_offer_to_us", "policy_limits_tender"})
SKIP_KINDS = frozenset({"acceptance", "rejection", "mediation_proposal", "other"})
#: The kinds that, made by the other side, are an offer the firm is told about:
#: a carrier's counter-offer included (the firm's data, 2026-10-10).
NOTICE_KINDS = frozenset(THEIRS)
OPERATOR_MARK = "Entered "
NOTE_MAX = 250


def mdy(d: str | None) -> str:
    if not d:
        return "date not stated"
    y, m, dd = d.split("-")
    return f"{int(m)}/{int(dd)}/{y[2:]}"


def party(s: str | None) -> str:
    s = re.sub(r"\s*\(.*?\)", "", s or "").strip()
    return re.sub(r"\b(Insurance Exchange|Insurance Company|Insurance|Ins\.?|Company|Co\.)\s*$", "", s).strip() or s


def money(a: Any) -> str:
    return f"${a:,.2f}".replace(".00", "") if isinstance(a, (int, float)) else ""


def amount_text(a: Any) -> str | None:
    """The connector's figure-as-written form, or None."""
    if isinstance(a, bool) or not isinstance(a, (int, float)) or a <= 0:
        return None
    return str(int(a)) if float(a).is_integer() else f"{a:.2f}"


def real_date(d: Any) -> str | None:
    if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.strip()):
        return None
    try:
        date.fromisoformat(d.strip())
    except ValueError:
        return None
    return d.strip()


def is_ours(e: dict[str, Any], tokens: set[str]) -> bool:
    if e.get("kind") in ("demand", "998_offer_by_us"):
        return True
    if e.get("kind") in OFFER_KINDS:
        return False
    by = (e.get("by") or "").lower()
    return "plaintiff" in by or any(t in by for t in tokens)


def operator_kept(tab: dict[str, Any]) -> bool:
    """A tab the Operator may write: empty, or carrying its own summary."""
    if not tab.get("rows"):
        return True
    return str(tab.get("details") or "").startswith(OPERATOR_MARK)


def source_phrase(doc: dict[str, Any]) -> str:
    name = re.sub(r"\s+", " ", str(doc.get("name") or "")).strip()
    if len(name) > 70:
        name = name[:67].rsplit(" ", 1)[0] + "..."
    kind = "email" if str(doc.get("ext") or "").lower() in (".msg", ".eml") else "letter"
    return f"'{name}' {kind}"


def _note(label: str, doc: dict[str, Any], joint: bool) -> str:
    txt = f"{label}, per {source_phrase(doc)}"
    if joint:
        txt = "Joint, all plaintiffs. " + txt
    return txt if len(txt) <= NOTE_MAX else txt[: NOTE_MAX - 4].rsplit(" ", 1)[0] + "..."


#: The labels a row's own note opens with (the backfill's and this lane's), and
#: what the "answers our ..." phrase calls that row.
_ROW_KINDS = (("our 998 offer", "998 offer"), ("our counter", "counter"), ("our demand", "demand"))


def _row_kind(row: dict[str, Any]) -> str | None:
    """What an earlier row of ours IS, from its own note; None when it cannot
    be told (never guessed: a 998 offer called a demand is a false record)."""
    note = str(row.get("note") or "").strip().lower()
    if note.startswith("joint, all plaintiffs."):
        note = note[len("joint, all plaintiffs.") :].strip()
    return next((kind for prefix, kind in _ROW_KINDS if note.startswith(prefix)), None)


def _answers(row: dict[str, Any]) -> str:
    """ "answers our counter of 10/2/26", or, when the row's kind is unknown,
    "answers our 10/2/26 figure of $500,000"."""
    when = real_date(str(row.get("demand_date") or "")[:10])
    kind = _row_kind(row)
    amt = money(_number(row.get("demand_amount")))
    if kind:
        return f"answers our {kind} of {mdy(when)}" if when else f"answers our {kind} on the row above"
    if when:
        return f"answers our {mdy(when)} figure of {amt}" if amt else f"answers our {mdy(when)} figure"
    return f"answers our figure of {amt} on the row above" if amt else "answers the row above"


def _number(raw: Any) -> float | None:
    try:
        return float(str(raw).replace(",", "").replace("$", "")) if raw not in (None, "") else None
    except ValueError:
        return None


def _answer_note(label: str, demand_row: dict[str, Any], doc: dict[str, Any], joint: bool) -> str:
    """An offer-only row's note when the figure of ours it answers is already on the tab."""
    txt = f"{label}; {_answers(demand_row)}; from {source_phrase(doc)}"
    if joint:
        txt = "Joint, all plaintiffs. " + txt
    return txt if len(txt) <= NOTE_MAX else txt[: NOTE_MAX - 4].rsplit(" ", 1)[0] + "..."


def _label(e: dict[str, Any], ours: bool, confirmed: bool) -> str:
    if ours:
        lab = OURS.get(str(e.get("kind")), "Our demand")
    else:
        lab = f"{party(e.get('by'))} {THEIRS.get(str(e.get('kind')), 'offer')}".strip()
    if e.get("amount") is None:
        lab += " (amount not stated in the file)"
    elif not confirmed:
        lab += " (amount to be checked against the letter)"
    if not real_date(e.get("date")):
        lab += " (date not stated in the file)"
    return lab


def _row_dict(view_rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(r["row"]): {k: v for k, v in r.items() if k != "row"} for r in view_rows}


class TabPlan:
    """What one plaintiff's tab gets from one document."""

    def __init__(self, tab: dict[str, Any]) -> None:
        self.tab = tab
        self.rows = _row_dict(tab.get("rows") or [])
        self.args: list[dict[str, Any]] = []
        self.kept = operator_kept(tab)
        #: Rows already on the tab before this run. Never paired into: a filled
        #: row's note was written for what it held then, and a field added
        #: beside it would leave that note saying something no longer true.
        self.existing = frozenset(self.rows)

    def next_row(self) -> int:
        return max(self.rows, default=-1) + 1

    def open_demand_row(self, offer_date: str | None) -> int | None:
        """The last row, when it holds a demand with no offer yet and the offer
        is not dated before it."""
        if not self.rows:
            return None
        n = max(self.rows)
        r = self.rows[n]
        if not (r.get("demand_amount") or r.get("demand_date")) or r.get("offer_amount") or r.get("offer_date"):
            return None
        dd = real_date(str(r.get("demand_date") or "")[:10])
        if offer_date and dd and offer_date < dd:
            return None
        return n

    def add(self, row: int, fields: dict[str, Any]) -> int:
        self.args.append({"row": row, **fields})
        self.rows.setdefault(row, {}).update({k: v for k, v in fields.items() if k != "note"})
        return len(self.args) - 1


def _fields(prefix: str, e: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    amt = amount_text(e.get("amount"))
    if amt and confirmed:
        out[f"{prefix}_amount"] = amt
    d = real_date(e.get("date"))
    if d:
        out[f"{prefix}_date"] = d
    return out


def _refusal(plan: TabPlan, fields: dict[str, Any]) -> tuple[str, str, int | None] | None:
    """(status, reason, row) when the event is not to be written, else None."""
    if not plan.kept:
        return "not_entered", "this file's Negotiation Details are kept by the firm", None
    if not fields:
        return "not_entered", "the letter's amount and date could not be read", None
    seen = match_existing(fields, plan.rows)
    if seen and seen[0] == "possible_duplicate":
        return seen[0], f"the same amount is already on row {seen[1] + 1} with a different date", seen[1]
    if seen:
        return seen[0], "", seen[1]
    return None


def _target_row(plan: TabPlan, ours: bool, fields: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    """(row, the existing demand row it answers or None). An offer pairs only
    into a row this run created; a row already on the tab is never added to."""
    row = None if ours else plan.open_demand_row(fields.get("offer_date"))
    if row is not None and row in plan.existing:
        return plan.next_row(), plan.rows[row]
    return (plan.next_row() if row is None else row), None


def _place(plan: TabPlan, e: dict[str, Any], ours: bool, doc: dict[str, Any], rec: dict[str, Any]) -> None:
    """Decide one event against its tab; ``rec`` carries the outcome."""
    confirmed = e.get("amount_confirmed") is True
    fields = _fields("demand" if ours else "offer", e, confirmed)
    refused = _refusal(plan, fields)
    if refused is not None:
        status, reason, row = refused
        rec["status"] = status
        if reason:
            rec["reason"] = reason
        if row is not None:
            rec["row"] = row
        return
    row, answers = _target_row(plan, ours, fields)
    if row >= ROWS:
        rec.update(status="not_entered", reason="the tab's ten rows are full")
        return
    if row not in plan.rows:
        label = _label(e, ours, confirmed)
        joint = bool(rec.get("joint"))
        fields["note"] = _answer_note(label, answers, doc, joint) if answers is not None else _note(label, doc, joint)
    date_only = not confirmed or e.get("amount") is None
    rec.update(status="to_write", row=row, entry=plan.add(row, fields), date_only=date_only)


def _accepted_in(events: list[dict[str, Any]], offer: dict[str, Any]) -> str | None:
    """The date (or "") of an acceptance of this same amount in the same
    document, or None. An old offer saved late must not read as a fresh one."""
    amt = offer.get("amount")
    for e in events:
        if e.get("kind") == "acceptance" and amt is not None and e.get("amount") == amt:
            return real_date(e.get("date")) or ""
    return None


def _dated(e: dict[str, Any]) -> str:
    return real_date(e.get("date")) or "9999-99-99"


def plan_document(
    events: list[dict[str, Any]],
    doc: dict[str, Any],
    tabs: list[dict[str, Any]],
    tokens: set[str],
) -> dict[str, Any]:
    """``{"tabs": {pidx: TabPlan}, "offers": [...]}``. Each offer is one notice
    candidate: ``status`` is ``to_write`` (``entry`` indexes its TabPlan's
    args; ``date_only`` when the amount was not confirmed), ``already_present``
    (no notice), or ``not_entered`` / ``possible_duplicate`` with the
    ``reason`` the firm is told."""
    by_idx = {int(t["plaintiff_index"]): t for t in tabs}
    plans: dict[int, TabPlan] = {}
    if not by_idx:
        reason = "this file has no Negotiation Details tab"
        offers = [
            {"event": e, "status": "not_entered", "reason": reason} for e in events if e.get("kind") in OFFER_KINDS
        ]
        return {"tabs": plans, "offers": offers}
    offers = []
    host, multi = min(by_idx), len(by_idx) > 1
    for e in sorted(events, key=_dated):
        kind = str(e.get("kind"))
        if kind in SKIP_KINDS:
            continue
        ours = is_ours(e, tokens)
        pidx = e.get("plaintiff_index")
        target: int = pidx if isinstance(pidx, int) and pidx in by_idx else host
        own = target == pidx
        rec: dict[str, Any] = {"event": e, "plaintiff_index": target, "joint": multi and not own}
        if e.get("amount") is None and not real_date(e.get("date")):
            continue  # a bare mention of an offer: nothing to enter, nothing new to tell
        _place(plans.setdefault(target, TabPlan(by_idx[target])), e, ours, doc, rec)
        if not ours and kind in NOTICE_KINDS and rec["status"] != "already_present":
            rec["accepted"] = _accepted_in(events, e)
            offers.append(rec)
    return {"tabs": plans, "offers": offers}


def details_for(tab: dict[str, Any], today: str) -> str:
    """The summary an EMPTY tab gets with its first row; never one already set."""
    if tab.get("rows") or tab.get("details"):
        return ""
    return f"{OPERATOR_MARK}{mdy(today)} from the offer letters and emails saved in this file."


__all__ = [
    "OFFER_KINDS",
    "OPERATOR_MARK",
    "TabPlan",
    "amount_text",
    "details_for",
    "is_ours",
    "mdy",
    "money",
    "operator_kept",
    "party",
    "plan_document",
    "source_phrase",
]
