"""A propounded discovery set the Operator drafted and filed, revised on the
attorney's reply: three closed ops, every one decided by code.

WHY (2026-11-25). An attorney replied in a propounded set's thread ("define
VEHICLE", "add a few more special interrogatories about his phone", "cut the
requests for admission down to 30"). The set-response revision
(``set_run.py``) applies the firm's rules to RESPONSES; nothing revised the
sets the firm serves, so each such reply was SMD's hand work.

The model names values, code decides (Law 18):

* ONE plan call per job reads the email into ops, each
  ``{op: define|add|cut, set_types, party_quote, term, number_quote, quote}``.
  Code keeps an op only when its ``quote`` (and its party, term and number
  words) are the email's own words (:func:`verbatim`). Any ask that is none of
  the three (a question, a rewording) is ``other``: the job holds for SMD,
  never a partial revision and never a question to the attorney.
* Which sets (:func:`scope`): the set types the op's own words name
  (``set_units.named_set_types``), else the types the model named that the
  email names, else every set; then the responding party the op names, matched
  against each file's own RESPONDING PARTY. A scope no file matches holds.
* Numbers (:func:`parse_number`) are parsed by code from the op's number
  words: "120-125", "120 to 125", and a digit run larger than the set's count
  that splits into exactly one ascending pair, each no larger than the count
  ("120125"). The target is the top of a range. Unparseable holds; never a guess.
* Per set, in order: define (the model writes the definition once per job;
  code places it alphabetically in the Definitions and numbers them), add
  (special interrogatories only; the model writes the requests, code labels
  and numbers them, at most the email's number, else ``DEFAULT_ADD``), cut
  (the model ranks the requests least important first; code drops the first
  ones no kept request cites), renumber (``set_items.renumber``, its
  cross-references remapped and the unresolved ones named), the defined terms'
  capitals (``sets``), and the declaration's count (:func:`declaration`):
  only inside the declaration, this set's old count rewritten to the new;
  crossing 35 either way, or the old count not found exactly once, holds.

Pure over markdown and a model ``Oracle`` (the model's side, its prompts and
retries, is ``propounded_ask.py``): the job (``propounded_run.py``), the $0
rehearsal (``propounded_rehearse.py``) and the tests run the same code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .. import render, set_items, sets
from ..counts import STATUTORY_LIMIT
from .propounded_numbers import DEFAULT_ADD, add_count, crosses, cut_drop, parse_number
from .set_units import named_set_types

#: The set types this revision edits: the ones whose requests carry numbered labels.
VARIANTS = tuple(set_items.WORDS)
#: The types section 2030.030 / 2033.030 limits to 35 without a declaration.
LIMITED = frozenset({"special_interrogatories", "requests_for_admission"})
#: The types requests are added to (the approved design: special interrogatories).
ADD_TYPES = frozenset({"special_interrogatories"})
#: Requests added when the email names no number.
OPS = ("define", "add", "cut")
#: Every set type a filed unit may be (``drafting/job.py``); form interrogatories are a form, never edited here.
SET_TYPES = (*VARIANTS, "form_interrogatories")
_NAME = (
    (re.compile(r"special\s+interrogator|\bs\.?rogs?\b", re.I), "special_interrogatories"),
    (re.compile(r"form\s+interrogator|\bf\.?rogs?\b", re.I), "form_interrogatories"),
    (re.compile(r"production|\brfps?\b|\brpds?\b", re.I), "requests_for_production"),
    (re.compile(r"admission|\brfas?\b", re.I), "requests_for_admission"),
)


class Held(ValueError):
    """The file or the request needs SMD (``revise_scope_unclear``,
    ``revise_declaration``, ``revise_set_unsupported``) or nothing would change
    (``revise_unchanged``): held, nothing filed."""

    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}")
        self.code, self.why = code, why


class Refused(ValueError):
    """Our machinery: a base code cannot prove, or a model answer code refused
    after its retry (``revise_base_unproven``, ``revise_unchecked``)."""

    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}")
        self.code, self.why = code, why


# ---- the filed sets ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    name: str
    set_type: str
    #: The RESPONDING PARTY the file's own identification block names; None unread.
    party: str | None
    count: int
    #: A set this revision edits (special, admission, production); a form set is kept as filed.
    supported: bool = True


def type_from_name(name: str) -> str:
    """The set a file is, from its own name; "" when it names none or several."""
    hits = {kind for pat, kind in _NAME if pat.search(name)}
    return hits.pop() if len(hits) == 1 else ""


def read_unit(name: str, hint: str, md: str) -> Unit:
    """One filed file as a set: its type (the delivery's hint, else its name,
    else its labels), its responding party and its count. A file of a
    supported type whose labels read no numbered request (labels Word numbers
    itself read as nothing) is unproven: code cannot pin a request to its
    number, so nothing is edited."""
    kind = hint or type_from_name(name) or set_items.variant_of(md) or ""
    party = render.party(md, "RESPONDING PARTY")
    if kind == "form_interrogatories":
        return Unit(name, kind, party, 0, supported=False)
    if kind not in VARIANTS:
        raise Refused("revise_base_unproven", f"{name}: no kind of propounded set code can read")
    found = set_items.variant_of(md)
    if found is not None and found != kind:
        raise Refused("revise_base_unproven", f"{name} is delivered as {kind} and labeled as {found}")
    items = set_items.read(md, kind)
    if not items:
        raise Refused(
            "revise_base_unproven",
            f"{name}: no numbered {set_items.NAMES[kind][0]} label reads (Word may number them itself)",
        )
    nums = [it.number for it in items]
    if len(set(nums)) != len(nums):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        raise Refused("revise_base_unproven", f"{name}: two requests are numbered {dup[0]}")
    return Unit(name, kind, party, len(items))


# ---- the plan ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    op: str
    quote: str
    set_types: tuple[str, ...] = ()
    party_quote: str = ""
    term: str = ""
    number_quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "set_types": list(self.set_types)}


def _norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return " ".join(s.split()).casefold()


def verbatim(words: str, email: str) -> bool:
    """``words`` are the email's own (whitespace, curly quotes and case aside)."""
    w = _norm(words)
    return bool(w) and w in _norm(email)


def check_plan(raw: Any, email: str) -> tuple[list[Op], list[str], list[str]]:
    """(the ops code keeps, the other asks' words, the problems a retry is told)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ops", []), list):
        return [], [], ['the answer is not {"ops": [...], "other": [...]}']
    ops, other, problems = [], [], []
    for x in raw.get("other") or []:
        q = x.get("quote") if isinstance(x, dict) else x
        if isinstance(q, str) and q.strip():
            other.append(" ".join(q.split()))
    for i, x in enumerate(raw.get("ops") or []):
        if not isinstance(x, dict) or x.get("op") not in OPS:
            problems.append(f"op {i + 1}: op is one of {', '.join(OPS)}")
            continue
        words = {k: " ".join(str(x.get(k) or "").split()) for k in ("quote", "party_quote", "term", "number_quote")}
        kinds = x.get("set_types") or []
        if not isinstance(kinds, list) or not all(k in SET_TYPES for k in kinds):
            problems.append(f"op {i + 1}: set_types are from {', '.join(SET_TYPES)}")
            continue
        unseen = [k for k, v in words.items() if v and not verbatim(v, email)]
        if not words["quote"] or unseen:
            problems.append(f"op {i + 1}: {', '.join(unseen) or 'quote'} is not the email's exact words")
            continue
        if x["op"] == "define" and not words["term"]:
            problems.append(f"op {i + 1}: a define names its term")
            continue
        ops.append(
            Op(x["op"], words["quote"], tuple(kinds), words["party_quote"], words["term"], words["number_quote"])
        )
    return ops, other, problems


# ---- scope -------------------------------------------------------------------------------------------

_ROLE = re.compile(r"(?i)\b(cross-?)?(defendants?|plaintiffs?|respondents?|petitioners?)\b")


#: Words that name no one: "the sets served on the defendant" names only a role.
_STOP = frozenset(
    "a an and for her his of on our party parties responding served set sets their them to cross s".split()
)


def _tokens(s: str) -> set[str]:
    return set(render._norm(s).split()) - _STOP


def party_match(quote: str, party: str | None) -> bool:
    """The party words of the email name this file's responding party: every
    name word of the email's in the party's, or, when the email names only a
    role ("the defendant"), that role in the party's line."""
    if not party:
        return False
    words = _tokens(quote)
    if words:
        return words <= _tokens(party)
    roles = {m.group(2).rstrip("s").lower() for m in _ROLE.finditer(quote)}
    have = {m.group(2).rstrip("s").lower() for m in _ROLE.finditer(party)}
    return bool(roles) and roles <= have


def scope(op: Op, units: list[Unit], email: str) -> list[int]:
    """The units ``op`` changes. ``Held(revise_scope_unclear)`` when no file is
    the one it names: SMD's, never a question to the attorney."""
    kinds: set[str] | None = named_set_types(op.quote) or None
    if kinds is None and op.set_types:
        named = named_set_types(email)
        kinds = set(op.set_types) & named
        if not kinds:
            raise Held(
                "revise_scope_unclear",
                f'"{op.quote}": the sets it is about are not named in the email; nothing was changed',
            )
    ks = [k for k, u in enumerate(units) if u.supported and (kinds is None or u.set_type in kinds)]
    if kinds is None and op.op == "add":
        ks = [k for k in ks if units[k].set_type in ADD_TYPES]  # requests are added to these alone
    if op.party_quote:
        ks = [k for k in ks if party_match(op.party_quote, units[k].party)]
        if len({render._norm(units[k].party or "") for k in ks}) > 1:
            raise Held(
                "revise_scope_unclear",
                f'"{op.quote}": "{op.party_quote}" fits sets served on more than one party; nothing was changed',
            )
    if not ks:
        who = f" served on {op.party_quote}" if op.party_quote else ""
        raise Held("revise_scope_unclear", f'"{op.quote}": no filed set{who} is the one it names; nothing was changed')
    if kinds is None and op.op == "cut" and len({units[k].set_type for k in ks}) > 1:
        raise Held(
            "revise_scope_unclear",
            f'"{op.quote}": the email does not say which kind of set to cut, and the thread has more than one; '
            "nothing was changed",
        )
    return ks


# ---- numbers (propounded_numbers.py) -----------------------------------------------------------------
def check_numbers(ops: list[Op], scoped: list[list[int]], sets: list[Unit]) -> None:
    """Every number read and every count projected before any write call: a
    number code cannot read, or a set whose count would cross 35, holds now."""
    projected = {k: s.count for k, s in enumerate(sets)}
    for op, ks in zip(ops, scoped):
        for k in ks:
            if op.op == "add" and sets[k].set_type in ADD_TYPES:
                n = add_count(op.number_quote, projected[k])
                if n is None:
                    raise Held("revise_scope_unclear", f'"{op.quote}": code cannot read how many to add')
                projected[k] += n
    for op, ks in zip(ops, scoped):
        for k in ks:
            if op.op == "cut":
                drop = cut_drop(op.number_quote, projected[k])
                if drop is None:
                    raise Held(
                        "revise_scope_unclear", f'"{op.quote}": code cannot read how many to remove from {sets[k].name}'
                    )
                projected[k] -= drop
    for k, s in enumerate(sets):
        if s.set_type in LIMITED and crosses(s.count, projected[k]):
            raise Held(
                "revise_declaration",
                f"{s.name} would go from {s.count} to {projected[k]}, across {STATUTORY_LIMIT}, so whether it "
                "carries a declaration changes; nothing was filed",
            )


# ---- define ------------------------------------------------------------------------------------------

_DEF_LINE = re.compile(r"(?m)^([ \t]*)(\d+)(\.[ \t]+)[\"“]([A-Z][A-Z0-9&'./ -]*[A-Z0-9])[\"”]")


_BOLD_DEFS = re.compile(r"(?im)^\*\*\s*DEFINITIONS\b[^\n]*\*\*[ \t]*$")
#: The end of a bold-titled Definitions: the next bold-only paragraph (a title or a label) or heading.
_BOLD_END = re.compile(r"(?m)^(?:\*\*[^\n]*\*\*[ \t]*$|#{1,6}\s)")


def _bold_defs(md: str) -> tuple[int, str] | None:
    """A Definitions titled by a bold paragraph rather than a heading (a set
    filed by hand from the firm's own file reads this way): its body and offset."""
    m = _BOLD_DEFS.search(md)
    if not m:
        return None
    end = _BOLD_END.search(md, m.end())
    return m.end(), md[m.end() : end.start() if end else len(md)]


def _defs(md: str) -> tuple[int, int, list[tuple[int, int, int, str]]] | None:
    """The Definitions body (start, end) and its numbered definitions as
    (paragraph start, number start, number end, term), in order."""
    got = sets._defs_section(md) or _bold_defs(md)
    if got is None:
        return None
    base, body = got
    rows = [
        (base + m.start(), base + m.start(2), base + m.end(2), " ".join(m.group(4).split()))
        for m in _DEF_LINE.finditer(body)
    ]
    return base, base + len(body), rows


def definition_ok(term: str, text: str) -> str | None:
    """The definition as code writes it (one paragraph opening with the quoted
    term in capitals); None when the model's text does not open with it."""
    t = " ".join((text or "").split())
    m = re.match(r"^[\"“]\s*([^\"”]+?)\s*[\"”]\s*(\S.*)$", t)
    if not m or " ".join(m.group(1).split()).upper() != term.upper() or len(t) > 1500:
        return None
    return f'"{term.upper()}" {m.group(2)}'


def define(md: str, term: str, text: str) -> tuple[str, str | None]:
    """The definition placed alphabetically among the set's numbered
    definitions, the ones after it numbered on: (the document, None) or (the
    document unchanged, why it was left)."""
    term = term.upper()
    got = _defs(md)
    if got is None or not got[2]:
        return md, f'the definition of "{term}" was not added: the set has no numbered Definitions code can read'
    _start, _end, rows = got
    if any(t == term for *_x, t in rows):
        return md, f'"{term}" is already defined in it; left as written'
    nums = [int(md[a:b]) for _p, a, b, _t in rows]
    if nums != list(range(nums[0], nums[0] + len(nums))):
        return md, f'the definition of "{term}" was not added: its definitions are not numbered in order'
    at = next((i for i, (*_x, t) in enumerate(rows) if t.casefold() > term.casefold()), len(rows))
    out = md
    for i in range(len(rows) - 1, at - 1, -1):  # from the end, so earlier offsets hold
        _p, a, b, _t = rows[i]
        out = out[:a] + str(nums[i] + 1) + out[b:]
    para = f"{nums[0] + at}. {text}"
    if at < len(rows):
        p = rows[at][0]
        indent = re.match(r"[ \t]*", out[p:]).group(0)  # type: ignore[union-attr]
        out = out[:p] + indent + para + "\n\n" + out[p:]
    else:  # after the whole last definition, its (a) and (b) included: the Definitions' end
        end = len(out[: got[1]].rstrip())
        out = out[:end] + "\n\n" + para + out[end:]
    return out, None


def _caps_seg(seg: str, terms: list[str]) -> str:
    def fix(s: str) -> str:
        for t in terms:
            s = sets._term_pattern(t).sub(
                lambda m, s=s: (
                    m.group(0) if m.group(0) == m.group(0).upper() or sets._general(s, m) else m.group(0).upper()
                ),
                s,
            )
        return s

    return sets._outside(seg, fix)


def capitalize(md: str, terms: list[str]) -> str:
    """Each use of ``terms`` after the Definitions in capitals, as the firm's
    caps pass writes them (``sets.enforce_caps``), and no other term's."""
    at, end = sets._region(md)
    if at < 0 or not terms:
        return md
    return md[:at] + _caps_seg(md[at:end], sorted(terms, key=len, reverse=True)) + md[end:]


# ---- add ---------------------------------------------------------------------------------------------

_LEAD_LABEL = re.compile(
    r"(?i)^\W*(?:special\s+interrogatory|request\s+for\s+(?:admission|production(?:\s+of\s+documents)?))"
    r"\s+no\.?\s*\d+\W*"
)


def _label_style(md: str, variant: str) -> tuple[str, str, bool]:
    """(the text before the number, the text after it, whether the request's
    words follow on the label's own line), from the set's last label."""
    last = set_items.read(md, variant)[-1]
    line_end = md.find("\n", last.num_end)
    rest = md[last.num_end : len(md) if line_end < 0 else line_end]
    inline = re.match(r"^([\s*_)\]]*[:.][\s*_]*)[^\s*_]", rest)
    return md[last.start : last.num_start], (inline.group(1) if inline else rest), bool(inline)


def add(md: str, variant: str, texts: list[str]) -> tuple[str, list[int]]:
    """``texts`` as requests after the set's last, labeled as its last label
    is, numbered on: (the document, the numbers added)."""
    texts = [" ".join(_LEAD_LABEL.sub("", t).split()) for t in texts]
    texts = [t for t in texts if t]
    if not texts:
        return md, []
    before = set_items.count(md, variant)
    head, tail, inline = _label_style(md, variant)
    texts = [_caps_seg(t, sorted(sets.defined_terms(md), key=len, reverse=True)) for t in texts]
    first = set_items.read(md, variant)[-1].number + 1
    block = "\n\n".join(
        f"{head}{first + i}{tail}{t}" if inline else f"{head}{first + i}{tail}\n\n{t}" for i, t in enumerate(texts)
    )
    out, n = set_items.append(md, variant, block)
    after = set_items.read(out, variant)
    return out, [it.number for it in after[before : before + n]]


# ---- cut ---------------------------------------------------------------------------------------------


def references(md: str, variant: str, skip: Sequence[tuple[int, int]] = (), own: bool = True) -> set[int]:
    """The numbers of ``variant`` the document cites in its words (never a
    label's own number), outside the ``skip`` spans: short and plural forms
    and a range's inner numbers included (``set_items.wide_refs``). ``own``:
    the set's own kind, where a bare "Request No. 3" is one of its requests."""
    out: set[int] = set()
    for at, _end, nums in set_items.wide_refs(md, variant, own):
        if not any(a <= at < b for a, b in skip):
            out |= nums
    return out


def cut(md: str, variant: str, ranking: list[int], n: int, protected: set[int]) -> tuple[str, list[int]]:
    """The first ``n`` requests of ``ranking`` removed, skipping any one a
    request that stays cites or ``protected`` names: (the document, the
    numbers removed, in ranking order). Never renumbers (``set_items.renumber``)."""
    items = set_items.read(md, variant)
    by = {it.number: it for it in items}
    drop: list[int] = []
    for num in ranking:
        if len(drop) == n:
            break
        if num in drop or num not in by or num in protected:
            continue
        gone = [(by[d].start, by[d].end) for d in (*drop, num)]
        if num in references(md, variant, gone):
            continue
        drop.append(num)
    if not drop or len(drop) >= len(items):
        return md, []
    if items[-1].number in drop and _runs_on(md, items[-1]):
        raise Held(
            "revise_scope_unclear",
            f"the last request (No. {items[-1].number}) is to be removed and paragraphs follow it that code cannot "
            "tell are its own or the set's; nothing was filed",
        )
    keep = [it for it in items if it.number not in drop]
    head, tail = md[: items[0].start], md[items[-1].end :]
    return head + "\n\n".join(it.text(md).strip() for it in keep) + tail, drop


def _runs_on(md: str, last: set_items.Item) -> bool:
    """Words between the set's last request and its next heading or date line:
    the request's second paragraph, or the set's own closing; code cannot tell."""
    rest = md[last.end :]
    para = re.search(r"\S[\s\S]*?(?=\n\s*\n|\Z)", rest)
    return para is not None and not set_items._HEAD.match(para.group(0))


# ---- the declaration ---------------------------------------------------------------------------------


def decl_block(md: str, variant: str | None = None) -> tuple[int, int] | None:
    """The declaration for additional discovery: from its title line, after
    the set's last request (a caption that names the declaration is never it),
    to the proof of service or the end; None when the set carries none."""
    items = set_items.read(md, variant) if variant else []
    after = items[-1].end if items else 0
    pos = 0
    start = None
    for ln in md.splitlines(keepends=True):
        if start is None and pos >= after and render.DECL_LINE.search(ln) and len(ln.strip()) <= 160:
            start = pos
        elif start is not None and render.POS_LINE.search(ln):
            return start, pos
        pos += len(ln)
    return (start, len(md)) if start is not None else None


_SPELLED = re.compile(
    r"(?i)\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b"
)


def declaration(md: str, old: int, new: int, variant: str) -> tuple[str, str | None]:
    """This set's count rewritten in its declaration: (the document, the line
    for the reply, or None when nothing changed). ``Held(revise_declaration)``
    when the count crosses 35 either way, or the old count is not in the
    declaration exactly once: what the declaration should say is SMD's."""
    if variant not in LIMITED or old == new:
        return md, None
    if crosses(old, new):
        way = "over" if new > STATUTORY_LIMIT else "under"
        raise Held(
            "revise_declaration",
            f"the set goes from {old} to {new}, {way} {STATUTORY_LIMIT}, so whether it carries a declaration "
            "changes; nothing was filed",
        )
    block = decl_block(md, variant)
    if block is None:
        if new > STATUTORY_LIMIT:
            raise Held("revise_declaration", f"the set has {new} and no declaration code can find; nothing was filed")
        return md, None
    a, b = block
    hits = list(re.finditer(rf"(?<![\d.]){old}(?!\d|\.\d)", md[a:b]))
    if len(hits) != 1:
        raise Held(
            "revise_declaration",
            f"the declaration says {old} {len(hits)} times, not once, so code cannot tell which is this set's "
            "count; nothing was filed",
        )
    at = a + hits[0].start()
    near = md[max(a, at - 40) : at + len(str(old)) + 40]
    if _SPELLED.search(near):
        raise Held(
            "revise_declaration",
            f"the declaration writes this set's count in words beside {old}, which code does not rewrite; "
            "nothing was filed",
        )
    out = md[:at] + str(new) + md[at + len(str(old)) :]
    return out, f"the declaration's count of this set is now {new} (was {old})"


# ---- the model ----------------------------------------------------------------------------------------


class Oracle(Protocol):
    """The model's three values: a definition, requests to add, a ranking."""

    def definition(self, term: str, quote: str) -> str: ...

    def additions(self, unit: str, md: str, variant: str, n: int, quote: str) -> list[str]: ...

    def ranking(self, unit: str, md: str, variant: str, n: int, protected: set[int]) -> list[int]: ...


__all__ = [
    "ADD_TYPES",
    "DEFAULT_ADD",
    "LIMITED",
    "OPS",
    "VARIANTS",
    "Held",
    "Op",
    "Oracle",
    "Refused",
    "Unit",
    "add",
    "add_count",
    "capitalize",
    "check_numbers",
    "check_plan",
    "crosses",
    "cut",
    "cut_drop",
    "declaration",
    "define",
    "definition_ok",
    "parse_number",
    "party_match",
    "read_unit",
    "references",
    "scope",
    "type_from_name",
    "verbatim",
]
