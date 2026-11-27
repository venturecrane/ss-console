"""How many requests a propounded set carries, owned by code (design B, B1).

The firm's targets are an optional section of ``drafting-firm.yaml``::

    discovery_counts: {<variant>: {min?, max?}}

``<variant>`` is a propounded set's key (``sets.VARIANTS``); each target names
a minimum, a maximum or both, whole numbers. Absent, nothing here runs and
every set is drafted, numbered and filed as before. The statutory 35 stays a
constant in code, never config.

With targets authored, code owns the count, in four places a job calls:

1. ``instruction``: the unit's THIS DOCUMENT line carries the number, written
   here; the firm's compose prompt states none. A request that states its own
   number for a set ("about 40 RFAs") wins over the firm's: one small model
   call (``asked``) returns the set, the number and the words that state it,
   and code keeps it only if those words are in the email verbatim and carry
   those digits.
2. ``after_compose``: a set below its minimum gets one "more requests" pass
   (``extend.more``) before the audit, told the count and the target. Still
   short, it files as it is: never padded, never held.
3. ``at_gate``: the labels are numbered again in order and ``counts.json``
   records the count, the target, the request's own number and the top-up.
4. The declaration: ``declaration`` is the one decision ``render.attach``,
   ``format_check`` and the review share. A set over the maximum is never cut;
   the attorney notes say so (``render.set_sizes``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sets as sets_mod, set_items

KEY = "discovery_counts"
BOUNDS = ("min", "max")
#: The count over which a declaration is attached (Code Civ. Proc. 2030.050, 2033.050).
STATUTORY_LIMIT = 35
#: The declaration each set type needs over the limit (``attachments`` keys).
DECLARATIONS = {"special_interrogatories": "decl_2030_050", "requests_for_admission": "decl_2033_050"}
FILE = "counts.json"
ASKED = "counts-asked.json"
ASK_STAGE = "counts-ask"
ASK_MAX_TOKENS = 400
ASK_SYSTEM = (
    "You read an attorney's email asking for discovery sets. For each set the email states a number of requests "
    "for (a count, a target or a cap), return one object: "
    '{"set": one of ' + ", ".join(f'"{k}"' for k, _l, _p in sets_mod.VARIANTS) + ', "number": the number as '
    'digits, "quote": the email\'s own words that state it, copied exactly}. A number that is not a count of '
    "requests (a date, a matter number, a set number, a page) is not one. Output only a JSON array, [] when the "
    "email states no number of requests."
)
#: A number near a set's name: the only email the ask is spent on.
_NEAR = 60


def _whole(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate(body: object) -> list[str]:
    """The section's closed shape: each variant maps to ``{min?, max?}``, whole
    numbers, at least one bound, min not above max."""
    keys = [k for k, _label, _pat in sets_mod.VARIANTS]
    if not isinstance(body, dict) or not body:
        return [f"{KEY}: expected a map of {keys} to {{min?, max?}}"]
    out: list[str] = []
    for variant, target in body.items():
        where = f"{KEY}.{variant}"
        if variant not in keys:
            out.append(f"{where}: unknown set (expected one of {keys})")
            continue
        if not isinstance(target, dict) or not target or not set(target) <= set(BOUNDS):
            out.append(f"{where}: expected {{min?, max?}} with at least one")
            continue
        out += [
            f"{where}.{b}: expected a whole number above 0" for b in BOUNDS if b in target and not _whole(target[b])
        ]
        if _whole(target.get("min")) and _whole(target.get("max")) and target["min"] > target["max"]:
            out.append(f"{where}: min is above max")
    return out


def targets(firm_data: dict[str, Any] | None) -> dict[str, tuple[int | None, int | None]]:
    """The firm's (min, max) per variant; {} when it authored none."""
    body = (firm_data or {}).get(KEY)
    if body is None or validate(body):
        return {}
    return {v: (t.get("min"), t.get("max")) for v, t in body.items()}


def enabled(firm_data: dict[str, Any] | None) -> bool:
    """The firm authored ``discovery_counts``: the switch for everything here
    but the declaration, which its own form switches."""
    return bool(targets(firm_data))


@dataclass(frozen=True)
class Asked:
    """A number the request states for one set, in its own words."""

    number: int
    quote: str


@dataclass(frozen=True)
class Facts:
    """What code counted in one unit, and what it decided: the set's variant,
    its requests (every one counts, genuineness requests included), the firm's
    target, the proven prior requests to that party, and the attachment key of
    the declaration attached (``decl_2030_050`` or ``decl_2033_050``), or None.
    ``asked`` is the request's own number when it stated one; ``topped_up`` the
    count before and after the one "more requests" pass; ``numbering`` what
    renumbering changed, in the attorney's words."""

    variant: str
    count: int
    target: tuple[int | None, int | None] = (None, None)
    prior: int = 0
    declaration: str | None = None
    asked: Asked | None = None
    topped_up: tuple[int, int] | None = None
    numbering: tuple[str, ...] = field(default_factory=tuple)


def facts(unit: Any) -> Facts | None:
    """The counts code recorded for a unit (``counts.json``); None when nothing
    recorded them (no targets authored, or a set not yet gated)."""
    data = getattr(unit, "data", None)
    p = Path(data) / FILE if data is not None else None
    if p is None or not p.is_file():
        return None
    r = json.loads(p.read_text(encoding="utf-8"))
    a = r.get("asked")
    t = r.get("topped_up")
    return Facts(
        variant=str(r["variant"]),
        count=int(r["count"]),
        target=(r["target"][0], r["target"][1]),
        asked=Asked(int(a["number"]), str(a["quote"])) if a else None,
        topped_up=(int(t[0]), int(t[1])) if t else None,
        numbering=tuple(r.get("numbering") or ()),
    )


def declaration(variant: str, count: int, prior: int = 0) -> str | None:
    """The one decision ``render.attach``, ``format_check`` and ``review``
    share: the attachment key of the declaration a set needs, or None. A set of
    special interrogatories or of requests for admission needs its section's
    declaration when this set's requests plus the PROVEN prior requests of that
    kind to the same party exceed 35. Every request counts; no kind of request
    for admission is left out of the count."""
    key = DECLARATIONS.get(variant)
    if key is None or count <= 0 or count + max(prior, 0) <= STATUTORY_LIMIT:
        return None
    return key


def sizes_note(f: Facts, total: int, key: str | None, attached: bool) -> list[str]:
    """The attorney notes' "Set sizes" lines for one set: its count, the
    firm's range (or the request's own number), what was done when it fell
    outside it, and whether a declaration is attached and why. Then what
    renumbering changed."""
    plural = set_items.NAMES[f.variant][1]
    lo, hi = f.target
    line = f"Set sizes: {f.count} {plural}"
    if f.asked is not None:
        line += f'; your request asked for {f.asked.number} ("{f.asked.quote}")'
    elif lo and hi:
        line += f"; your range is {lo} to {hi}"
    elif hi:
        line += f"; your limit is {hi}"
    elif lo:
        line += f"; your minimum is {lo}"
    if lo and f.count < lo:
        added = f.topped_up[1] - f.topped_up[0] if f.topped_up else 0
        line += (f"; {added} more were drafted from the file" if added else "") + "; the file supported no more"
    if hi and f.count > hi:
        line += "; above that number, kept as drafted with none removed"
    if f.variant in DECLARATIONS:
        if attached:
            line += f"; declaration attached ({total} to this party, over {STATUTORY_LIMIT})"
        elif key:
            line += (
                f"; {total} to this party is over {STATUTORY_LIMIT}, but no declaration form for this set is on file"
            )
        else:
            line += f"; no declaration ({total} to this party, not over {STATUTORY_LIMIT})"
    return [line, *f.numbering]


# ---- the request's own number ---------------------------------------------------------


def _squash(s: str) -> str:
    return " ".join(s.split()).casefold()


def might_state(request: str) -> bool:
    """A number of one to three digits within a few words of a set's name: the
    only request the ask is spent on."""
    for _key, _label, pat in sets_mod.VARIANTS:
        for m in pat.finditer(request or ""):
            near = request[max(0, m.start() - _NEAR) : m.end() + _NEAR]
            if re.search(r"(?<![\d/.-])\d{1,3}(?![\d/.-])", near):
                return True
    return False


def keep(answer: str, request: str) -> dict[str, Asked]:
    """The model's answer, kept only where its quote is the email's own words
    and carries the number's digits; one number per set (the first)."""
    try:
        rows = json.loads(answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    except ValueError:
        return {}
    keys = {k for k, _l, _p in sets_mod.VARIANTS}
    email = _squash(request)
    out: dict[str, Asked] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        v, n, q = r.get("set"), r.get("number"), r.get("quote")
        if v not in keys or not isinstance(n, int) or not _whole(n) or not isinstance(q, str) or not q.strip():
            continue
        if _squash(q) in email and str(n) in re.findall(r"\d+", q) and v not in out:
            out[str(v)] = Asked(n, " ".join(q.split()))
    return out


def asked(run: Any) -> dict[str, Asked]:
    """The numbers the request states, per set: asked once per job and kept
    (``counts-asked.json``); {} with no targets authored or no number near a
    set's name in the email."""
    p = Path(run.data) / ASKED
    if p.is_file():
        got = json.loads(p.read_text(encoding="utf-8"))
        return {k: Asked(int(v["number"]), str(v["quote"])) for k, v in got.items()}
    request = str(run.job.request_text or "")
    if not enabled(run.firm.data) or not might_state(request):
        return {}
    r = run.doorway.call(
        ASK_STAGE,
        model=run.firm.model("transcription"),
        system=ASK_SYSTEM,
        messages=[{"role": "user", "content": "THE EMAIL:\n\n" + request}],
        max_tokens=ASK_MAX_TOKENS,
        effort="",
    )
    out = keep(str(r.text), request)
    tmp = p.with_name(f".{ASKED}.tmp")
    tmp.write_text(json.dumps({k: vars(a) for k, a in out.items()}, indent=1), encoding="utf-8")
    tmp.replace(p)
    return out


# ---- the four call sites -----------------------------------------------------------------


def target(run: Any, variant: str | None) -> tuple[tuple[int | None, int | None], Asked | None]:
    """The unit's (min, max) and the request's own number when it stated one,
    which wins: its number is both. ((None, None), None) with nothing set."""
    if run.cls != "discovery_set" or variant not in set_items.WORDS or not enabled(run.firm.data):
        return (None, None), None
    a = asked(run).get(variant)
    if a is not None:
        return (a.number, a.number), a
    return targets(run.firm.data).get(variant, (None, None)), None


def target_line(variant: str, bounds: tuple[int | None, int | None], a: Asked | None) -> str:
    """The THIS DOCUMENT line's number, in the drafter's words; "" with none."""
    lo, hi = bounds
    if variant not in set_items.NAMES or (a is None and not lo and not hi):
        return ""  # nothing to say, or a set with no set-size line (a form set)
    plural = set_items.NAMES[variant][1]
    never = "Never write a request the record does not support to reach a number; fewer is right when the record supports fewer."
    if a is not None:
        return f'SET SIZE: write {a.number} {plural}, the number the request states ("{a.quote}"). {never}'
    if lo and hi:
        return f"SET SIZE (the firm's range): write between {lo} and {hi} {plural}, as many as the record supports. {never}"
    if hi:
        return f"SET SIZE (the firm's limit): write no more than {hi} {plural}, as many as the record supports."
    if lo:
        return f"SET SIZE (the firm's minimum): write at least {lo} {plural} where the record supports them. {never}"
    return ""


def instruction(run: Any, variant: str | None, instr: str | None) -> str | None:
    """``finish.units``: the unit's instruction with the set's number written
    in; unchanged with no targets authored."""
    bounds, a = target(run, variant)
    line = target_line(variant, bounds, a) if variant else ""
    if not line:
        return instr
    return f"{instr} {line}" if instr else f"THIS DOCUMENT (set by the job): {line}"


def after_compose(run: Any, u: Any, drafter: Any, digest: str) -> None:
    """``finish._draft``, after the compose and before the audit: one "more
    requests" pass when the set is below its minimum. Once per unit."""
    from . import extend, render

    if run.cls != "discovery_set" or run._is_done(u.stage("topup")):
        return
    (lo, hi), _a = target(run, u.variant)
    first = Path(u.data) / "draft-v1.md"
    if not lo or not first.is_file():
        return
    doc, notes = render.split_notes(first.read_text(encoding="utf-8"))
    before = set_items.count(doc, u.variant)
    added = 0
    if 0 < before < lo:
        doc, added = extend.more(drafter, doc, u.variant, before, (lo, hi), digest)
        if added:
            tmp = first.with_name(".draft-v1.md.tmp")
            tmp.write_text(doc + ("\n" + render.NOTES_MARK + "\n\n" + notes + "\n" if notes else ""), encoding="utf-8")
            tmp.replace(first)
        run.log(f"  set size: {before} of a minimum {lo}; {added} more written")
    run._done(u.stage("topup"), before=before, after=before + added)


def at_gate(run: Any, u: Any, doc: str) -> str:
    """``finish._gate``: the labels numbered again in order and ``counts.json``
    written. Unchanged with no targets authored."""
    if run.cls != "discovery_set" or not enabled(run.firm.data):
        return doc
    variant = u.variant or set_items.variant_of(doc)
    if variant not in set_items.WORDS:
        return doc
    r = set_items.renumber(doc, variant)
    bounds, a = target(run, variant)
    top = run._state().get(u.stage("topup")) or {}
    added = int(top.get("after", 0)) - int(top.get("before", 0))
    p = Path(u.data) / FILE
    # A set gated again (a format repair) is already in order: keep what the first pass changed.
    earlier = json.loads(p.read_text(encoding="utf-8")).get("numbering") if p.is_file() else None
    rec = {
        "variant": variant,
        "count": set_items.count(r.doc, variant),
        "target": list(bounds),
        "asked": vars(a) if a else None,
        "topped_up": [top["before"], top["after"]] if added > 0 else None,
        "numbering": set_items.numbers_note(r, variant) or earlier or [],
    }
    tmp = p.with_name(f".{FILE}.tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)
    return r.doc
