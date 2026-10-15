"""Parity: this run's values against the last delivered ones.

A value that changed is EXPLAINED when the change cites a document: either a
pass overturned it and named the source, or the new value's own source is a
file that is new or changed on the matter since the last run. A value that
changed with neither (the same papers, a different answer; or a value that
simply vanished) is UNEXPLAINED, and an unexplained divergence holds the job:
a person decides which read is right before the firm sees either.

One divergence is explained by the calendar alone: a next court date that
has passed since the last run is expected to move on. And a cited value
replacing one the gates could not pass (``seedcheck``: a date that was its
source file's save date and is not in that file's text, a source no longer on
the matter, a status outside the vocabulary) is explained: the last list's
value was the defect, and this run read it again for exactly that reason.

A value the last list carried that its document did not support, which this
run read again and found no date for, is explained the same way.

Values are compared by IDENTITY, not wording (``identity.py``, 2026-10-14):
a discovery set is its kind and set number, a combined name is one entry per
set, a party is its ``party_key``. A renaming alone is never a change.

Every divergence, explained or not, is also the workbook's "What changed" tab.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from . import identity, vocab


def _src_id(v: Any) -> str | None:
    src = v.get("source") if isinstance(v, dict) else None
    return str(src.get("file_id")) if isinstance(src, dict) and src.get("file_id") else None


def _key(v: Any, *fields: str) -> Any:
    if not isinstance(v, dict):
        return None
    vals = tuple(v.get(f) for f in fields)
    return None if all(x in (None, "") for x in vals) else (vals[0] if len(vals) == 1 else vals)


def party_key(name: str) -> str:
    """One defendant across two lists whose names are spelled differently
    (``identity.party_key``; kept here, where its callers have always looked)."""
    return identity.party_key(name)


def _keyed(path: str) -> str:
    """An overturn's path with its defendant name in ``party_key`` form."""
    m = re.match(r"^defendants\[(.*)\](.*)$", path)
    return f"defendants[{party_key(m.group(1))}]{m.group(2)}" if m else path


def points(m: dict[str, Any] | None) -> dict[str, tuple[Any, dict[str, Any] | None]]:
    """``{path: (comparable value, the value object it came from)}``."""
    if not m:
        return {}
    out: dict[str, tuple[Any, dict[str, Any] | None]] = {}
    cs = m.get("case_status") if isinstance(m.get("case_status"), dict) else {}
    out["case_status"] = (_key(cs, "value"), cs)
    out["complaint_filed"] = (_key(m.get("complaint_filed"), "date"), m.get("complaint_filed"))
    out["next_court_date"] = (_key(m.get("next_court_date"), "date"), m.get("next_court_date"))
    for d in m.get("defendants") or []:
        name = party_key(str(d.get("name") or ""))
        if _PLACEHOLDER.match(name):
            continue
        basis = d.get("answered") if _src_id(d.get("answered")) else d.get("served")
        out[f"defendants[{name}].status"] = (d.get("status"), basis)
        out[f"defendants[{name}].served"] = (_key(d.get("served"), "date"), d.get("served"))
        out[f"defendants[{name}].answered"] = (_key(d.get("answered"), "date"), d.get("answered"))
    for kind in ("discovery_propounded", "discovery_served_on_client"):
        for key, r in identity.keyed_rows(m.get(kind)):
            label = f"{kind}[{key}]"
            out[label + ".date"] = (r.get("date"), r)
            if kind == "discovery_served_on_client":
                rs = r.get("responses_served") or {}
                out[label + ".responses"] = (_key(rs, "value", "date"), rs)
    return out


#: Defendant statuses that claim the ABSENCE of a paper (2026-10-14 proof run:
#: "Not served" on a Doe line the last list carried bare). One of these first
#: appearing has nothing to cite; a status claiming service still needs its paper.
_NO_PAPER = (vocab.NOT_SERVED, vocab.D_UNCLEAR)


#: "Does 1 to 25", "Doe 3": the complaint's placeholder for parties not yet
#: named, not a party. Whether a read lists it is never a change.
_PLACEHOLDER = re.compile(r"^does?(\d+(to\d+)?)?$")


def _is_status(path: str) -> bool:
    return path.startswith("defendants[") and path.endswith("].status")


def _contradicted_its_proof(old: Any, old_obj: Any, cur: Any, cur_obj: Any) -> bool:
    """The last list said "Not served" beside a service date it cited, and
    this run's status (no longer "Not served") rests on that same proof of
    service: the change is the last list's error, corrected from its own paper."""
    if old != vocab.NOT_SERVED or cur in (None, *_NO_PAPER):
        return False
    was, now = _src_id(old_obj), _src_id(cur_obj)
    return bool(was and was == now and (old_obj or {}).get("date"))


def _renamed(
    after: dict[str, tuple[Any, dict[str, Any] | None]], prior: dict[str, Any], new: dict[str, Any]
) -> dict[str, tuple[Any, dict[str, Any] | None]]:
    """``after`` with each discovery set this run wrote a party for shorter
    or longer than the last list keyed as the last list keys it
    (``identity.pair_rows``), so a wording alone is never a change."""
    out = dict(after)
    for kind in ("discovery_propounded", "discovery_served_on_client"):
        for nk, ok in identity.pair_rows(prior.get(kind), new.get(kind)).items():
            for path in [p for p in out if p.startswith(f"{kind}[{nk}].")]:
                out[f"{kind}[{ok}]" + path[len(f"{kind}[{nk}]") :]] = out.pop(path)
    return out


def _unsupported(
    path: str, old: Any, old_obj: Any, pairs: set[tuple[str, str]] | frozenset, on_matter: set[str] | None
) -> bool:
    src = _src_id(old_obj)
    if path == "case_status" and old is not None and old not in vocab.CASE_STATUSES:
        return True
    if src and on_matter is not None and src not in on_matter:
        return True
    date = old_obj.get("date") if isinstance(old_obj, dict) else None
    return bool(src and isinstance(date, str) and (src, date) in pairs)


def compare(
    prior: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    moved_files: set[str],
    overturns: list[dict[str, Any]],
    today: dt.date,
    unsupported: set[tuple[str, str]] | frozenset = frozenset(),
    on_matter: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Every divergence on one matter, each marked explained or not.
    ``unsupported``: ``(file_id, date)`` pairs of prior values read again
    because their source's text does not carry their date; ``on_matter``: the
    matter's file ids now (a prior source not among them was read again)."""
    if prior is None:
        return [{"path": "matter", "old": None, "new": "added to the list", "explained": True, "why": "new matter"}]
    before, after = points(prior), _renamed(points(new), prior, new)
    cited = {_keyed(str(o.get("path"))) for o in overturns if o.get("source")}
    out = []
    for path in sorted(set(before) | set(after)):
        old, old_obj = before.get(path, (None, None))
        cur, cur_obj = after.get(path, (None, None))
        if old == cur or (old is None and cur is None):
            continue
        why = None
        if any(path == c or path.startswith(c + ".") or path.startswith(c + "[") for c in cited):
            why = "a pass overturned it citing a document"
        elif cur is not None and _src_id(cur_obj) in moved_files:
            why = "its source is a document new or changed since the last list"
        elif old is None and cur is not None and _src_id(cur_obj):
            why = "a value the last list did not have, with a source"
        elif cur is not None and _src_id(cur_obj) and not _src_id(old_obj):
            why = "a cited value replacing one the last list carried without a source"
        elif path == "next_court_date" and isinstance(old, str) and old < today.isoformat():
            why = "the last list's court date has passed"
        elif cur is not None and _src_id(cur_obj) and _unsupported(path, old, old_obj, unsupported, on_matter):
            why = "a cited value replacing one the last list carried that its document did not support"
        elif cur is None and _unsupported(path, old, old_obj, unsupported, on_matter):
            why = "the last list's value was not supported by its document, and no document gives one"
        elif old is None and cur in _NO_PAPER and _is_status(path):
            why = "a status the last list did not carry that claims no service, so no paper exists to cite"
        elif _is_status(path) and _contradicted_its_proof(old, old_obj, cur, cur_obj):
            why = "the last list's status contradicted its own cited proof of service"
        out.append(
            {
                "path": path,
                "label": _label(path, new, prior),
                "old": old,
                "new": cur,
                "explained": why is not None,
                "why": why or "changed with no document cited",
            }
        )
    return out


def unexplained(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in changes if not c["explained"]]


def _label(path: str, *results: dict[str, Any]) -> str:
    """The path with the defendant's name, or the discovery set and party,
    as a list spells them (for people)."""
    m = re.match(r"^defendants\[(.*)\](.*)$", path)
    if m:
        for res in results:
            for d in res.get("defendants") or []:
                if party_key(str(d.get("name") or "")) == m.group(1):
                    return f"defendants[{d.get('name')}]{m.group(2)}"
        return path
    m = re.match(r"^(discovery_\w+)\[(.*)\](\.\w+)$", path)
    if m:
        for res in results:
            for key, r in identity.keyed_rows(res.get(m.group(1))):
                if key == m.group(2):
                    return f"{m.group(1)}[{r.get('set')}|{identity.row_party(r)}]{m.group(3)}"
    return path
