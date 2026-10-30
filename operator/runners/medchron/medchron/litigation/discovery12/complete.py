"""`complete`: the twelve sections completed from the status list's own values.

WHY (2026-10-30). An independent review of the first full run found what the
breakdown printed was right (2,028 of 2,031 checked fields) but that it left
things out: of 1,232 disagreements with the status list, the papers sided
with the status list 755 times, 470 of them a served date the breakdown left
blank and 119 a set it never listed. Four sections printed "None in file"
although a paper of that kind was in the file.

The rules, conservative by design:

* A breakdown value with its own cited paper is never overwritten. Only a
  served date that is blank (and not marked unconfirmed) is filled, from the
  status list's matching set, with the status list's source.
* A status-list set with no plausible match in its section is added to that
  section, marked as coming from the status list. A plausible match is the
  same section, the same direction, no conflicting set number, and a shared
  party word (or a party missing on either side).
* "None in file" therefore prints only when neither the breakdown nor the
  status list has anything for that section.
* A status-list set whose name does not say which kind of discovery it is,
  and every set of a case the breakdown did not read, is listed under the
  case's discovery as "From the status list", never dropped.
* A status-list value that names no paper says so in its source.
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: Notes on an entry the status list supplied, and the label for its block.
STATUS_NOTE = "From the status list"
FILLED_NOTE = "Served date from the status list"
STATUS_LABEL = "From the status list"
NO_PAPER = {"name": "status list, no paper named"}

_DIRS = (
    ("discovery_propounded", "propounded by the firm", "served_on"),
    ("discovery_served_on_client", "served on the firm's client", "served_by"),
)
_KIND = (
    ("supplemental", re.compile(r"supplement|further|amended", re.I)),
    ("frogs", re.compile(r"form\s+interrog|\bfrogs?\b", re.I)),
    ("srogs", re.compile(r"special\s+interrog|\bsrogs?\b|interrogator", re.I)),
    ("rfas", re.compile(r"admission|\brfas?\b", re.I)),
    ("rfps", re.compile(r"production|inspection|\brfps?\b|\brpds?\b|documents", re.I)),
    ("depositions", re.compile(r"deposition", re.I)),
    ("nonparty_subpoenas", re.compile(r"subpoena", re.I)),
)
_NUM = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}
_ORD = {w: i for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth".split(), 1)}
_STOP = set(
    "the and of for to on by in a an inc llc corp co ltd lp dba plaintiff plaintiffs defendant defendants "
    "cross complainant complainants set no number responses response request requests".split()
)


def kind_of(name: str) -> str | None:
    """The section a set's name says it is, or None when it does not say."""
    for key, rx in _KIND:
        if rx.search(name or ""):
            return key
    return None


def set_number(name: str) -> int | None:
    s = (name or "").lower()
    m = re.search(r"\bset\s*(?:no\.?\s*)?(\d+|[a-z]+)\b", s)
    if m:
        w = m.group(1)
        return int(w) if w.isdigit() else _NUM.get(w)
    m = re.search(r"\b(" + "|".join(_ORD) + r")\s+set\b", s)
    return _ORD[m.group(1)] if m else None


def _words(s: Any) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", str(s or "").lower()) if w not in _STOP}


def _plausible(row: dict[str, Any], party: str, direction: str, e: dict[str, Any]) -> bool:
    if str(e.get("direction") or "") != direction:
        return False
    a, b = set_number(str(row.get("set") or "")), set_number(str(e.get("item") or ""))
    if a is not None and b is not None and a != b:
        return False
    mine, theirs = _words(party), _words(e.get("party")) | _words(e.get("item"))
    return not mine or not _words(e.get("party")) or bool(mine & theirs)


def _source(v: dict[str, Any]) -> dict[str, Any]:
    s = v.get("source")
    return copy.deepcopy(s) if isinstance(s, dict) and (s.get("name") or s.get("file_id")) else dict(NO_PAPER)


def entry(row: dict[str, Any], direction: str, party: str) -> dict[str, Any]:
    """A status-list set in the breakdown's entry shape."""
    rs = row.get("responses_served") if isinstance(row.get("responses_served"), dict) else {}
    served = {"date": row.get("date"), "source": _source(row), "from_status": True} if row.get("date") else {}
    response: dict[str, Any] = {}
    if rs.get("value") or rs.get("date"):
        response = {"value": str(rs.get("value") or ""), "date": rs.get("date"), "from_status": True}
        if rs.get("date"):
            response["source"] = _source(rs)
    return {
        "item": str(row.get("set") or ""),
        "direction": direction,
        "party": str(party or ""),
        "served": served,
        "due": {},
        "response": response,
        "extension": {},
        "notes": STATUS_NOTE,
        "from_status": True,
    }


def _score(row: dict[str, Any], party: str, e: dict[str, Any]) -> tuple[int, int]:
    a, b = set_number(str(row.get("set") or "")), set_number(str(e.get("item") or ""))
    same_set = 2 if a is not None and a == b else (1 if a is None or b is None else 0)
    return same_set, len(_words(party) & (_words(e.get("party")) | _words(e.get("item"))))


def _fill(row: dict[str, Any], party: str, cands: list[dict[str, Any]]) -> int:
    """Fill the served date of the best-matching candidates when they are ONE
    set (the same set number and party: the breakdown sometimes prints a set
    twice); two different sets tie and nothing is filled. Returns how many."""
    best = max(_score(row, party, e) for e in cands)
    top = [e for e in cands if _score(row, party, e) == best]
    if len({(set_number(str(e.get("item") or "")), frozenset(_words(e.get("party")))) for e in top}) != 1:
        return 0
    filled = 0
    for e in top:
        served = e.get("served") if isinstance(e.get("served"), dict) else {}
        if served.get("date") or served.get("unconfirmed"):
            continue
        e["served"] = {"date": row["date"], "source": _source(row), "from_status": True}
        e["notes"] = "; ".join(x for x in (str(e.get("notes") or ""), FILLED_NOTE) if x)
        filled += 1
    return filled


def merge(prior: dict[str, Any], d12: dict[str, Any]) -> dict[str, Any]:
    """The case's twelve sections completed from ``prior`` (the status list's
    values); returns a new dict with ``sections``, ``status_rows`` (sets with no
    section, or every set of a case not read) and ``completed`` counts."""
    out = copy.deepcopy(d12)
    secs: dict[str, list[dict[str, Any]]] = out.setdefault("sections", {}) or {}
    out["sections"] = secs
    loose: list[dict[str, Any]] = []
    n = {"filled": 0, "added": 0, "unplaced": 0}
    readable = bool(out.get("read")) and not out.get("declined")
    for field, direction, who in _DIRS:
        for row in prior.get(field) or []:
            if not isinstance(row, dict):
                continue
            party = str(row.get(who) or "")
            key = kind_of(str(row.get("set") or ""))
            if not readable or key is None:
                loose.append(entry(row, direction, party))
                n["unplaced"] += 1
                continue
            got = secs.setdefault(key, [])
            cands = [e for e in got if _plausible(row, party, direction, e)]
            if not cands:
                got.append(entry(row, direction, party))
                n["added"] += 1
                continue
            if row.get("date"):
                n["filled"] += _fill(row, party, cands)
    out["status_rows"] = loose
    out["completed"] = n
    return out
