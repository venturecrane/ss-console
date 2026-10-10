"""Parity: this run's values against the last delivered ones.

A value that changed is EXPLAINED when the change cites a document: either a
pass overturned it and named the source, or the new value's own source is a
file that is new or changed on the matter since the last run. A value that
changed with neither (the same papers, a different answer; or a value that
simply vanished) is UNEXPLAINED, and an unexplained divergence holds the job:
a person decides which read is right before the firm sees either.

One divergence is explained by the calendar alone: a next court date that
has passed since the last run is expected to move on.

Every divergence, explained or not, is also the workbook's "What changed" tab.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any


def _src_id(v: Any) -> str | None:
    src = v.get("source") if isinstance(v, dict) else None
    return str(src.get("file_id")) if isinstance(src, dict) and src.get("file_id") else None


def _key(v: Any, *fields: str) -> Any:
    if not isinstance(v, dict):
        return None
    vals = tuple(v.get(f) for f in fields)
    return None if all(x in (None, "") for x in vals) else (vals[0] if len(vals) == 1 else vals)


_NOISE = {
    "md",
    "do",
    "np",
    "pa",
    "rn",
    "dds",
    "inc",
    "llc",
    "lp",
    "llp",
    "corp",
    "co",
    "the",
    "dba",
    "aka",
    "sued",
    "as",
}


def party_key(name: str) -> str:
    """One defendant across two lists whose names are spelled differently
    ("X (sued as Y), M.D." / "X, MD"): the name's letters, parentheticals,
    initials and credentials dropped."""
    words = re.findall(r"[a-z]+", re.sub(r"\([^)]*\)", " ", name.lower()))
    words = [w for w in words if len(w) > 1 and w not in _NOISE]
    return "".join(words) or name


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
        basis = d.get("answered") if _src_id(d.get("answered")) else d.get("served")
        out[f"defendants[{name}].status"] = (d.get("status"), basis)
        out[f"defendants[{name}].served"] = (_key(d.get("served"), "date"), d.get("served"))
        out[f"defendants[{name}].answered"] = (_key(d.get("answered"), "date"), d.get("answered"))
    for kind in ("discovery_propounded", "discovery_served_on_client"):
        for r in m.get(kind) or []:
            label = f"{kind}[{r.get('set')}|{r.get('served_on') or r.get('served_by')}]"
            out[label + ".date"] = (r.get("date"), r)
            if kind == "discovery_served_on_client":
                rs = r.get("responses_served") or {}
                out[label + ".responses"] = (_key(rs, "value", "date"), rs)
    return out


def compare(
    prior: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    moved_files: set[str],
    overturns: list[dict[str, Any]],
    today: dt.date,
) -> list[dict[str, Any]]:
    """Every divergence on one matter, each marked explained or not."""
    if prior is None:
        return [{"path": "matter", "old": None, "new": "added to the list", "explained": True, "why": "new matter"}]
    before, after = points(prior), points(new)
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
    """The path with the defendant's name as a list spells it (for people)."""
    m = re.match(r"^defendants\[(.*)\](.*)$", path)
    if not m:
        return path
    for res in results:
        for d in res.get("defendants") or []:
            if party_key(str(d.get("name") or "")) == m.group(1):
                return f"defendants[{d.get('name')}]{m.group(2)}"
    return path
