"""What an entry-read finding would write, the second reader's VERIFY of it,
and the code that decides. $0 here; the verify call is the stage's.

WHY (round 4, 2026-11-18). Three rounds of code comparing a blind value to
the proposed replacement kept letting faults through. The hand process that
proved itself on three real chronologies (131 of 133 findings confirmed by a
second reader, 86 of 86 corrected sentences supported, 10 of 10 planted
wrong values rejected) is a second reader shown the PAGES AS PICTURES, the
line as it stands and the proposed sentence in full, asked two questions:
is the line wrong, and is every statement of the proposed sentence on the
pages. That is this module; the token rules are gone.

* ``propose``: a finding becomes the WHOLE labelled line it would write (the
  reader's ``replacement`` spliced over its ``quote``, found exactly once in
  the line, the line's own citations kept byte for byte; a ``pointer`` swaps
  the line's one citation), or a new paragraph for an ``omitted`` finding,
  or a reason code it is recorded under without a verify.
* ``verify_request`` / ``parse_verify``: the second reader.
* ``decide``: apply only when the line is wrong AND the proposed sentence is
  supported AND the verify names a page it was shown; else recorded with a
  reason code. Nothing is ever removed.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..audit import claims as CL
from ..prompts import load
from . import review_pages as RV
from .entry_read import Images, Parsed, ReadFinding, Unit

PROMPT = "entry-verify-system"
VERIFY_PAGES = 8
RECORD_CLASSES = ("date", "provider", "another_person")
#: Reason codes a recorded finding carries (round 4).
NOT_WRONG, NOT_SUPPORTED, VERIFY_FAILED, VERIFY_UNPARSED = (
    "not_wrong",
    "not_supported",
    "verify_failed",
    "verify_unparsed",
)
QUOTE_NOT_ONCE, SAME_LINE, ADD_ON_HEADER = "quote_not_once", "same_line", "add_on_header"
#: Round 5: a pointer is never applied (moving a paragraph's one trailing
#: citation moves it for every sentence it covers); a [D] or [P] line is never
#: edited; the verify's page must be one the line cites; a verify with no
#: picture to look at is never sent.
POINTER_RECORDED, HEADER_LINE, PAGE_NOT_CITED, NO_PICTURE = (
    "pointer_recorded",
    "header_line",
    "page_not_cited",
    "no_picture",
)
CODES_IN_INDEX, DUPLICATE_ADD, PARTIAL_VIEW = "codes_in_index", "duplicate_add", "partial_view"
RECORD_ONLY_CLASS, PRIOR_HISTORY, ADD_CITATION = "record_only_class", "prior_history", "add_citation"
#: An ICD-10 code (letter, two digits, dot, one to four characters): never
#: written from the writer's index; an ordinary number never matches.
CODE = re.compile(r"\b[A-Z]\d{2}\.[0-9A-Z]{1,4}\b")


@dataclass
class Edit:
    """One applying change: ``swap`` a whole labelled line for `new`, or
    ``add`` `new` as its own paragraph after the paragraph holding `line`."""

    kind: str
    anchor: str  # the entry, N<k>
    line: str  # the labelled line's text as it stands
    new: str
    outcome: str = ""


@dataclass
class Proposal:
    finding: ReadFinding
    unit: Unit
    edit: Edit
    shown: list[tuple[int, int]] = field(default_factory=list)  # the pages its verify showed as pictures


@dataclass
class Verify:
    original_wrong: bool
    corrected_supported: bool
    exhibit: int | None
    page: int | None
    reason: str = ""


Decision = tuple[str, Any]  # ("apply", Edit) | ("record", reason code)


def _line_pages(line: str, unit: Unit) -> set[tuple[int, int]]:
    """The pages the line cites; a line with no citation of its own shares
    its paragraph's (the entry's claim that holds it)."""
    own = {(int(m.group(1)), p) for m in CL.CITE.finditer(line) for p in CL.parse_pages(m.group(2)) or [1]}
    if own or unit.entry is None:
        return own
    hit = [c for c in unit.entry.claims if line in c.text]
    return {(hit[0].exhibit, p) for p in hit[0].pages} if len(hit) == 1 else set()


_TAIL = re.compile(r"(?:\s*" + CL.CITE.pattern + r")+\s*$")


def _split_tail(s: str) -> tuple[str, str]:
    """(the sentence, its trailing citations with the space before them)."""
    m = _TAIL.search(s)
    return (s[: m.start()], s[m.start() :]) if m else (s, "")


def _swap(f: ReadFinding, line: str) -> str:
    """The line with the reader's replacement over its quote, citations kept
    byte for byte, and the line's closing . ! or ? kept when the splice
    dropped it (round 5)."""
    if line.count(f.quote) != 1 or not f.replacement.strip() or CL.CITE.search(f.replacement):
        return QUOTE_NOT_ONCE
    new = line.replace(f.quote, f.replacement, 1)
    cites = [m.group() for m in CL.CITE.finditer(line)]
    if [m.group() for m in CL.CITE.finditer(new)] != cites:
        return QUOTE_NOT_ONCE
    old_body, _ = _split_tail(line)
    body, tail = _split_tail(new)
    mark = old_body.rstrip()[-1:]
    if mark in (".", "!", "?") and body.strip() and body.rstrip()[-1:] not in (".", "!", "?"):
        new = body.rstrip() + mark + tail
    return new


def _add(f: ReadFinding, unit: Unit) -> str:
    if f.line in ("D", "P"):
        return ADD_ON_HEADER
    if CODE.search(CL.CITE.sub("", f.replacement)):
        return CODES_IN_INDEX
    cites = list(CL.CITE.finditer(f.replacement))
    pages = CL.parse_pages(cites[0].group(2)) if len(cites) == 1 else []
    if len(cites) != 1 or not pages or any((int(cites[0].group(1)), p) not in unit.cites() for p in pages):
        return ADD_CITATION  # one citation, to pages the entry already cites
    return f.replacement.strip()


def propose(f: ReadFinding, unit: Unit) -> Proposal | str:
    """The finding as the whole line (or paragraph) it would write, or the
    reason code it is recorded under without a verify."""
    if f.record_only or unit.record_only:
        return PRIOR_HISTORY
    if f.klass in RECORD_CLASSES:
        return RECORD_ONLY_CLASS
    if f.klass == "pointer":
        return POINTER_RECORDED
    if f.line in ("D", "P") and f.klass != "omitted":
        return HEADER_LINE
    line = unit.lines.get(f.line, "")
    if not line or unit.text.count(line) != 1:
        return QUOTE_NOT_ONCE
    if unit.sub and _line_pages(line, unit) - set(unit.pages):
        return PARTIAL_VIEW  # a one-page read cannot judge a line that cites more
    new = _add(f, unit) if f.klass == "omitted" else _swap(f, line)
    if new in (ADD_ON_HEADER, CODES_IN_INDEX, ADD_CITATION, QUOTE_NOT_ONCE):
        return new
    return Proposal(f, unit, Edit("add" if f.klass == "omitted" else "swap", unit.anchor, line, new))


def verify_pages(p: Proposal) -> list[tuple[int, int]]:
    """The pages the line cites and the finding's page, each with one page
    either side, inside what the read was shown: at most VERIFY_PAGES, the
    ones nearest the finding's page kept."""
    f = p.finding
    base = _line_pages(p.edit.line, p.unit) | {(f.exhibit, f.page)}
    near = {(x, q) for x, pg in base for q in (pg - 1, pg, pg + 1)} & set(p.unit.pages)
    ranked = sorted(near, key=lambda xp: (xp[0] != f.exhibit, abs(xp[1] - f.page), xp))
    return sorted(ranked[:VERIFY_PAGES])


def verify_request(p: Proposal, cfg: Any, client: str, images: Images) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, user content): the line as it stands, the proposed
    sentence in full, the class and the reader's problem, and the pages as
    PICTURES always (a second reader looks at the page itself)."""
    f, e = p.finding, p.edit
    asked = (
        f"CLASS: {f.klass}\nTHE FIRST READER SAYS: {f.problem or '(no reason given)'}\n\n"
        f"ENTRY LINE AS IT STANDS:\n{e.line}\n\n"
        + (
            f"PROPOSED NEW SENTENCE, to follow the line above:\n{e.new}"
            if e.kind == "add"
            else f"PROPOSED SENTENCE:\n{e.new}"
        )
    )
    out: list[dict[str, Any]] = [{"type": "text", "text": client}, {"type": "text", "text": asked}]
    p.shown = []
    for ex, pg in verify_pages(p):
        blocks = RV.page_blocks(ex, pg, "", images, None)
        if any(b.get("type") == "image" for b in blocks):  # a page with no picture is not shown
            p.shown.append((ex, pg))
        out += blocks
    return load(PROMPT, cfg), out


def cited_pages(p: Proposal) -> set[tuple[int, int]]:
    """The pages a verify may settle a proposal on: those the swapped line
    cites (its paragraph's when it has no citation of its own), or those the
    added sentence's own citation names."""
    if p.edit.kind == "add":
        return {(int(m.group(1)), q) for m in CL.CITE.finditer(p.edit.new) for q in CL.parse_pages(m.group(2)) or []}
    return _line_pages(p.edit.line, p.unit)


def _flag(v: object) -> bool | None:
    return v if isinstance(v, bool) else None


def parse_verify(reply: str) -> Verify | None:
    """The verify's answer, or None when it is not one JSON object with both
    flags (recorded ``verify_unparsed``)."""
    a, b = reply.find("{"), reply.rfind("}")
    try:
        obj = json.loads(reply[a : b + 1]) if 0 <= a < b else None
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    wrong, supported = _flag(obj.get("original_wrong")), _flag(obj.get("corrected_supported"))
    if wrong is None or supported is None:
        return None
    page = obj.get("page") if isinstance(obj.get("page"), dict) else {}
    ints = [
        v if isinstance(v, int) and not isinstance(v, bool) else None for v in (page.get("exhibit"), page.get("page"))
    ]
    return Verify(wrong, supported, ints[0], ints[1], str(obj.get("reason") or "")[:200])


def decide(p: Proposal, v: Verify | None) -> Decision:
    """Apply only on a verify that finds the line wrong, the proposed
    sentence supported, and names a page the line cites (``cited_pages``)
    that it was shown."""
    if v is None:
        return ("record", VERIFY_UNPARSED)
    if not v.original_wrong:
        return ("record", NOT_WRONG)
    if not v.corrected_supported:
        return ("record", NOT_SUPPORTED)
    if (v.exhibit, v.page) not in cited_pages(p):
        return ("record", PAGE_NOT_CITED)
    if (v.exhibit, v.page) not in (p.shown or verify_pages(p)):
        return ("record", NOT_SUPPORTED)
    return ("apply", p.edit)


def summary(units: list[Unit], parsed: list[Parsed], decisions: list[Decision], gaps: Any = ()) -> dict[str, Any]:
    """Plain counts for the run record; no text of the document."""
    found = [f for p in parsed for f in p.findings]
    gl = list(gaps)
    return {
        "units": len(units),
        "findings_by_class": dict(Counter(f.klass for f in found)),
        "dropped": dict(sum((p.dropped for p in parsed), Counter())),
        "over_cap": sum(p.over_cap for p in parsed),
        "decided": len(decisions),
        # an applying decision the write reverted (``breaks_structure``) is recorded too
        "recorded_by_reason": dict(
            Counter(
                d[1] if d[0] == "record" else d[1].outcome
                for d in decisions
                if d[0] == "record" or d[1].outcome not in ("applied", "")
            )  # fmt: skip
        ),
        "gaps_codes": sum(1 for g in gl if g.kind == "codes"),
        "gaps_no_entry": sum(1 for g in gl if g.kind == "no_entry"),
    }
