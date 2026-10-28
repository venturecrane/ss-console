"""`flags`: the low limits and the new files, decided in code from the record.

The reader records what the file says (the insurance and its limits as the
document prints them, the Medicals rows); it never judges a limit low and never
decides a file is new. Here, with the firm's thresholds:

* **Low limits** (``firm.low_limits``). A limits string is parsed only when it
  reads as limits ("30/60", "$15,000/$30,000", "100k/300k", "1M CSL"); the first
  figure is the per-person limit (a split limit written in thousands, as policies
  print it, reads in thousands). A policy at or under ``per_person_usd`` is a low
  limit; so is an OTHER-SIDE carrier the firm's ``carrier_patterns`` name, since
  the firm knows those carriers write the minimum. A string that does not parse
  prints as written and is never flagged. Bills on the file (the Medicals rows'
  charges) at or over ``bills_trigger_usd`` make an other-side low limit urgent.
* **New files** (``firm.new_matter``). A file opened (Smokeball's opened date)
  within ``opened_within_days`` that carries NONE of the firm's opening tasks,
  open or completed, is flagged with every opening task it is missing. A file
  with some of them is not: an opening task that does not fit a case type is the
  firm's to leave off. Nothing is created: the flag prepares, a person routes.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

_FIGURE = r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*([kKmM])?"
_TAIL = r"(?:\s*(?:csl|combined single limit|per person|each person|pp|bi|limits?))*"
LIMITS = re.compile(rf"^\s*{_FIGURE}(?:\s*/\s*{_FIGURE})*{_TAIL}\s*$", re.I)
FIRST = re.compile(_FIGURE)
SIDES = (("first_party", "Client's own policy"), ("third_party", "Other side"))


def per_person(limits: Any) -> int | None:
    """The per-person limit in dollars, or None when the string is not limits."""
    if not isinstance(limits, str) or not LIMITS.match(limits):
        return None
    m = FIRST.search(limits)
    if m is None:
        return None
    value = int(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    elif value < 1_000:
        value *= 1_000  # a split limit printed in thousands: 30/60 is $30,000 / $60,000
    return value or None


def bills_total(rec: dict[str, Any]) -> float:
    """The charges of the Medicals rows the record holds (new and existing)."""
    out = 0.0
    for m in rec.get("medicals") or []:
        c = m.get("charge") if isinstance(m, dict) else None
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            out += float(c)
    return round(out, 2)


def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def low_limits(rec: dict[str, Any], firm: Any) -> list[dict[str, Any]]:
    """One entry per policy the firm's rules call low: ``{side, side_label,
    carrier, limits, per_person, why, urgent}``. Empty when the firm set none."""
    threshold = int(firm.low_limits("per_person_usd"))
    carriers = firm.rx(list(firm.low_limits("carrier_patterns")))
    trigger = int(firm.low_limits("bills_trigger_usd"))
    bills = bills_total(rec)
    got = rec.get("insurance")
    ins: dict[str, Any] = got if isinstance(got, dict) else {}
    out = []
    for side, label in SIDES:
        for p in ins.get(side) or []:
            if not isinstance(p, dict):
                continue
            carrier, limits = str(p.get("carrier") or ""), p.get("limits")
            pp = per_person(limits)
            whys = []
            if threshold and pp is not None and pp <= threshold:
                whys.append(f"per-person limit {_money(pp)}, at or under the firm's {_money(threshold)}")
            if side == "third_party" and carrier and any(c.search(carrier) for c in carriers):
                whys.append("a carrier the firm lists as writing low limits")
            if not whys:
                continue
            urgent = side == "third_party" and bool(trigger) and bills >= trigger
            if urgent:
                whys.append(f"bills on the file {_money(bills)}, past the firm's {_money(trigger)} line")
            out.append(
                {
                    "side": side,
                    "side_label": label,
                    "carrier": carrier,
                    "limits": limits if isinstance(limits, str) else "",
                    "per_person": pp,
                    "bills": bills,
                    "why": "; ".join(whys) + ".",
                    "urgent": urgent,
                }
            )
    return out


def _date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def new_matter(inv: dict[str, Any], firm: Any, today: dt.date) -> dict[str, Any] | None:
    """``{opened, missing: [names]}`` for a new file with none of the opening
    tasks; None otherwise (or when the firm lists no opening tasks)."""
    tasks = list(firm.new_matter("opening_tasks"))
    if not tasks:
        return None
    detail = inv.get("detail") or {}
    opened = _date(detail.get("openedDate") or (inv.get("matter") or {}).get("openedDate"))
    if opened is None or not 0 <= (today - opened).days <= int(firm.new_matter("opened_within_days")):
        return None
    subjects = [str(t.get("subject") or "") for t in [*(inv.get("tasks_open") or []), *(inv.get("tasks_done") or [])]]
    present = [t for t in tasks if any(re.search(t["pattern"], s, re.I) for s in subjects)]
    if present:
        return None
    return {"opened": opened.isoformat(), "missing": [str(t["name"]) for t in tasks]}


def flags_for(rec: dict[str, Any], inv: dict[str, Any], firm: Any, today: dt.date) -> dict[str, Any]:
    return {"low_limits": low_limits(rec, firm), "new_matter": new_matter(inv, firm, today)}


__all__ = ["bills_total", "flags_for", "low_limits", "new_matter", "per_person"]
