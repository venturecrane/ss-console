"""The second, BLIND read of each entry-read finding, and the code that
decides what is applied. $0 here; the paid call is the stage's.

The confirm is shown at most CONFIRM_PAGES pages (the quoted sentence's cited
pages and the finding's page, one either side) and asked ONE question about
the item, never the proposed replacement (except an added sentence, which it
is shown and asked only whether the pages state it). ``decide`` is pure code
(tightened in the fix round of 2026-11-18):

* provider and another_person findings, and every finding on a record-only
  unit, are recorded, never applied; a date finding is applied only on an
  entry dated after today, to a date the cited page's text states and the
  blind read gives;
* value / wrong_field / unsupported: applied only when the blind read names
  the finding's page and its value agrees with the replacement's changed
  value, a negation it changes is carried by the blind read too, it drops
  no other content (``drops_content``), and on a text page the page's own
  layer carries every number, 4+ letter word and negation it adds;
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
from datetime import date
from typing import Any

from ..audit import claims as CL
from ..prompts import load
from . import review_pages as RV, review_repair as RR
from .entry_read import Images, Parsed, ReadFinding, Unit
from .values import NUM, numbers, words

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
#: A diagnosis code as the writer's INDEX states one (``icd.ICD_SHAPE``'s
#: letter forms, and an ICD-9 code with its decimal), found inside a sentence.
CODE = re.compile(r"\b(?:[A-Z]\d{2}(?:\.[0-9A-Z]{1,4}|[0-9A-Z]{0,4})|\d{3}\.\d{1,2})\b")
CODES_IN_INDEX, DROPS_CONTENT, POINTER_MULTI = "codes_in_index", "drops_content", "pointer_multi"


def _record(why: str) -> Decision:
    return ("record", why)


def _same_page(f: ReadFinding, c: Confirm) -> bool:
    return c.page == f.page and c.exhibit in (None, f.exhibit)


def _blind_agrees(f: ReadFinding, c: Confirm | None) -> bool:
    """A blind read that names the finding's page and states a value."""
    return c is not None and c.answer == STATED and _same_page(f, c) and bool(c.value.strip())


def _claim(f: ReadFinding, unit: Unit) -> RR.Claim | str:
    hits = [c for c in (unit.entry.claims if unit.entry else []) if f.quote in c.text]
    if len(hits) != 1 or hits[0].text.count(f.quote) != 1:
        return "the quote is not in exactly one cited sentence of the entry" if not hits else "the quote is ambiguous"
    return hits[0]


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


def _agrees(f: ReadFinding, c: Confirm) -> bool:
    got, changed = _norm(c.value), _changed(f.quote, f.replacement)
    return bool(changed) and got in (changed, _norm(f.replacement))


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
    cite = list(CL.CITE.finditer(claim.text))[-1]
    if int(cite.group(1)) != f.exhibit or f.page not in CL.parse_pages(cite.group(2)):
        return _record("the page is not one the sentence cites")
    if _drops(f.quote, f.replacement):
        return _record(DROPS_CONTENT)
    if page_text is not None and (why := _page_carries(claim.text, f, page_text)):
        return _record(why)
    if not _blind_agrees(f, c):
        return _record("no agreeing blind read on the finding's page")
    if not _agrees(f, c):
        return _record("the blind read's value differs from the replacement")
    if _negations(f.quote) != _negations(f.replacement) and bool(_negations(c.value)) != bool(
        _negations(f.replacement)
    ):
        return _record("a negation changes and the blind read does not carry it")
    return ("apply", _edit(f, claim.anchor, "replace", claim.text.replace(f.quote, f.replacement, 1)))


def _pointer(f: ReadFinding, c: Confirm | None, unit: Unit) -> Decision:
    """Only a one-page citation moved to one other page the blind read names."""
    claim = _claim(f, unit)
    if isinstance(claim, str):
        return _record(claim)
    m = list(CL.CITE.finditer(claim.text))[-1]
    pages = CL.parse_pages(m.group(2)) or []
    if len(pages) != 1 or (int(m.group(1)), pages[0]) == (f.exhibit, f.page):
        return _record(POINTER_MULTI)
    if c is None or c.answer != STATED or not _same_page(f, c):
        return _record(POINTER_MULTI)
    new = f"{claim.text[: m.start()]}(Exhibit {f.exhibit} - p. {f.page}){claim.text[m.end() :]}"
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
    return ("apply", _edit(f, unit.anchor, "add", f.replacement.strip()))


def _mdy(s: str) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", s.strip())
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


def dated_after(unit: Unit, today: date | None) -> bool:
    """True when the unit's entry is dated after `today` (a misread date)."""
    d = _mdy(unit.entry.text[:10]) if unit.entry is not None else None
    return d is not None and today is not None and d > today


def _date(f: ReadFinding, c: Confirm | None, page_text: str | None, unit: Unit, today: date | None) -> Decision:
    """A date finding is applied only on an entry dated after today, to a date
    the cited page's text states and the blind read gives."""
    new = _mdy(f.replacement)
    if unit.entry is None or not dated_after(unit, today) or new is None or today is None or new > today:
        return _record("a date finding is recorded unless the entry is dated after today")
    if (f.exhibit, f.page) not in unit.cites():
        return _record("the page is not one the entry cites")
    edit = _edit(f, unit.anchor, "redate", f.replacement.strip())
    if RR._date_on_page(edit, unit.entry, lambda _x, _p: page_text):
        return _record("the page text does not state the corrected date")
    if not _blind_agrees(f, c) or _mdy(c.value) != new:
        return _record("no agreeing blind read of the date")
    return ("apply", edit)


def decide(
    finding: ReadFinding,
    confirm: Confirm | None,
    page_text: str | None,
    unit: Unit,
    others: list[RR.Entry] | None = None,
    today: date | None = None,
) -> Decision:
    """("apply", edit) or ("record", reason). `page_text` is the finding
    page's usable text layer (``page_mode`` TEXT), None when it is shown as a
    picture; `others` is every entry of the document (``unit.entry`` excluded
    by anchor); `today` decides whether a date finding may apply."""
    f = finding
    if f.record_only or unit.record_only:
        return _record("a prior-history finding is recorded, never applied")
    if f.klass == "date":
        return _date(f, confirm, page_text, unit, today)
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
