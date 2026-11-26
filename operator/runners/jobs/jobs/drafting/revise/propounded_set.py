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
from .propounded import FOLLOW_UP, define, follow_ups, references
from .propounded_decl import decl_block, declaration
from .propounded_define import spelled_in, term_form


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
    #: The define asks (their quotes) this set does not use: left, and held
    #: only when no set in their scope uses them (``revise_all``).
    unused: list[str] = field(default_factory=list)

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
    """Each define op whose term this set uses; a set that never uses it is
    left as filed (``revise_all`` holds when no set uses it)."""
    terms: list[str] = []
    for op in (o for o in ops if o.op == "define"):
        term, plural = term_form(md, op.term)
        if not spelled_in(term, md):
            res.left.append(f'it does not use "{term}", so no definition was added to it')
            res.unused.append(op.quote)
            continue
        new, why = define(md, term, oracle.definition(term, op.quote), plural)
        if why:
            res.left.append(why)
            continue
        md = new
        terms.append(term)
        res.lines.insert(len(terms) - 1, f'Added the definition of "{term}".')  # the reply leads with them
    return md, terms


def _add(md: str, unit: Unit, ops: list[Op], oracle: Oracle, res: Result) -> str:
    """Each add op its own answer: two asks of one set never share one (``unit#op`` key)."""
    v = unit.set_type
    plural, one = set_items.NAMES[v][1], set_items.NAMES[v][0]
    for i, op in enumerate(ops):
        if op.op != "add" or FOLLOW_UP.search(op.quote):
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


@dataclass
class _Numbers:
    """Each original request's number now (``cur``), and the numbers now of
    each original request's follow-ups (``kids``), carried through every pass."""

    cur: dict[int, int]
    kids: dict[int, list[int]] = field(default_factory=dict)

    def carry(self, moved: dict[int, int], parents: dict[int, int] | None = None) -> None:
        for fresh, parent in (parents or {}).items():
            orig = next((o for o, c in self.cur.items() if c == parent), None)
            if orig is not None:
                self.kids.setdefault(orig, []).append(fresh)
        self.cur = {o: moved[c] for o, c in self.cur.items() if c in moved}
        self.kids = {o: sorted(moved[c] for c in cs if c in moved) for o, cs in self.kids.items()}
        self.kids = {o: cs for o, cs in self.kids.items() if cs}

    def families(self) -> dict[int, list[int]]:
        return {self.cur[o]: cs for o, cs in self.kids.items() if o in self.cur}


def _pass(md: str, unit: Unit, start: int) -> tuple[str, dict[int, int]]:
    """One renumbering, its own citations with it, the declaration aside
    (``declaration`` carries its lists): (the document, each label's number before to after)."""
    v = unit.set_type
    block = decl_block(md, v)
    md, decl = (md[: block[0]], md[block[0] :]) if block else (md, "")
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
    return r.doc + decl, dict(zip(r.before, r.after))


def _follow(md: str, unit: Unit, ops: list[Op], oracle: Oracle, start: int, nums: _Numbers) -> str:
    """Each follow-up ask: the model's follow-ups, each keyed to a request, put
    directly after it, then the set numbered again; any number of them when the
    email names none, the declaration's count holding a crossing."""
    v = unit.set_type
    for i, op in enumerate(ops):
        if op.op != "add" or not FOLLOW_UP.search(op.quote):
            continue
        n = add_count(op.number_quote, set_items.count(md, v)) if op.number_quote else None
        if n == 0:
            continue
        md, parents = follow_ups(md, v, oracle.follow_ups(f"{unit.name}#{i}", md, v, n, op.quote))
        md, moved = _pass(md, unit, start)
        nums.carry(moved, parents)
    return md


def _cut(md: str, unit: Unit, ops: list[Op], oracle: Oracle, protected: set[int], nums: _Numbers, res: Result) -> str:
    """Each cut, after the follow-ups: a request goes with its follow-ups, and
    the target counts them, as SMD's own cuts do."""
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
        fams = nums.families()
        mine = {c for o, c in nums.cur.items() if o in protected}
        ranking = oracle.ranking(f"{unit.name}#{i}", md, v, drop, mine, fams)
        new, dropped = cut(md, v, ranking, drop, mine, fams)
        if len(dropped) < drop:
            raise Refused("revise_unchecked", f"{unit.name}: only {len(dropped)} of {drop} requests could be removed")
        md = new
        was = {c: o for o, c in nums.cur.items()}
        res.lines.append(f"Removed former {_span(sorted(was[d] for d in dropped if d in was))}.")
    return md


def _close(md: str, unit: Unit, start: int, nums: _Numbers, res: Result) -> str:
    v = unit.set_type
    md, moved = _pass(md, unit, start)
    nums.carry(moved)
    res.numbers = dict(nums.cur)
    new = sorted(c for cs in nums.kids.values() for c in cs)
    if new:
        plural, one = set_items.NAMES[v][1], set_items.NAMES[v][0]
        res.lines.append(
            f"Added {len(new)} follow-up {plural if len(new) > 1 else one}, each after the one it follows "
            f"({_span(new)})."
        )
    kept = sorted(nums.cur)
    note = set_items.Renumbered(md, kept, [nums.cur[o] for o in kept], {}, [])
    res.lines += [f"{ln}." for ln in set_items.numbers_note(note, v)]
    return md


def revise_unit(
    md: str, unit: Unit, ops: list[Op], oracle: Oracle, foreign: dict[int, list[str]] | None = None
) -> Result:
    """``ops`` made in one filed set, in order: add, follow-ups, define, cut,
    renumber, capitals, the declaration carried through. ``foreign``: this
    set's numbers other files cite, with their names; never cut
    (``revise_all`` rewrites them)."""
    v = unit.set_type
    res = Result(md, unit.count, unit.count)
    orig = [it.number for it in set_items.read(md, v)]
    start = orig[0]  # a set whose first request is cut still begins here
    md = _add(md, unit, ops, oracle, res)
    nums = _Numbers({it.number: it.number for it in set_items.read(md, v)})  # the additions numbered on
    md = _follow(md, unit, ops, oracle, start, nums)
    md, terms = _define(md, ops, oracle, res)  # after the additions: a term only they use is used
    md = _cut(md, unit, ops, oracle, set(foreign or {}), nums, res)
    md = _close(md, unit, start, nums, res)
    if terms:
        md = capitalize(md, terms)
    res.after = set_items.count(md, v)
    if res.after != res.before or res.moved or nums.kids:
        md, decl = declaration(md, res.before, res.after, v, nums.cur, nums.kids, set(orig))
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
    for op, ks in zip(ops, scoped):
        if op.op == "define" and ks and all(op.quote in out[k].unused for k in ks):
            raise Held(
                "revise_scope_unclear",
                f'"{op.quote}": no filed set uses "{op.term.upper()}", so code cannot confirm the term; '
                "nothing was filed",
            )
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


def one_line(name: str, res: Result, variant: str, extra: str = "") -> str:
    """The reply's one line for one file: its name, its count when it changed,
    what was done and what was left, written by code (the broker lists these)."""
    plural = set_items.NAMES[variant][1]
    count = f" ({res.before} {plural}, now {res.after})" if res.changed and res.before != res.after else ""
    left = sentences(res.left)
    done = res.lines if res.changed else ["Left as filed."] if not left else []
    return f'"{name}"{count}: ' + " ".join([*done, *left, *([extra] if extra else [])])


def sentences(left: list[str]) -> list[str]:
    """Each reason a change was left, as a sentence."""
    return [w[:1].upper() + w[1:] + ("" if w.endswith(".") else ".") for w in left]


__all__ = ["Result", "foreign_refs", "one_line", "revise_all", "revise_unit", "sentences", "summary"]
