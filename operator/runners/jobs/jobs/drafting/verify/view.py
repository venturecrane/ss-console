"""How the check reads the drafter's markup: sections, sentences, tokens, and
each sentence as a checker sees it.

The check runs over the MARKED-UP text (before render), so every sentence
still names the fact ids behind it. A checker is shown the sentence with each
token replaced by what it will print plus its id (``preview``): a quote shows
the excerpt's own words, a figure its parsed value. Ids are labels a control
can swap (``label``), so a planted control looks like any other item.

The token grammar is ``evidence.markup``'s; it is read here with its own small
pattern so the check depends only on ``evidence.shapes``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .. import howell
from ..evidence.shapes import Fact, Ledger

TOKEN = re.compile(r"\{\{\s*(F|Q|N|D|SUM|COUNT|SPAN|T|A|L)\s*:([^{}]*)\}\}")
_PASS = re.compile(r"\{\{\s*(?:FILL|ATTORNEY)\s*:.*?\}\}", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
_ABBREV = re.compile(
    r"(?i)\b(?:Dr|Mr|Mrs|Ms|Jr|Sr|St|No|Nos|Inc|Co|Corp|Ltd|v|vs|Cal|App|Civ|Proc|Evid|Code|Supp|Rptr|"
    r"U\.S|e\.g|i\.e|etc|al|Ex|Exh|Dep|Fig|approx|Hon|Ct|Gov|Ins|Veh|Pen|Bus|Prof)\.$"
)
FACT_KINDS = ("F", "Q", "N", "D", "SUM", "COUNT", "SPAN", "A")


@dataclass(frozen=True)
class Tok:
    kind: str
    ids: tuple[str, ...]  # fact ids, or for L the slug
    text: str | None
    start: int
    end: int
    raw: str


def tokens(text: str) -> list[Tok]:
    out = []
    for m in TOKEN.finditer(text):
        kind, args = m.group(1), m.group(2)
        head, sep, rest = args.partition("|")
        free = rest.strip() if sep else None
        h = head.strip()
        if kind in ("T",):
            ids: tuple[str, ...] = ()
            free = h
        elif h == "?":
            ids = ()
        else:
            ids = tuple(i.strip() for i in h.split(",") if i.strip())
        out.append(Tok(kind, ids, free, m.start(), m.end(), m.group(0)))
    return out


def fact_ids(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for t in tokens(text):
        if t.kind in FACT_KINDS:
            seen += [i for i in t.ids if i not in seen]
    return tuple(seen)


def authority_refs(text: str) -> list[tuple[str, str]]:
    """(slug, citation) for each L token: ``("", citation)`` for ``{{L:?|..}}``."""
    return [((t.ids[0] if t.ids else ""), (t.text or "")) for t in tokens(text) if t.kind == "L"]


def sections(markup: str) -> list[tuple[str, str]]:
    """(heading, body) in order; text before the first heading is ``""``."""
    out: list[tuple[str, str]] = []
    marks = list(_HEADING.finditer(markup))
    if not marks or marks[0].start() > 0:
        head = markup[: marks[0].start()] if marks else markup
        if head.strip():
            out.append(("", head))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(markup)
        out.append((m.group(2).strip(), markup[m.end() : end]))
    return out


def _masked(text: str) -> str:
    """Tokens and markers blanked, so a boundary is never found inside one."""
    chars = list(text)
    for rx in (TOKEN, _PASS):
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                if chars[i] != "\n":
                    chars[i] = "x"
    return "".join(chars)


def sentences(body: str) -> list[str]:
    """The body's sentences (and table rows, list items) as written."""
    masked = _masked(body)
    out: list[str] = []
    start = 0
    i = 0
    n = len(body)
    while i < n:
        c = masked[i]
        if c == "\n" and (i + 1 < n and masked[i + 1] == "\n" or _line_item(masked, i + 1)):
            _push(out, body[start:i])
            start = i + 1
        elif c in ".!?":
            j = i + 1
            while j < n and masked[j] in "\"'”’)]":
                j += 1
            if j >= n or masked[j].isspace():
                word = masked[max(0, i - 12) : i + 1].split()[-1:] or [""]
                k = j
                while k < n and masked[k].isspace():
                    k += 1
                nxt = masked[k] if k < n else ""
                if not _ABBREV.search(word[0]) and (not nxt or nxt.isupper() or nxt in '"“(x[{#-*0123456789'):
                    _push(out, body[start:j])
                    start = j
                    i = j
                    continue
        i += 1
    _push(out, body[start:])
    return out


def _line_item(masked: str, i: int) -> bool:
    return bool(re.match(r"[ \t]*(?:[-*+]|\d+\.|\|)\s", masked[i : i + 8]))


def _push(out: list[str], s: str) -> None:
    s = s.strip()
    if s and re.search(r"[A-Za-z]", s):
        out.append(s)


# ---- what a checker sees ---------------------------------------------------------------


def money(v: Any) -> str:
    try:
        return howell.fmt(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        return str(v)


def fact_value(f: Fact) -> str:
    if f.value is None:
        return f.excerpt[:120]
    if f.unit == "usd":
        return money(f.value)
    if f.unit == "pct":
        return f"{f.value}%"
    return str(f.value)


def preview(
    sentence: str,
    ledger: Ledger,
    store: Any = None,
    label: Mapping[str, str] | None = None,
) -> str:
    """``sentence`` with every token shown as what it prints plus its id.
    ``label`` renames ids in what is shown (a control's swap); the printed
    words are always the original facts'."""
    lab = dict(label or {})

    def name(ids: tuple[str, ...]) -> str:
        return ", ".join(lab.get(i, i) for i in ids)

    out: list[str] = []
    cursor = 0
    for t in tokens(sentence):
        out.append(sentence[cursor : t.start])
        out.append(_piece(t, ledger, store, name))
        cursor = t.end
    out.append(sentence[cursor:])
    return " ".join("".join(out).split())


def _piece(t: Tok, ledger: Ledger, store: Any, name: Any) -> str:
    fs = [f for f in (ledger.get(i) for i in t.ids) if f is not None]
    if t.kind == "F":
        return f"[{name(t.ids)}]"
    if t.kind == "Q":
        words = t.text if t.text else (fs[0].excerpt if fs else "")
        return f'"{words}" [{name(t.ids)}]'
    if t.kind in ("N", "D"):
        return f"{fact_value(fs[0]) if fs else '?'} [{name(t.ids)}]"
    if t.kind == "SUM":
        try:
            total = sum((Decimal(str(f.value)) for f in fs), Decimal("0"))
            shown = money(total) if fs and fs[0].unit == "usd" else f"{total.normalize():f}"
        except (InvalidOperation, ValueError):
            shown = "?"
        return f"{shown} [sum of {name(t.ids)}]"
    if t.kind == "COUNT":
        return f"{len(fs)} [count of {name(t.ids)}]"
    if t.kind == "SPAN":
        return f"[the time between {name(t.ids)}]"
    if t.kind == "T":
        return "[the charges table]"
    if t.kind == "A":
        return f"[attorney marker: {t.text}]" if not t.ids else f"[attorney to confirm {name(t.ids)}]"
    if t.kind == "L":
        if not t.ids:
            return f"{t.text} [no primary text on file]"
        cite = store.citation(t.ids[0]) if store is not None and hasattr(store, "citation") else None
        return f"{cite or t.ids[0]} [L:{t.ids[0]}]"
    return t.raw


def terms(text: str, limit: int = 8) -> list[str]:
    """Distinctive words of a sentence (tokens dropped) for cutting a long
    authority to the passages it is about."""
    plain = TOKEN.sub(" ", text)
    words = re.findall(r"[A-Za-z][A-Za-z'-]{4,}", plain)
    seen: list[str] = []
    for w in sorted(words, key=len, reverse=True):
        if w.lower() not in seen:
            seen.append(w.lower())
    return seen[:limit]
