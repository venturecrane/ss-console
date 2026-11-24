"""A set revision scored against its answer key, response by response.

WHY (2026-11-23). The rehearsal said only whether the responses a revision
changed were the responses the key changed. A response changed in the wrong
words counted as right, and the number of responses the key changed that the
lane never touched was not counted at all, so "10 of 68" could not be read off
any report. This scores each response of one document three ways, base (as
filed), out (as the lane revised it) and key (as revised by hand):

* ``made``: the lane changed it.
* ``correct``: the lane changed it and its words now equal the key's
  (:func:`norm`: as a reader sees them, spacing and curly quotes folded).
* ``missed``: the key changed it and the lane's words are not the key's
  (not made at all, or made differently; ``wrong`` counts the second).
* ``extra``: the lane changed it and the key did not.

The Index of Exhibits is scored the same way, line by line: a line the key
added or removed is a change, and a change the lane made is correct when the
key made the same one.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import responses as rs

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
#: Every count a score carries, in the order a report prints them.
COUNTS = ("made", "correct", "missed", "extra", "wrong")


def norm(text: str) -> str:
    return " ".join(rs.plain(text).translate(_QUOTES).split())


def _texts(doc: rs.Document) -> dict[str, str]:
    return {r.number: norm(doc.text_of(r)) for r in doc.responses}


def _status(b: str | None, o: str | None, k: str | None) -> str:
    made, keyed = o != b, k != b
    if made and o == k:
        return "correct"
    if made and keyed:
        return "wrong"
    if made:
        return "extra"
    return "missed" if keyed else "unchanged"


def _tally(statuses: list[str]) -> dict[str, int]:
    c = Counter(statuses)
    return {
        "made": c["correct"] + c["wrong"] + c["extra"],
        "correct": c["correct"],
        "missed": c["missed"] + c["wrong"],
        "extra": c["extra"],
        "wrong": c["wrong"],
    }


def responses(base: rs.Document, out: rs.Document, key: rs.Document) -> dict[str, Any]:
    """Each response's status and the document's counts."""
    b, o, k = _texts(base), _texts(out), _texts(key)
    numbers = list(dict.fromkeys([*b, *k, *o]))
    per = {n: _status(b.get(n), o.get(n), k.get(n)) for n in numbers}
    return {"responses": {n: s for n, s in per.items() if s != "unchanged"}, **_tally(list(per.values()))}


def _changes(before: list[str], after: list[str]) -> Counter[tuple[str, str]]:
    a, b = Counter(before), Counter(after)
    return Counter({("removed", line): n for line, n in (a - b).items()}) + Counter(
        {("added", line): n for line, n in (b - a).items()}
    )


def index(base: rs.Document, out: rs.Document, key: rs.Document) -> dict[str, Any]:
    """The index's lines scored as changes: the lane's against the key's."""
    lines = [[norm(x) for x in d.index_lines()] for d in (base, out, key)]
    made, keyed = _changes(lines[0], lines[1]), _changes(lines[0], lines[2])
    correct = made & keyed
    return {
        "made": sum(made.values()),
        "correct": sum(correct.values()),
        "missed": sum((keyed - correct).values()),
        "extra": sum((made - correct).values()),
        "wrong": 0,
        "key_lines": [f"{how}: {line}" for (how, line), _n in keyed.items()],
    }


def totals(scores: list[dict[str, Any]]) -> dict[str, int]:
    return {c: sum(int(s.get(c) or 0) for s in scores) for c in COUNTS}


# ---- per kind of change (A2) ----------------------------------------------------------------
#: What one response's change is made of, read from its sentences.
KINDS = (
    "objection_added",
    "objection_removed",
    "disposition",
    "marker_cleared",
    "sentence_added",
    "sentence_removed",
)
_SENT = re.compile(r"(?<=[.;])\s+(?=[A-Z{(])")


_MARKER = re.compile(r"\{\{(?:[^{}]|\{\{[^{}]*\}\})*\}\}")


def _sentences(text: str) -> list[str]:
    """The response's sentences with every marker taken out: a marker cleared
    is its own kind, never a sentence added."""
    return [s for s in (x.strip() for x in _SENT.split(norm(_MARKER.sub(" ", text)))) if s]


def change_kinds(before: str, after: str, book: Any = None) -> set[str]:
    """The kinds of change from ``before`` to ``after`` of one response. With
    the firm's phrase book, a sentence it holds is an objection or a
    disposition; without it, every sentence is plain added or removed."""
    out: set[str] = set()
    if after.count("{{") < before.count("{{"):
        out.add("marker_cleared")
    a, b = Counter(_sentences(before)), Counter(_sentences(after))
    for s, how in [*((s, "added") for s in b - a), *((s, "removed") for s in a - b)]:
        if "{{" in s:
            continue
        key = book.find(s) if book is not None else None
        kind = book.entries[key].kind if key else ""
        if kind == "objection":
            out.add(f"objection_{how}")
        elif kind == "disposition":
            out.add("disposition")
        else:
            out.add(f"sentence_{how}")
    return out


def kinds(base: rs.Document, out: rs.Document, key: rs.Document, book: Any = None) -> dict[str, dict[str, int]]:
    """Per kind: ``correct`` (the key made it and the lane's response equals
    the key's), ``missed`` (the key made it, the lane's response is not the
    key's), ``extra`` (the lane made it, the key did not)."""
    b, o, k = ({r.number: d.text_of(r) for r in d.responses} for d in (base, out, key))
    tally: dict[str, Counter[str]] = {kind: Counter() for kind in KINDS}
    for n in b:
        want = change_kinds(b[n], k.get(n, b[n]), book)
        got = change_kinds(b[n], o.get(n, b[n]), book)
        right = norm(o.get(n, b[n])) == norm(k.get(n, b[n]))
        for kind in want:
            tally[kind]["correct" if right and kind in got else "missed"] += 1
        for kind in got - want:
            tally[kind]["extra"] += 1
    return {kind: {c: t[c] for c in ("correct", "missed", "extra")} for kind, t in tally.items()}


__all__ = ["COUNTS", "KINDS", "change_kinds", "index", "kinds", "norm", "responses", "totals"]
