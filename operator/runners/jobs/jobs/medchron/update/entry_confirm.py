"""The second, BLIND read of each entry-read finding, and the code that
decides what is applied. $0 here; the paid call is the stage's.

The confirm is shown at most CONFIRM_PAGES pages (the quoted sentence's cited
pages and the finding's page, one either side) and asked ONE question about
the item, never the proposed replacement (except an added sentence, which it
is shown and asked only whether the pages state it). ``decide`` is pure code
(tightened in the fix round of 2026-11-18):

* date, provider and another_person findings, and every finding on a
  record-only unit, are recorded, never applied (a re-dated entry would
  leave the summary tables built before the audit stale);
* value / wrong_field / unsupported: applied only when the blind read names
  the finding's page and its value agrees (``_agrees``: what the replacement
  adds is in it, its numbers are in the corrected sentence, its polarity and
  units match), the replacement drops no other content (``drops_content``),
  and on a text page the page's own layer carries every number, 4+ letter
  word and negation it adds;
* pointer: only a one-page citation moved to one page the blind read names
  (else ``pointer_multi``);
* omitted: applied only on YES on the finding's page, a page the entry
  already cites, when no other entry carries that page and those values,
  and never a sentence carrying a diagnosis code (``codes_in_index``);
* anything else (NOT_STATED, CANNOT_READ, an unparsed confirm, any
  disagreement) is recorded. Nothing is ever removed.

An applied decision is a ``review_repair.Finding`` that ``review_repair.check``
and ``review_repair.apply`` take unchanged: a ``replace`` on the claim the
quote sits in, or an ``add`` on the entry.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..audit import claims as CL
from ..prompts import load
from . import review_pages as RV, review_repair as RR
from .entry_read import Images, Parsed, ReadFinding, Unit
from .values import NUM, norm_number, numbers, words

PROMPT = "entry-confirm-system"
STATED, NOT_STATED, CANNOT_READ, YES, NO, UNPARSED = "STATED", "NOT_STATED", "CANNOT_READ", "YES", "NO", "UNPARSED"
ANSWERS = (STATED, NOT_STATED, CANNOT_READ, YES, NO)
VALUE_CLASSES = ("value", "wrong_field", "unsupported")
RECORD_CLASSES = ("date", "provider", "another_person")
BLANK = "____"
_WORD4 = re.compile(r"[a-z]{4,}")


@dataclass
class Confirm:
    answer: str
    exhibit: int | None = None
    page: int | None = None
    value: str = ""


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (s or "").lower()).split())


def _uncite(s: str) -> str:
    return " ".join(CL.CITE.sub("", s).split())


def _blanked(quote: str, replacement: str) -> str:
    """The item as the entry words it, the disputed part blanked: the words
    the replacement does not keep, else every number."""
    keep = set(_norm(replacement).split())
    toks = quote.split()
    out = [BLANK if _norm(t) and not set(_norm(t).split()) <= keep else t for t in toks]
    if out == toks:
        return NUM.sub(BLANK, quote)
    return " ".join(out)


def question(f: ReadFinding) -> str:
    if f.klass == "pointer":
        return (
            f'On which of these pages is the following content stated?\nCONTENT: "{_uncite(f.quote)}"\n'
            'Answer {"answer": "STATED", "exhibit": E, "page": P} for the page that states it, or NOT_STATED.'
        )
    if f.klass == "omitted":
        return (
            f'Is every statement in this sentence stated on these pages?\nSENTENCE: "{_uncite(f.replacement)}"\n'
            'Answer {"answer": "YES", "exhibit": E, "page": P} with the page that states it, or {"answer": "NO"}.'
        )
    return (
        "What do these pages state about the following item? The part in question is shown as "
        f'{BLANK}.\nITEM: "{_uncite(_blanked(f.quote, f.replacement))}"\n'
        'Answer {"answer": "STATED", "exhibit": E, "page": P, "value": "<the exact value or wording as printed>"}, '
        "or NOT_STATED, or CANNOT_READ."
    )


def confirm_request(
    finding: ReadFinding, unit: Unit, cfg: Any, client: str, images: Images, modes: RV.Modes | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, user content) for one finding's blind confirm: the
    pages of ``confirm_pages``, unlabelled, and one question. The entry and
    the proposed replacement are not shown (an added sentence is)."""
    out: list[dict[str, Any]] = [{"type": "text", "text": client}, {"type": "text", "text": question(finding)}]
    for ex, pg in confirm_pages(finding, unit):
        out += RV.page_blocks(ex, pg, "", images, modes)
    return load(PROMPT, cfg), out


#: The pages one confirm shows at most.
CONFIRM_PAGES = 8


def confirm_pages(f: ReadFinding, unit: Unit) -> list[tuple[int, int]]:
    """The pages the quoted sentence cites and the finding's page, each with
    one page either side, inside what the read was shown: at most
    CONFIRM_PAGES, the ones nearest the finding's page kept."""
    base = {(f.exhibit, f.page)}
    claim = _claim(f, unit)
    if not isinstance(claim, str):
        base |= {(claim.exhibit, p) for p in claim.pages}
    near = {(x, q) for x, p in base for q in (p - 1, p, p + 1)} & set(unit.pages)
    ranked = sorted(near, key=lambda xp: (xp[0] != f.exhibit, abs(xp[1] - f.page), xp))
    return sorted(ranked[:CONFIRM_PAGES])


def parse_confirm(reply: str) -> Confirm:
    """The confirm's answer; anything that does not parse is UNPARSED (recorded)."""
    a, b = reply.find("{"), reply.rfind("}")
    try:
        obj = json.loads(reply[a : b + 1]) if 0 <= a < b else None
    except json.JSONDecodeError:
        obj = None
    ans = str(obj.get("answer") or "").strip().upper() if isinstance(obj, dict) else ""
    if not isinstance(obj, dict) or ans not in ANSWERS:
        return Confirm(UNPARSED)
    v = obj.get("value")
    return Confirm(ans, RR._int(obj.get("exhibit")), RR._int(obj.get("page")), v if isinstance(v, str) else "")


# ---- decide -------------------------------------------------------------------------
Decision = tuple[str, Any]  # ("apply", RR.Finding) | ("record", reason)
#: Words that turn a statement into its opposite; a change of them is checked
#: like a changed value (fix round 2026-11-18).
NEGATIONS = frozenset({"no", "not", "denies", "deny", "denied", "without", "negative", "none"})
#: An ICD-10 code as the writer's INDEX states one: a letter, two digits, a
#: dot and one to four characters, found inside a sentence (round 3: an
#: ordinary number such as a temperature or a vertebra never matches).
CODE = re.compile(r"\b[A-Z]\d{2}\.[0-9A-Z]{1,4}\b")  # never "100.4" or "T12"
CODES_IN_INDEX, DROPS_CONTENT, POINTER_MULTI = "codes_in_index", "drops_content", "pointer_multi"


def _record(why: str) -> Decision:
    return ("record", why)


def _same_page(f: ReadFinding, c: Confirm) -> bool:
    return c.page == f.page and c.exhibit in (None, f.exhibit)


def _blind_agrees(f: ReadFinding, c: Confirm | None) -> bool:
    """A blind read that names the finding's page and states a value."""
    return c is not None and c.answer == STATED and _same_page(f, c) and bool(c.value.strip())


def _claim(f: ReadFinding, unit: Unit) -> RR.Claim | str:
    """The cited paragraph the finding's labelled line sits in (round 3: the
    line is the handle, not a free quote)."""
    line = unit.lines.get(f.line, "")
    if not line or line.count(f.quote) != 1:
        return "the quote is not once in its line"
    hits = [c for c in (unit.entry.claims if unit.entry else []) if line in c.text]
    if len(hits) != 1 or hits[0].text.count(line) != 1:
        return "the line is not in exactly one cited sentence of the entry"
    return hits[0]


def _cite_of(f: ReadFinding, unit: Unit, claim: RR.Claim) -> re.Match[str]:
    """The line's own last citation, else its paragraph's."""
    own = list(CL.CITE.finditer(unit.lines.get(f.line, "")))
    return own[-1] if own else list(CL.CITE.finditer(claim.text))[-1]


def edited_line(f: ReadFinding, unit: Unit) -> str:
    """The finding's line as a value or pointer edit leaves it."""
    line = unit.lines[f.line]
    if f.klass == "pointer":
        m = list(CL.CITE.finditer(line))[-1]
        return f"{line[: m.start()]}(Exhibit {f.exhibit} - p. {f.page}){line[m.end() :]}"
    return line.replace(f.quote, f.replacement, 1)


def _edit(f: ReadFinding, anchor: str, action: str, corrected: str) -> RR.Finding:
    return RR.Finding(
        RR.MUST, f.klass, anchor, f.exhibit, f.page, f.problem, action, corrected, "entry read, confirmed"
    )


def _w4(s: str) -> set[str]:
    return set(_WORD4.findall(CL.CITE.sub("", s).lower()))


def _negations(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower())) & NEGATIONS


def _added(before: str, after: str) -> tuple[set[str], set[str]]:
    """The numbers, and the 4+ letter words and negations, `after` adds."""
    adds = (_w4(after) | _negations(after)) - (_w4(before) | _negations(before))
    return numbers(after) - numbers(before), adds


def _drops(quote: str, replacement: str) -> bool:
    """True when the replacement loses more numbers, or more 4+ letter words,
    than it puts in their place: content, not a corrected value."""
    lost_n, add_n = numbers(quote) - numbers(replacement), numbers(replacement) - numbers(quote)
    lost_w, add_w = _w4(quote) - _w4(replacement), _w4(replacement) - _w4(quote)
    return len(lost_n) > len(add_n) or len(lost_w) > len(add_w)


def _changed(quote: str, replacement: str) -> str:
    old = set(_norm(quote).split())
    return " ".join(t for t in _norm(replacement).split() if t not in old)


#: A unit printed right after a number: two values in different units never agree.
_UNIT = re.compile(r"(\d+(?:[.,/:]\d+)*)\s*(mcg|mg|g|ml|units|mmhg|lbs|kg|cm|mm|%)(?![a-z])", re.I)


def _units(s: str) -> dict[str, str]:
    return {norm_number(m.group(1)): m.group(2).lower() for m in _UNIT.finditer(CL.CITE.sub("", s))}


def _agrees(f: ReadFinding, c: Confirm, sentence: str) -> bool:
    """The blind value agrees with the correction (round 3, 2026-11-18):
    (a) every number, 4+ letter word and negation the replacement adds is in
    the blind value; (b) every number of the blind value is in the corrected
    sentence; (c) the blind value carries a negation exactly when the
    replacement's changed words do; (d) a number both print with a unit
    carries the same unit. The blind value's units and other words are
    otherwise ignored."""
    nums, ws = _added(f.quote, f.replacement)
    ch = nums | ws
    bv_n = numbers(c.value)
    if not ch or not ch <= bv_n | _w4(c.value) | _negations(c.value):
        return False
    if bv_n - numbers(sentence):
        return False
    if bool(_negations(c.value)) != bool(_negations(_changed(f.quote, f.replacement))):
        return False
    mine, theirs = _units(f.replacement), _units(c.value)
    return all(mine[n] == theirs[n] for n in nums if n in mine and n in theirs)


def _page_carries(claim_text: str, f: ReadFinding, page_text: str) -> str:
    """'' when the page's text layer carries everything the replacement adds."""
    nums, ws = _added(claim_text, f.replacement)
    if not nums and not ws:
        return "the change adds nothing the page text can be checked for"
    page_words = set(_WORD4.findall(page_text.lower())) | set(re.findall(r"[a-z]+", page_text.lower()))
    if nums - numbers(page_text) or ws - page_words:
        return "the page text does not carry what the replacement adds"
    return ""


def _value(f: ReadFinding, c: Confirm | None, page_text: str | None, unit: Unit) -> Decision:
    """A text page and a picture page alike need a blind read that names the
    page and agrees; a text page also needs its layer to carry the change."""
    claim = _claim(f, unit)
    if isinstance(claim, str):
        return _record(claim)
    if not f.replacement.strip() or CL.CITE.search(f.replacement):
        return _record("no replacement, or one carrying a citation")
    cite = _cite_of(f, unit, claim)
    if int(cite.group(1)) != f.exhibit or f.page not in CL.parse_pages(cite.group(2)):
        return _record("the page is not one the sentence cites")
    if _drops(f.quote, f.replacement):
        return _record(DROPS_CONTENT)
    if page_text is not None and (why := _page_carries(claim.text, f, page_text)):
        return _record(why)
    if not _blind_agrees(f, c):
        return _record("no agreeing blind read on the finding's page")
    line = unit.lines[f.line]
    corrected = claim.text.replace(line, edited_line(f, unit), 1)
    if not _agrees(f, c, corrected):
        return _record("the blind read's value differs from the replacement")
    return ("apply", _edit(f, claim.anchor, "replace", corrected))


def _pointer(f: ReadFinding, c: Confirm | None, unit: Unit) -> Decision:
    """Only the line's own one-page citation moved to one other page the
    blind read names."""
    claim = _claim(f, unit)
    if isinstance(claim, str):
        return _record(claim)
    own = list(CL.CITE.finditer(unit.lines[f.line]))
    pages = CL.parse_pages(own[-1].group(2)) or [] if len(own) == 1 else []
    if len(pages) != 1 or (int(own[-1].group(1)), pages[0]) == (f.exhibit, f.page):
        return _record(POINTER_MULTI)
    if c is None or c.answer != STATED or not _same_page(f, c):
        return _record(POINTER_MULTI)
    new = claim.text.replace(unit.lines[f.line], edited_line(f, unit), 1)
    return ("apply", _edit(f, claim.anchor, "replace", new))


def _carried(sentence: str, page: tuple[int, int], others: list[RR.Entry]) -> bool:
    vals = numbers(sentence) or words(sentence)
    have = numbers if numbers(sentence) else words
    return any(page in e.cites() and vals <= have(e.text) for e in others)


def _omitted(f: ReadFinding, c: Confirm | None, unit: Unit, others: list[RR.Entry]) -> Decision:
    if CODE.search(CL.CITE.sub("", f.replacement)):
        return _record(CODES_IN_INDEX)  # a code is never written from the writer's index
    cites = list(CL.CITE.finditer(f.replacement))
    if len(cites) != 1 or int(cites[0].group(1)) != f.exhibit or f.page not in CL.parse_pages(cites[0].group(2)):
        return _record("the added sentence does not end citing the finding's page")
    if (f.exhibit, f.page) not in unit.cites():
        return _record("the page is not one the entry already cites")
    if c is None or c.answer != YES or not _same_page(f, c):
        return _record("no agreeing blind read")
    if not (numbers(f.replacement) or words(f.replacement)):
        return _record("the sentence states no value to compare")
    if _carried(f.replacement, (f.exhibit, f.page), [e for e in others if e.anchor != unit.anchor]):
        return _record("another entry already carries that page and those values")
    if f.line not in unit.lines or not unit.lines[f.line]:
        return _record("the line to add after is not in the entry")
    edit = _edit(f, unit.anchor, "add", f.replacement.strip())
    edit.fid = f.line  # the label of the line the sentence goes after (``entry_audit_apply``)
    return ("apply", edit)


def decide(
    finding: ReadFinding,
    confirm: Confirm | None,
    page_text: str | None,
    unit: Unit,
    others: list[RR.Entry] | None = None,
) -> Decision:
    """("apply", edit) or ("record", reason). `page_text` is the finding
    page's usable text layer (``page_mode`` TEXT), None when it is shown as a
    picture; `others` is every entry of the document (``unit.entry`` excluded
    by anchor). A date finding is always recorded (round 3, 2026-11-18): a
    re-dated entry would leave the summary tables built before the audit
    (``stages/build_doc.py``) stale."""
    f = finding
    if f.record_only or unit.record_only:
        return _record("a prior-history finding is recorded, never applied")
    if f.klass in RECORD_CLASSES:
        return _record(f"a {f.klass} finding is recorded, never applied")
    if confirm is not None and confirm.answer in (UNPARSED, CANNOT_READ, NOT_STATED, NO):
        return _record(f"blind read {confirm.answer}")
    if f.klass in VALUE_CLASSES:
        return _value(f, confirm, page_text, unit)
    if f.klass == "pointer":
        return _pointer(f, confirm, unit)
    return _omitted(f, confirm, unit, others or [])


def usable_text(modes: RV.Modes | None, exhibit: int, page: int) -> str | None:
    """The page's text when the one rule shows it as text, else None."""
    m = modes(exhibit, page) if modes is not None else None
    return m.text if m is not None and m.kind == "text" and m.text else None


def to_repair_findings(decisions: list[Decision]) -> list[RR.Finding]:
    return [d[1] for d in decisions if d[0] == "apply"]


def shown(units: list[Unit]) -> dict[str, list[tuple[int, int]]]:
    """``review_repair.apply``'s `shown`: an add may cite any page its entry cites."""
    return {u.anchor: sorted(u.cites()) for u in units if not u.record_only}


def summary(units: list[Unit], parsed: list[Parsed], decisions: list[Decision], gaps: Any = ()) -> dict[str, Any]:
    """Plain counts for the run record; no text of the document."""
    found = [f for p in parsed for f in p.findings]
    dropped: Counter = sum((p.dropped for p in parsed), Counter())
    gl = list(gaps)
    return {
        "units": len(units),
        "findings_by_class": dict(Counter(f.klass for f in found)),
        "dropped": dict(dropped),
        "over_cap": sum(p.over_cap for p in parsed),
        "confirmed": len(decisions),
        "applied": sum(1 for d in decisions if d[0] == "apply"),
        "recorded_by_reason": dict(Counter(d[1] for d in decisions if d[0] == "record")),
        "gaps_codes": sum(1 for g in gl if g.kind == "codes"),
        "gaps_no_entry": sum(1 for g in gl if g.kind == "no_entry"),
    }
