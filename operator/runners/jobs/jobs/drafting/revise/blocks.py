"""A filed response document edited as a list of paragraphs per response, and
the proof that nothing else moved.

The phrase-at-a-sentence edit (``responses.apply``) can only change words
inside a paragraph. The firm's rules also add an objection as a paragraph of
its own, drop a sentence until its paragraph is empty, and clear a marker that
was the whole paragraph. So a revision here is, per response, its new list of
paragraphs, and the Index of Exhibits' changed lines; :func:`build` writes them
into the document's markdown, and ``inplace.edit_content`` inserts, rewrites
and removes the paragraphs in the filed Word file (a new paragraph is a clone
of its neighbour, so it carries the response's own style).

:func:`prove` is ``set_units.revise_one``'s proof for that wider edit: the XML
unchanged beyond the paragraphs rewritten; the responses numbered as before;
the responses whose words changed a subset of those the plan names; words
outside every response unchanged, except the index lines when an index plan
exists; and no kind of format finding the filed file did not already have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import format_check
from . import layout as lay, responses as rs, set_units as su


def build(doc: rs.Document, bodies: dict[str, list[str]], index_edits: dict[int, str] | None = None) -> str:
    """The document's markdown with each named response's paragraphs replaced
    by ``bodies[number]`` (its heading's own words stay as filed unless the
    response is headed inline, when the first paragraph is the heading's) and
    each index block in ``index_edits`` rewritten."""
    edits = dict(index_edits or {})
    by = doc.by_number()
    replace: dict[int, list[str]] = {}
    drop: set[int] = set()
    for number, new in bodies.items():
        r = by[number]
        own = r.editable
        if not own:
            continue
        replace[own[0]] = [b for b in new if b.strip()]
        drop |= set(own[1:])
        if r.inline and (not replace[own[0]] or not rs.plain(replace[own[0]][0])):
            raise ValueError(f"response {number}: an inline heading cannot lose its words")
    out: list[str] = []
    for i, b in enumerate(doc.blocks):
        if i in drop:
            continue
        if i in replace:
            out += replace[i]
            continue
        out.append(edits.get(i, b))
    return "\n\n".join(out) + "\n"


def _outside(d: rs.Document, skip_index: bool) -> list[str]:
    mine = {i for r in d.responses for i in [r.head, *r.body]}
    if skip_index and d.index is not None:
        mine |= set(d.index.blocks)
    return [rs.plain(x) for i, x in enumerate(d.blocks) if i not in mine]


def changed(before: str, after: str, set_type: str | None, index_ok: bool) -> tuple[list[str], list[str]]:
    """(the responses whose words differ, what outside them differs)."""
    a, b = rs.read(before, set_type), rs.read(after, set_type)
    an, bn = a.by_number(), b.by_number()
    if list(an) != list(bn):
        return sorted(set(an) ^ set(bn)), ["the responses no longer number as filed"]
    diff = [n for n in an if rs.plain(a.text_of(an[n])) != rs.plain(b.text_of(bn[n]))]
    why = [] if _outside(a, index_ok) == _outside(b, index_ok) else ["words outside the responses changed"]
    if not index_ok and a.index_lines() != b.index_lines():
        why.append("the index changed with no index plan")
    return diff, why


def prove(
    base_md: str,
    filed: Path,
    new_md: str,
    out: Path,
    allowed: set[str],
    *,
    index_ok: bool,
    cls: str,
    fmt: dict[str, Any] | None,
    set_type: str | None = None,
) -> dict[str, Any]:
    """``new_md`` made in the filed file at ``out``, proven; ``su.Refused`` if not."""
    rec: dict[str, Any] = {"changed": [], "out": None}
    if new_md == base_md:
        return rec
    ed, diffs = su.edit_file(filed, new_md, out)
    if diffs:
        raise su.Refused("revise_layout_lost", "the revision changes what no rule names: " + "; ".join(diffs)[:300])
    numbers, outside = changed(base_md, new_md, set_type, index_ok)
    if outside or not set(numbers) <= allowed:
        raise su.Refused(
            "revise_layout_lost", f"{'; '.join(outside) or 'responses no rule names changed'} ({numbers})"[:300]
        )
    if fmt is not None:
        paper = str(lay.read(filed)["paper"])
        had = {su.finding_kind(f) for f in format_check.check(filed, cls, fmt, paper=paper).fails}
        new = [f for f in format_check.check(out, cls, fmt, paper=paper).fails if su.finding_kind(f) not in had]
        if new:
            raise su.Refused("format_check", "the revision brings in: " + "; ".join(new)[:300])
    rec.update(changed=numbers, out=str(out), touched=len(ed.touched), added=len(ed.added), removed=ed.removed)
    return rec


__all__ = ["build", "changed", "prove"]
