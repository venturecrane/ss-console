"""A date the last list carried is not dropped because this read left it out.

WHY (2026-10-14 proof run). Values the last list carried came back empty
while their source document was unchanged and still on the matter: the run
read nothing that contradicts them, it simply did not restate them, and the
parity gate held the list on each one as "changed with no document cited".
An empty answer is not evidence of absence (the same document, the same
paper), so the list keeps the last value.

A value is carried forward only when ALL of these hold:

* this run's value has no date (a different date, or a different response,
  is a real answer and is never overwritten);
* the last value has a ``YYYY-MM-DD`` date and a source file;
* that file is still on the matter and is not new or changed since the last
  list (a changed document is read for what it says now);
* the value is not one the seed check sent back to be read again
  (``seedcheck``: a date that is its file's Smokeball save date and is not in
  that file's text); and, the same rule the gates apply, a date equal to its
  file's save date is carried only when this run holds the file's text and
  the text carries the date;
* the date is not in the future, and a next court date is not in the past;
* a defendant's answer date is carried only while its status says answered,
  a service date only while its status does not say not served (the gates
  refuse either contradiction).

No date is ever derived from a file NAME: the runner has no such convention,
and a name is not the document.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Callable

from . import gates, identity, vocab

DISCOVERY = ("discovery_propounded", "discovery_served_on_client")


def _qualifies(
    v: Any,
    *,
    moved: set[str],
    on_matter: set[str],
    saved: dict[str, set[str]],
    unsupported: set[tuple[str, str]] | frozenset,
    text_of: Callable[[str], "str | None"],
    today: dt.date,
    court: bool = False,
) -> bool:
    if not isinstance(v, dict):
        return False
    d, src = v.get("date"), v.get("source")
    if not (isinstance(d, str) and gates.DATE.match(d) and isinstance(src, dict) and src.get("file_id")):
        return False
    fid = str(src["file_id"])
    if fid not in on_matter or fid in moved or (fid, d) in unsupported:
        return False
    if (d < today.isoformat()) if court else (d > today.isoformat()):
        return False
    if d in saved.get(fid, set()):
        text = text_of(fid)
        return text is not None and gates.text_carries(text, d)
    return True


def _empty(v: Any) -> bool:
    return not (isinstance(v, dict) and v.get("date"))


def carry_forward(
    prior: dict[str, Any] | None,
    result: dict[str, Any],
    *,
    moved: set[str],
    on_matter: set[str],
    saved: dict[str, set[str]],
    unsupported: set[tuple[str, str]] | frozenset = frozenset(),
    text_of: Callable[[str], "str | None"] = lambda _fid: None,
    today: dt.date,
) -> list[str]:
    """Fill ``result`` (in place) with every last value this run left empty
    that the rules above allow. Combined discovery rows are split and
    near-duplicate defendants folded first, so the comparison is per set and
    per person. Returns the paths carried."""
    for kind in DISCOVERY:
        if kind in result:
            result[kind] = identity.split_rows(result.get(kind))
    if "defendants" in result:
        result["defendants"] = identity.merge_defendants(result.get("defendants") or [])
    if not prior:
        return []
    kw: dict[str, Any] = {
        "moved": moved,
        "on_matter": on_matter,
        "saved": saved,
        "unsupported": unsupported,
        "text_of": text_of,
        "today": today,
    }
    return _carry_top(prior, result, kw) + _carry_defendants(prior, result, kw) + _carry_discovery(prior, result, kw)


def _carry_top(prior: dict[str, Any], result: dict[str, Any], kw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("complaint_filed", "next_court_date"):
        if k in result and _empty(result.get(k)) and _qualifies(prior.get(k), court=k == "next_court_date", **kw):
            result[k] = copy.deepcopy(prior[k])
            out.append(k)
    return out


def _carry_defendants(prior: dict[str, Any], result: dict[str, Any], kw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    olds = identity.merge_defendants(prior.get("defendants") or [])
    for d in result.get("defendants") or []:
        old = next((o for o in olds if identity.same_person(str(o.get("name") or ""), str(d.get("name") or ""))), None)
        if old is None:
            continue
        st = d.get("status")
        allowed = {
            "answered": st in (vocab.ANSWERED, vocab.ANSWERED_ORIGINAL),
            "served": st not in (vocab.NOT_SERVED, vocab.OUT_FOR_SERVICE, vocab.D_UNCLEAR),
            # Papers go out before service whatever the status says now.
            "sent_for_service": True,
        }
        for f, ok in allowed.items():
            if ok and _empty(d.get(f)) and _qualifies(old.get(f), **kw):
                d[f] = copy.deepcopy(old[f])
                out.append(f"defendants[{d.get('name')}].{f}")
    return out


def _carry_discovery(prior: dict[str, Any], result: dict[str, Any], kw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for kind in DISCOVERY:
        before = dict(identity.keyed_rows(prior.get(kind)))
        for key, r in identity.keyed_rows(result.get(kind)):
            old = before.get(key)
            if old is not None:
                out += _carry_row(kind, key, r, old, kw)
    return out


def _carry_row(kind: str, key: Any, r: dict[str, Any], old: dict[str, Any], kw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if not r.get("date") and _qualifies(old, **kw):
        r["date"], r["source"] = old.get("date"), copy.deepcopy(old.get("source"))
        out.append(f"{kind}[{key}].date")
    if kind != "discovery_served_on_client":
        return out
    rs, ors = r.get("responses_served") or {}, old.get("responses_served") or {}
    same_answer = rs.get("value") in (None, "", ors.get("value"))
    if same_answer and _empty(rs) and _qualifies(ors, **kw):
        r["responses_served"] = copy.deepcopy(ors)
        out.append(f"{kind}[{key}].responses")
    return out
