"""The fact index the drafter composes from, built by code.

One line per usable fact: its id, kind (and a figure's role), where it is,
the claim, the verified excerpt, and any parsed value. An unconfirmed fact
(``inference``) is marked so the drafter names it only through ``{{A:id}}``.
Rejected facts are never shown.

The same facts are written as a digest-compatible text too (``digest``), so
the lane's existing readers (review, repair scoping, notes) keep a
``digest.md`` to read, with every line's cite at page level.

Over the budget, code trims, and logs every id it trimmed: first the
excerpts shorten, then plain facts go from the end of the largest documents,
then other kinds. Figures, dates and testimony go last.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .render import cite_parts, date_text, figure_text
from .shapes import Fact, Ledger

#: Excerpt characters an index line shows before the budget forces it shorter.
EXCERPT_SHOWN = 300
EXCERPT_TRIMMED = 140
#: Trim order: the lower the rank, the sooner a fact goes when over budget.
_RANK = {"fact": 0, "quote": 1, "inference": 1, "testimony": 2, "date": 3, "figure": 3}


@dataclass(frozen=True)
class Index:
    text: str
    digest: str
    kept: tuple[str, ...]
    trimmed: tuple[str, ...]


def _value(f: Fact) -> str:
    if f.kind == "figure" and f.value is not None:
        return f" | value={figure_text(f)}"
    if f.kind == "date" and f.value is not None:
        return f" | date={date_text(f.value)}"
    return ""


def _kind(f: Fact) -> str:
    if f.kind == "figure":
        return f"figure:{f.role or 'other'}"
    if f.kind == "inference":
        return "UNCONFIRMED (name it only with {{A:" + f.id + "}})"
    return f.kind


def _excerpt(f: Fact, limit: int) -> str:
    e = f.excerpt if len(f.excerpt) <= limit else f.excerpt[: limit - 3].rstrip() + "..."
    return e.replace("\n", " ")


def line(f: Fact, limit: int = EXCERPT_SHOWN) -> str:
    where = cite_parts([f])
    extra = f" | read from the page image: {f.image_reading}" if f.image_reading else ""
    return f"{f.id} | {_kind(f)} | {where} | {f.claim} | excerpt: {_excerpt(f, limit)}{_value(f)}{extra}"


def _text(facts: list[Fact], limit: int) -> str:
    by: dict[str, list[Fact]] = {}
    for f in facts:
        by.setdefault(f.file, []).append(f)
    out = []
    for name, fs in by.items():
        out.append(f"## {name}")
        out += [line(f, limit) for f in fs]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def digest(facts: list[Fact]) -> str:
    """The facts as a cited digest: one block per document, one bullet per
    fact, each cited at page level."""
    by: dict[str, list[Fact]] = {}
    for f in facts:
        by.setdefault(f.file, []).append(f)
    out = []
    for k, (name, fs) in enumerate(by.items(), start=1):
        out.append(f"## {k} | {name} | {len(fs)} fact{'s' if len(fs) != 1 else ''}")
        for f in fs:
            val = _value(f).replace(" | ", "; ") if _value(f) else ""
            out.append(f'- [{f.id}] {f.claim}{val} ({cite_parts([f])}): "{_excerpt(f, EXCERPT_SHOWN)}"')
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build(ledger: Ledger, budget_chars: int | None = None, log: Callable[[str], None] | None = None) -> Index:
    """The index (and digest) of the ledger's usable facts, inside the budget."""
    facts = ledger.usable()
    limit = EXCERPT_SHOWN
    text = _text(facts, limit)
    trimmed: list[str] = []
    if budget_chars is not None and len(text) > budget_chars:
        limit = EXCERPT_TRIMMED
        text = _text(facts, limit)
        if len(text) > budget_chars:
            sizes: dict[str, int] = {}
            for f in facts:
                sizes[f.file] = sizes.get(f.file, 0) + 1
            pos = {f.id: i for i, f in enumerate(facts)}
            order = sorted(facts, key=lambda f: (_RANK.get(f.kind, 0), -sizes[f.file], -pos[f.id]))
            keep = {f.id for f in facts}
            over = len(text) - budget_chars
            for f in order:
                if over <= 0:
                    break
                keep.discard(f.id)
                trimmed.append(f.id)
                over -= len(line(f, limit)) + 1
            facts = [f for f in facts if f.id in keep]
            text = _text(facts, limit)
        if log is not None:
            log(
                f"  evidence index: {len(text):,} characters against a {budget_chars:,} budget; excerpts shortened to "
                f"{limit}; {len(trimmed)} fact(s) trimmed" + (f": {', '.join(trimmed)}" if trimmed else "")
            )
    return Index(text, digest(facts), tuple(f.id for f in facts), tuple(trimmed))
