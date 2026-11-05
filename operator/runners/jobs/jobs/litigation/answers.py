"""A read's answer in the state schema: code keeps what the read found and works
out what follows from it (split from ``read.py``, 2026-11-05).

The read reports every dated event and every court date with its document.
Code derives the rest: a defendant's status (``defstatus.derive``), the last
date the papers went out for service, and the next court date.

Stored shape: ``sent_for_service`` is one value and ``next_court_date`` is one
value, as every report on a seat already has them. ``court_dates_read`` is the
list ``next_court_date`` was picked from; a report without it (one stored
before this module) keeps the one value it has. It is deliberately not
``court_dates``, which ``courtdates.shown`` sets on the copy the list prints.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from . import defstatus, gates, identity, vocab
from .tools import MatterContext

#: The matter flag for a side the read could not say the firm is on. Nothing
#: defaults to "plaintiff": the firm reads the list to know what it must do.
FIRM_ROLE_UNCLEAR = "Which side the firm represents is unclear from the file."
_NO_COURT_DATE = {"date": None, "event": None, "source": None}


def val(ctx: MatterContext, v: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    out = {k: v.get(k) for k in keys}
    out["source"] = ctx.source(v.get("doc"), v.get("doc_date"))
    return out


def latest_sent(ctx: MatterContext, raw: Any) -> dict[str, Any]:
    """The most recent request among those a read listed (a read may hand one
    value, as a verdict does). A date that is not a date is returned for the
    gate to refuse, never ordered with the rest. None dated: read, none found."""
    rows = raw if isinstance(raw, list) else [raw]
    dated = [v for v in (val(ctx, r, ("date",)) for r in rows if isinstance(r, dict)) if v["date"]]
    if not dated:
        return {"date": None, "source": None}
    bad = [v for v in dated if not gates.DATE.match(str(v["date"]))]
    return bad[0] if bad else max(dated, key=lambda v: str(v["date"]))


def _role(ctx: MatterContext, raw: Any) -> dict[str, Any]:
    """A read's role, kept only when it named one. A value outside the list is
    kept as read: the derivation answers Unclear to it."""
    if not isinstance(raw, dict) or not raw.get("value"):
        return {}
    return {"role": val(ctx, raw, ("value",))}


def defendant(ctx: MatterContext, d: Any, today: dt.date) -> dict[str, Any]:
    """One defendant in the state schema, its status derived. Whatever status
    the input carries is not read."""
    d = d if isinstance(d, dict) else {}
    out: dict[str, Any] = {
        "name": str(d.get("name") or "").strip(),
        "out_for_service": val(ctx, d.get("out_for_service"), ("value",)),
        "served": val(ctx, d.get("served"), ("date", "method")),
        "answered": val(ctx, d.get("answered"), ("date",)),
        **_role(ctx, d.get("role")),
        "flags": [str(x) for x in d.get("flags") or []],
    }
    # Kept only when the read answered it: an absent key renders "Not read
    # yet", never "Not in file", which would be a finding nobody made.
    if isinstance(d.get("sent_for_service"), (dict, list)):
        out["sent_for_service"] = latest_sent(ctx, d["sent_for_service"])
    for f in vocab.DEFENDANT_EVENTS:
        if f not in out and f != "sent_for_service" and isinstance(d.get(f), dict) and d[f].get("date"):
            out[f] = val(ctx, d[f], ("date",))
    out["status_from"] = vocab.FROM_DERIVED
    out["status"] = defstatus.derive(out, today)
    return out


def discovery(ctx: MatterContext, rows: Any, theirs: bool) -> list[dict[str, Any]]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        row = val(ctx, r, ("set", "served_by", "date") if theirs else ("set", "served_on", "date"))
        if theirs:
            row["responses_served"] = val(ctx, r.get("responses_served"), ("value", "date"))
        out.append(row)
    return identity.split_rows(out)  # one row per set, as the papers are served (identity.py)


def court_rows(ctx: MatterContext, rows: Any) -> list[dict[str, Any]]:
    """Every court date a read listed that has a date."""
    got = [val(ctx, r, ("date", "event")) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return [r for r in got if r["date"]]


def next_court(rows: list[dict[str, Any]], today: dt.date) -> dict[str, Any]:
    """The earliest date that is today or later. A date that is not a date is
    returned for the gate to refuse, never dropped as if it were not there."""
    bad = [r for r in rows if not gates.DATE.match(str(r.get("date")))]
    if bad:
        return copy.deepcopy(bad[0])
    ahead = [r for r in rows if str(r["date"]) >= today.isoformat()]
    return copy.deepcopy(min(ahead, key=lambda r: str(r["date"]))) if ahead else dict(_NO_COURT_DATE)


def repick_court(result: dict[str, Any], today: dt.date) -> None:
    """``next_court_date`` afresh from the list it was picked from, so one that
    has passed moves on to the next. A report with no list keeps its value."""
    if "court_dates_read" in result:
        result["next_court_date"] = next_court(result["court_dates_read"], today)


def case_status(ctx: MatterContext, raw: Any) -> dict[str, Any]:
    cs: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return {
        **val(ctx, cs, ("value", "detail")),
        "value": cs.get("value") if cs.get("value") in vocab.CASE_STATUSES else vocab.UNCLEAR,
    }


def firm_role(raw: Any) -> str:
    return raw if raw in vocab.FIRM_ROLES else vocab.UNCLEAR


def _case_fields(ctx: MatterContext, raw: dict[str, Any], today: dt.date) -> dict[str, Any]:
    rows = court_rows(ctx, raw.get("court_dates"))
    return {
        "case_name": raw.get("case_name"),
        "court": raw.get("court"),
        "case_number": raw.get("case_number"),
        "firm_role": firm_role(raw.get("firm_role")),
        "case_status": case_status(ctx, raw.get("case_status")),
        "complaint_filed": val(ctx, raw.get("complaint_filed"), ("date",)),
        "court_dates_read": rows,
        "next_court_date": next_court(rows, today),
    }


def to_state(ctx: MatterContext, raw: dict[str, Any], groups: list[str], today: dt.date) -> dict[str, Any]:
    """The model's answer for ``groups`` in the state schema."""
    out: dict[str, Any] = {}
    if vocab.GROUP_CASE in groups:
        out.update(_case_fields(ctx, raw, today))
    if vocab.GROUP_DEFENDANTS in groups:
        out["defendants"] = [defendant(ctx, d, today) for d in raw.get("defendants") or [] if isinstance(d, dict)]
    if vocab.GROUP_DISCOVERY in groups:
        out["discovery_propounded"] = discovery(ctx, raw.get("discovery_propounded"), False)
        out["discovery_served_on_client"] = discovery(ctx, raw.get("discovery_served_on_client"), True)
    out["matter_flags"] = [str(x) for x in raw.get("matter_flags") or []]
    if out.get("firm_role") == vocab.UNCLEAR:
        out["matter_flags"].append(FIRM_ROLE_UNCLEAR)
    if raw.get("notes"):
        out["notes"] = str(raw["notes"])
    return out


def merge(prior: dict[str, Any] | None, fresh: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """The fresh groups over the prior result; flags and notes from this read
    replace the prior's only when the whole matter was read."""
    base = copy.deepcopy(prior) if prior else {}
    for g in groups:
        for k in vocab.GROUP_FIELDS[g]:
            if k in fresh:
                base[k] = fresh[k]
    if vocab.GROUP_CASE in groups and "court_dates_read" in fresh:
        base["court_dates_read"] = fresh["court_dates_read"]
    if vocab.GROUP_DEFENDANTS in groups:
        base["defendants"] = identity.merge_defendants(base.get("defendants") or [])
    full = set(groups) == set(vocab.GROUPS)
    if full or not base.get("matter_flags"):
        base["matter_flags"] = fresh.get("matter_flags", [])
    else:
        base["matter_flags"] = list(dict.fromkeys([*base.get("matter_flags", []), *fresh.get("matter_flags", [])]))
    if fresh.get("notes") and (full or not base.get("notes")):
        base["notes"] = fresh["notes"]
    base["fields_read"] = sorted(set(base.get("fields_read") or []) | set(groups))
    return base
