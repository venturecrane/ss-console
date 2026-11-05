"""The check passes' verdicts, applied by code (split from ``read.py``, 2026-11-05).

A verdict names a value by path. A defendant's path carries its INDEX in the
result the reader was shown (``defendants[2].served``), which code built, so no
reader has to retype a name. The older ``defendants[<name>].<field>`` form is
accepted for one release.

Nothing is dropped quietly: a verdict that cannot be applied (an unknown
defendant, a corrected value that does not parse) goes in ``unmatched``, which
the passes record as ``unmatched_verdicts``. A reader may not set a status,
which is derived: it corrects the event, and an overturned status is logged as
unmatched. An "unclear" status still marks the defendant Unclear.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Any

from . import answers, defstatus, vocab
from .tools import MatterContext

_DEFENDANT_PATH = re.compile(r"^defendants\[(.*)\]\.(\w+)$")
_KEYS: dict[str, tuple[str, ...]] = {
    **{f: ("date",) for f in vocab.DEFENDANT_EVENTS},
    "served": ("date", "method"),
    "out_for_service": ("value",),
    "role": ("value",),
    "complaint_filed": ("date",),
    "next_court_date": ("date", "event"),
}
NO_STATUS_OVERTURN = "a status is derived from its events: the verdict must correct the event"


def find_defendant(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    key = " ".join(name.lower().split())
    return next(
        (d for d in result.get("defendants") or [] if " ".join(str(d.get("name")).lower().split()) == key), None
    )


def _target(result: dict[str, Any], path: str) -> tuple[dict[str, Any] | None, str]:
    """(the value's holder, its field). A defendant path that names nobody on
    the result has no holder; anything else is a field on the matter."""
    m = _DEFENDANT_PATH.match(path)
    if not m:
        return result, path
    ref, field = m.group(1).strip(), m.group(2)
    if ref.isdigit():
        ds = result.get("defendants") or []
        return (ds[int(ref)] if int(ref) < len(ds) else None), field
    return find_defendant(result, ref), field


def _keyed_value(ctx: MatterContext, field: str, c: Any, ref: dict[str, Any]) -> Any:
    keys = _KEYS.get(field)
    if keys is None:
        return None
    if field in ("role", "out_for_service"):
        c = c if isinstance(c, dict) else {"value": c}
    elif not isinstance(c, dict):
        c = {"date": c} if isinstance(c, str) or c is None else {}
    return answers.val(ctx, {**ref, **c}, keys)


def _case_status(ctx: MatterContext, c: Any, v: dict[str, Any]) -> Any:
    val = c.get("value") if isinstance(c, dict) else c
    if val not in vocab.CASE_STATUSES:
        return None
    return {
        "value": val,
        "detail": c.get("detail") if isinstance(c, dict) else v.get("why"),
        "source": ctx.source(v.get("doc"), v.get("doc_date")),
    }


def corrected_value(ctx: MatterContext, field: str, v: dict[str, Any]) -> Any:
    c = v.get("corrected")
    ref = {"doc": v.get("doc"), "doc_date": v.get("doc_date")}
    if field in ("case_name", "court", "case_number"):
        return c if isinstance(c, str) else None
    if field == "firm_role":
        return c if c in vocab.FIRM_ROLES else None
    if field == "case_status":
        return _case_status(ctx, c, v)
    if field in ("discovery_propounded", "discovery_served_on_client"):
        return answers.discovery(ctx, c, field == "discovery_served_on_client") if isinstance(c, list) else None
    if field == "sent_for_service" and isinstance(c, list):
        return answers.latest_sent(ctx, [{**ref, **r} for r in c if isinstance(r, dict)])
    return _keyed_value(ctx, field, c, ref)


class _Applier:
    def __init__(
        self, ctx: MatterContext, result: dict[str, Any], pass_name: str, today: dt.date, missed: list[dict[str, Any]]
    ) -> None:
        self.ctx, self.result, self.pass_name, self.today = ctx, result, pass_name, today
        self.log: list[dict[str, Any]] = []
        self.missed = missed

    def _miss(self, v: dict[str, Any], reason: str) -> None:
        self.missed.append(
            {
                "path": str(v.get("path") or ""),
                "verdict": v.get("verdict"),
                "reason": reason,
                "why": v.get("why"),
                "pass": self.pass_name,
            }
        )

    def _logged(self, path: str, old: Any, new: Any, v: dict[str, Any]) -> None:
        self.log.append(
            {
                "path": path,
                "old": old,
                "new": new,
                "source": self.ctx.source(v.get("doc"), v.get("doc_date")),
                "why": v.get("why"),
                "pass": self.pass_name,
            }
        )

    def _add_defendant(self, v: dict[str, Any]) -> None:
        d = answers.defendant(self.ctx, v.get("corrected"), self.today)
        if not d["name"]:
            self._miss(v, "the corrected defendant has no name")
        elif find_defendant(self.result, d["name"]) is not None:
            self._miss(v, "that defendant is already on the result")
        else:
            self.result.setdefault("defendants", []).append(d)
            self._logged(f"defendants[{d['name']}]", None, d, v)

    def _court_dates(self, v: dict[str, Any]) -> None:
        """A corrected list replaces the list; the next date is picked from it."""
        c = v.get("corrected")
        if v["verdict"] != "overturned" or not isinstance(c, list):
            self._miss(v, "court_dates takes the whole corrected list")
            return
        ref = {"doc": v.get("doc"), "doc_date": v.get("doc_date")}
        rows = answers.court_rows(self.ctx, [{**ref, **r} for r in c if isinstance(r, dict)])
        old = copy.deepcopy(self.result.get("next_court_date"))
        self.result["court_dates_read"] = rows
        new = answers.next_court(rows, self.today)
        self.result["next_court_date"] = new
        if (old or {}).get("date") != new["date"]:
            self._logged("next_court_date", old, new, v)

    def _status(self, d: dict[str, Any], v: dict[str, Any], path: str) -> None:
        if v["verdict"] == "overturned":
            self._miss(v, NO_STATUS_OVERTURN)
            return
        old = d.get("status")
        d["status_unclear"] = str(v.get("why") or "a check pass could not settle it")
        defstatus.absorb(d, self.today)
        if old != d["status"]:
            self._logged(path, old, d["status"], v)

    def _unclear_flag(self, target: dict[str, Any], v: dict[str, Any], path: str) -> None:
        key = "flags" if target is not self.result else "matter_flags"
        target.setdefault(key, []).append(f"Unclear: {v.get('why') or path}")

    def _set(self, target: dict[str, Any], field: str, path: str, v: dict[str, Any]) -> None:
        new = corrected_value(self.ctx, field, v)
        if new is None:
            self._miss(v, f"the corrected value for {field} does not parse")
            return
        old = copy.deepcopy(target.get(field))
        if old == new:
            return
        target[field] = new
        if field == "next_court_date":
            target["court_dates_read"] = [new] if new.get("date") else []
        if target is not self.result:
            defstatus.absorb(target, self.today)
        self._logged(path, old, new, v)

    def one(self, v: dict[str, Any]) -> None:
        path = str(v.get("path") or "")
        if path == "defendants[+]":
            self._add_defendant(v)
            return
        if path == "court_dates":
            self._court_dates(v)
            return
        target, field = _target(self.result, path)
        if target is None:
            self._miss(v, "no defendant on the result answers to this path")
            return
        shown = path if target is self.result else f"defendants[{target.get('name')}].{field}"
        if field == "status" and target is not self.result:
            self._status(target, v, shown)
        elif v["verdict"] == "overturned":
            self._set(target, field, shown, v)
        elif field == "case_status" and target is self.result:
            self._set_unclear_case(v, shown)
        else:
            self._unclear_flag(target, v, shown)

    def _set_unclear_case(self, v: dict[str, Any], path: str) -> None:
        new = {"value": vocab.UNCLEAR, "detail": v.get("why"), "source": None}
        old = copy.deepcopy(self.result.get("case_status"))
        if old != new:
            self.result["case_status"] = new
            self._logged(path, old, new, v)


def apply_verdicts(
    ctx: MatterContext,
    result: dict[str, Any],
    verdicts: list[dict[str, Any]],
    pass_name: str,
    today: dt.date,
    unmatched: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Apply every overturn (and every unclear) in place; a defendant whose
    event moved has its status derived again. Returns the overturn log: path,
    old, new, the source the overturn cites, why. A verdict that cannot be
    applied is appended to ``unmatched`` and changes nothing."""
    applier = _Applier(ctx, result, pass_name, today, unmatched if unmatched is not None else [])
    for v in verdicts if isinstance(verdicts, list) else []:
        if isinstance(v, dict) and v.get("verdict") in ("overturned", "unclear"):
            applier.one(v)
    return applier.log
