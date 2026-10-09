"""The one short message the firm gets per new offer, composed in code from the
offer as read and the write's read-back. The completion turn sends it as
written; no model words it, so no figure, name or date can drift between the
tab and the email.

    New offer on matter 200123, Doe v. Example.
    Example Mutual offer of $15,000 dated 10/7/26, per 'Offer 10-07.pdf' letter.
    Entered in Negotiation Details, row 3.

An offer that was not entered says so, and why, and asks for the letter to be
checked: it is never dropped silently. No em dashes (the firm's house rule for
every Operator message).
"""

from __future__ import annotations

import re
from typing import Any

from .rows import THEIRS, mdy, money, party, real_date, source_phrase

MAX = 900


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("—", ",").replace("–", "-")).strip()


def offer_line(e: dict[str, Any], doc: dict[str, Any]) -> str:
    who = party(e.get("by")) or "The other side"
    what = THEIRS.get(str(e.get("kind")), "offer")
    amt = money(e.get("amount"))
    if not amt:
        fig = " (amount not stated)"
    elif e.get("amount_confirmed") is True:
        fig = f" of {amt}"
    else:
        fig = f" of {amt} as read (not confirmed in the letter)"
    d = real_date(e.get("date"))
    when = f" dated {mdy(d)}" if d else " (date not stated)"
    return f"{who} {what}{fig}{when}, per {source_phrase(doc)}."


def status_line(rec: dict[str, Any], plaintiff: str) -> str:
    whose = f" for {plaintiff}" if plaintiff else ""
    if rec["status"] == "written":
        row = int(rec["row"]) + 1
        if rec.get("date_only"):
            return (
                f"Entered in Negotiation Details{whose}, row {row}, with the date only: the amount "
                "could not be confirmed from the letter, please check it."
            )
        return f"Entered in Negotiation Details{whose}, row {row}."
    reason = str(rec.get("reason") or "it could not be confirmed")
    return f"Not entered in Negotiation Details, please check the letter: {reason}."


def compose(matter: dict[str, Any], doc: dict[str, Any], rec: dict[str, Any], plaintiff: str = "") -> str:
    head = f"New offer on matter {matter.get('number') or 'without a number'}"
    title = _clean(str(matter.get("title") or ""))
    head += f", {title}." if title else "."
    if rec.get("unread"):
        line = f"A document that may hold an offer was saved, per {source_phrase(doc)}."
    else:
        line = offer_line(rec["event"], doc)
    text = "\n".join((head, _clean(line), _clean(status_line(rec, plaintiff))))
    return text if len(text) <= MAX else text[: MAX - 3] + "..."


__all__ = ["compose", "offer_line", "status_line"]
