"""The review's findings as the attorney reads them in the Review Notes file.

``proof.review.notes_section`` lists every SHOULD-FIX and NOTE as one flat
bullet run, each led by its grade. On Pamplona (2026-10-25) that was 172
bullets: the attorney had to read all of them to find the 53 places the
records disagree.

This module groups the same findings by what the attorney would do with them,
most actionable first, and within a group puts the SHOULD-FIX items before the
NOTEs. Findings with the same text and citation are listed once. Nothing is
left out and nothing is rewritten: each bullet is the finding as the review,
or the settlement that disclosed a deletion (``review_settle``), wrote it. A
settled wording finding is that disclosure, so it is always kept.
"""

from __future__ import annotations

from typing import Any

from ..proof import review

#: (heading, predicate on the lower-cased check label), in reading order.
#: The first match wins; the last group takes everything else.
GROUPS: tuple[tuple[str, Any], ...] = (
    ("Findings the review graded must-fix, released by SMD", None),
    ("Places where the records disagree", lambda c: c.startswith("2")),
    ("Statements that may be about another person", lambda c: c.startswith("1")),
    ("Summary lines to check against the entries", lambda c: c.startswith("4")),
    ("Statements that may repeat", lambda c: c.startswith("5") and ("dup" in c or "repeat" in c)),
    ("Text that may have been read imperfectly", lambda c: c.startswith("5") and "garbl" in c),
    ("Wording changed or to review", lambda c: c.startswith("3")),
    ("Other observations", lambda c: True),
)
FIRST = "Worth checking first: "


def _flat(s: str) -> str:
    return " ".join(s.lower().split())


def _bullet(f: dict[str, Any]) -> str:
    lead = FIRST if f["grade"] == review.SHOULD else ""
    return f"- {lead}{f['finding']}" + (f" ({f['cite']})" if f.get("cite") else "")


def lines(rec: dict[str, Any] | None) -> list[str]:
    """The notes lines: every shown finding once, grouped."""
    if not rec:
        return []
    keep = (review.MUST, review.SHOULD, review.NOTE) if rec.get("released") else (review.SHOULD, review.NOTE)
    seen: set[tuple[str, str]] = set()
    groups: dict[str, list[dict[str, Any]]] = {h: [] for h, _ in GROUPS}
    for f in rec.get("findings") or []:
        if f["grade"] not in keep:
            continue
        key = (_flat(str(f["finding"])), _flat(str(f.get("cite") or "")))
        if key in seen:
            continue
        seen.add(key)
        c = str(f.get("check") or "").strip().lower()
        head = GROUPS[0][0] if f["grade"] == review.MUST else next(h for h, p in GROUPS[1:] if p(c))
        groups[head].append(f)
    out: list[str] = []
    for head, rows in groups.items():
        if not rows:
            continue
        rows.sort(key=lambda f: f["grade"] != review.SHOULD)  # stable: the review's order within a grade
        out += ["", f"## {head}", ""] + [_bullet(f) for f in rows]
    return out
