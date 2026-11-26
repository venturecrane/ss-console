"""A propounded revision assembled (``propounded.py`` holds the ops): each
set's ops made in order, then every OTHER file's citations of a set that was
renumbered rewritten in the same job, so the files filed together agree.

A citation code cannot carry holds the whole job, never a stale number:
another file of the same party citing a renumbered set's request it cannot
read ("Request No. 7" in an interrogatory set), a range whose inside would no
longer be one range, or two sets of one kind and party a citation could mean.
Sets served on a different party never protect or rewrite each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import render, set_items
from .propounded import ADD_TYPES, Held, Op, Oracle, Refused, Unit, add, add_count, capitalize, cut, cut_drop
from .propounded import declaration, define, references


@dataclass
class Result:
    md: str
    before: int
    after: int
    #: What changed, a line each, in the attorney's words.
    lines: list[str] = field(default_factory=list)
    #: What was asked of this set and left, with why.
    left: list[str] = field(default_factory=list)
    #: Every kept request's number before the revision to its number after.
    numbers: dict[int, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.lines)

    @property
    def moved(self) -> bool:
        return any(b != a for b, a in self.numbers.items())

    def to_dict(self) -> dict[str, Any]:
        return {"before": self.before, "after": self.after, "lines": self.lines, "left": self.left}


def _span(nums: list[int]) -> str:
    if len(nums) > 2 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"Nos. {nums[0]} to {nums[-1]}"
    return ("Nos. " if len(nums) > 1 else "No. ") + ", ".join(str(n) for n in nums)


def _same_party(a: Unit, b: Unit) -> bool | None:
    """Whether two sets are served on one party; None when either party is unread."""
    if not a.party or not b.party:
        return None
    return render._norm(a.party) == render._norm(b.party)


def _define(md: str, ops: list[Op], oracle: Oracle, res: Result) -> tuple[str, list[str]]:
    terms: list[str] = []
    for op in (o for o in ops if o.op == "define"):
        term = op.term.upper()
        new, why = define(md, term, oracle.definition(term, op.quote))
        if why:
            res.left.append(why)
            continue
        md = new
        terms.append(term)
        res.lines.append(f'Added the definition of "{term}".')
    return md, terms


def _add(md: str, unit: Unit, ops: list[Op], oracle: Oracle, res: Result) -> str:
    """Each add op its own answer: two asks of one set never share one (``unit#op`` key)."""
    v = unit.set_type
    plural, one = set_items.NAMES[v][1], set_items.NAMES[v][0]
    for i, op in enumerate(ops):
        if op.op != "add":
            continue
        if v not in ADD_TYPES:
            res.left.append(f'"{op.quote}": requests are added to special interrogatories only; left as written')
            continue
        n = add_count(op.number_quote, set_items.count(md, v))
        if not n:
            res.left.append(f'"{op.quote}": the set already has that many; nothing was added')
            continue
        md, added = add(md, v, oracle.additions(f"{unit.name}#{i}", md, v, n, op.quote)[:n])
        if added:
            res.lines.append(f"Added {len(added)} {plural if len(added) > 1 else one}, {_span(added)}.")
    return md


def _cut(md: str, unit: Unit, ops: list[Op], oracle: Oracle, protected: set[int], res: Result) -> str:
    v = unit.set_type
    for i, op in enumerate(ops):
        if op.op != "cut":
            continue
        have = set_items.count(md, v)
        drop = cut_drop(op.number_quote, have)
        if drop is None:
            raise Held("revise_scope_unclear", f'"{op.quote}": code cannot read how many to remove; nothing was filed')
        if not drop:
            res.left.append(f'"{op.quote}": the set already has {have}; nothing was removed')
            continue
        new, dropped = cut(md, v, oracle.ranking(f"{unit.name}#{i}", md, v, drop, protected), drop, protected)
        if len(dropped) < drop:
            raise Refused("revise_unchecked", f"{unit.name}: only {len(dropped)} of {drop} requests could be removed")
        md = new
        res.lines.append(f"Removed former {_span(sorted(dropped))}.")
    return md


def _renumber(md: str, unit: Unit, start: int, res: Result) -> str:
    v = unit.set_type
    r = set_items.renumber(md, v, start, wide=True)
    if r.unresolved:
        raise Held(
            "revise_scope_unclear",
            f"{unit.name} cites No. {', '.join(map(str, r.unresolved))} of its own {set_items.NAMES[v][1]} in a "
            "form the new numbering cannot carry; nothing was filed",
        )
    if r.changed and set_items.unparsed(r.doc, v):
        raise Held(
            "revise_scope_unclear",
            f"{unit.name} is renumbered and cites a request in a form code cannot read "
            f'("{set_items.unparsed(r.doc, v)[0]}"); nothing was filed',
        )
    res.numbers = dict(zip(r.before, r.after))
    res.lines += [f"{ln}." for ln in set_items.numbers_note(r, v)]
    return r.doc


def revise_unit(
    md: str, unit: Unit, ops: list[Op], oracle: Oracle, foreign: dict[int, list[str]] | None = None
) -> Result:
    """``ops`` made in one filed set, in order: define, add, cut, renumber,
    capitals, the declaration's count. ``foreign``: this set's numbers other
    files cite, with their names; never cut (``revise_all`` rewrites them)."""
    v = unit.set_type
    res = Result(md, unit.count, unit.count)
    start = set_items.read(md, v)[0].number  # a set whose first request is cut still begins here
    md, terms = _define(md, ops, oracle, res)
    md = _add(md, unit, ops, oracle, res)
    md = _cut(md, unit, ops, oracle, set(foreign or {}), res)
    md = _renumber(md, unit, start, res)
    if terms:
        md = capitalize(md, terms)
    res.after = set_items.count(md, v)
    md, decl = declaration(md, res.before, res.after, v)
    if decl:
        res.lines.append(decl[0].upper() + decl[1:] + ".")
    res.md = md
    return res


def foreign_refs(units: list[Unit], mds: list[str], k: int) -> dict[int, list[str]]:
    """Unit ``k``'s numbers the OTHER kinds of set served on its party cite
    ("Request for Admission No. 3" in a special interrogatory), with the citing
    files' names. A file whose party is unread protects, to be safe."""
    v, out = units[k].set_type, {}
    for j, (u, md) in enumerate(zip(units, mds)):
        if j == k or u.set_type == v or not md or _same_party(u, units[k]) is False:
            continue
        for num in references(md, v, own=False):
            out.setdefault(num, []).append(u.name)
    return out


def revise_all(
    units: list[Unit], mds: list[str], ops: list[Op], scoped: list[list[int]], oracle: Oracle
) -> dict[int, Result]:
    """Every set's ops, then every other file's citations of a renumbered set
    rewritten: the results by unit index, a file changed only by its
    citations included. Raises ``Held`` or ``Refused`` as ``revise_unit``."""
    out: dict[int, Result] = {}
    for k in sorted({k for ks in scoped for k in ks}):
        mine = [o for o, ks in zip(ops, scoped) if k in ks]
        out[k] = revise_unit(mds[k], units[k], mine, oracle, foreign_refs(units, mds, k))
    for k, res in list(out.items()):
        if res.moved:
            _rewrite_citations(units, mds, out, k)
    return out


def _rewrite_citations(units: list[Unit], mds: list[str], out: dict[int, Result], k: int) -> None:
    v, res = units[k].set_type, out[k]
    for j, u in enumerate(units):
        if j == k or u.set_type == v:
            continue
        md = out[j].md if j in out else mds[j]
        cites = references(md, v, own=False)
        if not cites:
            if u.supported and set_items.unparsed(md, u.set_type) and _same_party(u, units[k]) is not False:
                raise Held(
                    "revise_scope_unclear",
                    f"{units[k].name} is renumbered and {u.name} cites a request in a form code cannot read "
                    f'("{set_items.unparsed(md, u.set_type)[0]}"); nothing was filed',
                )
            continue
        same = _same_party(u, units[k])
        if same is False:
            continue
        twins = [x for x, w in enumerate(units) if w.set_type == v and _same_party(w, u) is not False]
        if same is None or len(twins) > 1:
            raise Held(
                "revise_scope_unclear",
                f"{u.name} cites {set_items.NAMES[v][1]} and code cannot tell which filed set it means; "
                "nothing was filed",
            )
        new, left = set_items.remap(md, v, res.numbers, own=False)
        if left:
            raise Held(
                "revise_scope_unclear",
                f"{u.name} cites {set_items.NAMES[v][0]} No. {', '.join(map(str, left))} in a form the new "
                "numbering cannot carry; nothing was filed",
            )
        if new == md:
            continue
        moved = sorted((b, a) for b, a in res.numbers.items() if b != a and b in cites)
        line = (
            f"Its citations of {units[k].name} follow the new numbering ("
            + ", ".join(f"No. {b} is now No. {a}" for b, a in moved)
            + ")."
        )
        if j in out:
            out[j].md = new
            out[j].lines.append(line)
        else:
            out[j] = Result(new, u.count, u.count, [line], numbers={})


def summary(name: str, res: Result, variant: str) -> list[str]:
    """The reply's lines for one file, written by code from what was done."""
    lines = []
    if res.changed:
        plural = set_items.NAMES[variant][1]
        count = f" ({res.before} {plural}, now {res.after})" if res.before != res.after else ""
        lines.append(f"{name}{count}:")
        lines += [f"- {ln}" for ln in res.lines]
    lines += [f"- {name}: {why}" for why in res.left]
    return lines


__all__ = ["Result", "foreign_refs", "revise_all", "revise_unit", "summary"]
