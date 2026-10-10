"""The summary at the top of a Negotiation Details tab, kept true after a write.

WHY. The 10/9/26 backfill opened each tab it filled with a summary ("Entered
10/9/26 from the offer letters and emails saved in this file. Latest: our
$1,000,000 demand of 7/28/26 has no response in the file."). The first live
negotiation run (2026-10-10) added the carrier's $956,000 tender beneath it,
and the summary went on telling the firm the demand had no response: a false
statement in the firm's own record. So when a run writes a row, it rewrites the
summary's "Latest:" part from the tab as it now stands.

ONLY THE OPERATOR'S OWN SUMMARY. A summary that does not open with the
Operator's "Entered <date> from the offer letters ..." sentence was written by
the firm and is never touched (the connector refuses it too). The opening
sentence is kept as it was; only what follows it is rewritten.

Pure: no client, no clock. Who made an offer is read from the row's own note,
which the Operator wrote; when it cannot be told, no name is given (never
guessed)."""

from __future__ import annotations

import re
from typing import Any

from smokeball_connector.layout_sections import DETAILS_MAX, OPERATOR_DETAILS

from .rows import _number, _row_kind, mdy, money, real_date

_JOINT = "joint, all plaintiffs."
_OFFER_WORD = re.compile(r"^(.*?)\s+(?:counter-offer|998 offer|policy-limits tender|offer)\b", re.IGNORECASE)


def who_of(note: Any) -> str:
    """Who made a row's offer, from the row's Operator-written note
    ("Kemper / Infinity Select offer; answers ..." -> "Kemper / Infinity
    Select"); "" when the note does not say."""
    text = str(note or "").strip()
    if text.lower().startswith(_JOINT):
        text = text[len(_JOINT) :].strip()
    for seg in text.split(";"):
        seg = re.split(r",\s+per\s+'", seg)[0].strip()
        if not seg or seg.lower().startswith(("our ", "answers ", "from ")):
            continue
        found = _OFFER_WORD.match(seg)
        if found:
            who = re.sub(r"\s*\(.*?\)", "", found.group(1)).split(", via ")[0]
            return who.strip(" ,")
    return ""


def _when(row: dict[str, Any], side: str) -> str:
    return real_date(str(row.get(f"{side}_date") or "")[:10]) or ""


def _last(rows: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    """The latest row holding this side's figure: by date, then row order. An
    undated figure never outranks a dated one."""
    held = [r for r in rows if r.get(f"{side}_amount") not in (None, "") or _when(r, side)]
    return max(held, key=lambda r: (_when(r, side), int(r.get("row") or 0)), default=None)


def _offer_phrase(row: dict[str, Any], accepted: dict[int, str]) -> str:
    who = who_of(row.get("note"))
    amt = money(_number(row.get("offer_amount")))
    head = " ".join(x for x in (who, amt) if x) if amt else f"{who + ' ' if who else ''}offer (amount not stated)"
    d = _when(row, "offer")
    head += f" ({mdy(d)})" if d else " (date not stated)"
    acc = accepted.get(int(row.get("row") or 0))
    if acc is not None:
        head += f", accepted {mdy(acc)}" if acc else ", accepted"
    return head


def _demand_phrase(row: dict[str, Any]) -> str:
    kind = _row_kind(row) or "figure"
    amt = money(_number(row.get("demand_amount")))
    d = _when(row, "demand")
    what = f"our {amt} {kind}" if amt else f"our {kind}"
    what += f" of {mdy(d)}" if d else " (date not stated)"
    if not amt:
        what += " (amount not stated)"
    return f"{what} has no response in the file"


def latest(rows: list[dict[str, Any]], accepted: dict[int, str] | None = None) -> str:
    """ "Latest: ..." for the tab's rows, or "" when the tab has no figure.
    ``accepted`` maps a row to the acceptance date ("" if undated) a document
    this run read showed for that row's offer."""
    accepted = accepted or {}
    offer, demand = _last(rows, "offer"), _last(rows, "demand")
    parts = []
    if offer is not None:
        parts.append(_offer_phrase(offer, accepted))
    if demand is not None:
        newer = offer is None or (_when(demand, "demand") > _when(offer, "offer") and _when(offer, "offer"))
        if newer:
            parts.append(_demand_phrase(demand))
    return f"Latest: {'; '.join(parts)}." if parts else ""


def refreshed(details: Any, rows: list[dict[str, Any]], accepted: dict[int, str] | None = None) -> str | None:
    """The summary to write after a run's rows went in, or None: None when the
    summary is not the Operator's (the firm's own is never changed) or already
    says this."""
    if not isinstance(details, str):
        return None
    found = OPERATOR_DETAILS.match(details)
    if not found:
        return None
    line = latest(rows, accepted)
    new = f"{found.group(0)} {line}".strip()[:DETAILS_MAX]
    return None if new == details.strip() else new


__all__ = ["latest", "refreshed", "who_of"]
