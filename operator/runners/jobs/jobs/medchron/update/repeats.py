"""Which composed entries an update keeps, and in what shape. $0, no model.

An entry restating a visit the chronology already holds is kept only for what
it ADDS, and what it adds is decided on two things:

* VALUES: every number it states outside citations (``values.numbers``, the
  same notion proof 10 holds the document to, so a decision here can never
  drop a value that proof then fails on);
* TERMS: the diagnosis and impression words it states (``values.terms``), so a
  qualitative finding ("examination revealed paraspinal spasm") is no longer
  dropped for carrying no number.

A restatement of a DELIVERED visit that adds something becomes an "Additional
findings" row: only the sentences carrying a new value or term, each with its
citation, cut deterministically from the composed text (no model rewrite), so
each is audited as a new claim and nothing the firm already reads is said
twice. Between NEW entries, one with the date and provider of another whose
values and terms it does not exceed is dropped (two copies of one record).

SAME VISIT, IN OTHER WORDS (2026-11-17). Two overlapping exports of one
clinic's records were composed twice, each copy in its own words under its
own heading ("Surname, Given (M.D.)" and "Clinic Department, Given Surname"),
and a copy of a delivered visit was re-composed with a time of day or a year
the delivered row did not state, which ``values.numbers`` counts: a quarter
of the update's new rows re-listed a visit. Two entries are the SAME VISIT
(``same_visit``) when they share the visit date, their providers match
(``provider_score`` at least PROVIDER_MIN: the canonical provider, or the
Jaccard of the clinician's name words, facility and credential words aside),
and their bodies agree (``body_score`` at least BODY_MIN: the share of the
shorter body's words the other states). A same-visit entry is DROPPED only
when every content value it states (``values.content_numbers``: dates,
doses, vitals, labs, codes; a time of day and a bare year are not care) and
every finding term is in the text that stays (``covered``, the safety
property); one with no value and no term must restate PLAIN_MIN of its
words. One that adds a value is folded: against a delivered visit it is cut
to its "Additional findings"; against a new one, its sentences carrying what
the other lacks join that entry (``fold_new``); kept whole when neither can
be done without a loss.
"""

from __future__ import annotations

import difflib
import re
from typing import Callable

from ...stages.group import CRED_SEG, FACILITY_WORD
from . import body as B
from .values import content_numbers, norm_number, numbers, terms, words

ADDITIONAL = "Additional findings"
SAME_VISIT = "dropped: same visit as a delivered entry, no value or finding it lacks"
REPEAT_NEW = "dropped: repeats another new entry of that date and provider"
REWORDED = "dropped: the same visit as a delivered entry in other words, every value and finding in it"
REWORDED_NEW = "dropped: the same visit as another new entry in other words, every value and finding in it"
FOLDED_NEW = "folded: the same visit as another new entry, its added findings joined to it"
ADDITIONAL_MERGED = "merged: additional findings for one visit under one heading"
NEAR = 0.95  # how alike two texts read to count as one restated
PROVIDER_MIN = 0.5  # the canonical provider, else the Jaccard of the name words
BODY_MIN = 0.5  # the share of the shorter body's words the other states
PLAIN_MIN = 0.8  # the same, for an entry with no value and no finding term
#: Words of a provider line that name no person.
_NOT_A_NAME = {"the", "and", "for", "office", "visit", "practice", "physician", "provider", "doctor", "staff", "inc"}
#: A specialty or service word a facility heading carries ("Orthopedics", "Pain Management").
_SPECIALTY = re.compile(
    r"(?i)^(?:(?:orthop|neurolog|neurosurg|physiat|chiropr|radiolog|pediatr|cardiolog|rehabilit|occupational|"
    r"pulmon|gastroent|dermatol|psychiat|podiat|urolog|obstet|gynec)[a-z]*|(?:family|internal|spine|pain|sports|"
    r"primary|general|management|physical|therapy|physicians))$"
)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
Canon = Callable[[str], str]


def _same(e: str, others: list[str], canon: Canon) -> list[str]:
    d = B.iso(e)
    return [
        b
        for b in others
        if B.iso(b) == d and (canon(B.provider_of(b)) == canon(B.provider_of(e)) or B.score(e, b) >= 0.5)
    ]


def _new_in(text: str, values: set[str], vocab: set[str]) -> bool:
    return bool(numbers(text) - values) or bool(terms(text) - vocab)


def _sentences(e: str) -> list[tuple[str, str]]:
    """(sentence, citation) for every cited sentence of the entry's body: a
    sentence takes the citation that closes its span."""
    out: list[tuple[str, str]] = []
    for ln in e.splitlines()[2:]:
        prev, last = 0, None
        for m in B.CITE.finditer(ln):
            seg = ln[prev : m.start()].strip()
            prev, last = m.end(), m.group(0)
            out.extend((s.strip(), last) for s in SENTENCE.split(seg) if len(s.strip()) > 3)
        rest = ln[prev:].strip().lstrip(".;, ")
        if rest and last:  # text after the line's last citation travels with it, never dropped
            out.extend((s.strip(), last) for s in SENTENCE.split(rest) if len(s.strip()) > 3)
    return out


def additional(e: str, same_day: list[str], nums: Callable[[str], set[str]] = numbers) -> str | None:
    """The "Additional findings" row: `e`'s sentences that carry a value or a
    term the delivered entries of that day lack, or None when no single
    sentence does (then the whole entry is kept: what it adds is spread).
    `nums`: what counts as a value (``content_numbers`` for a same visit)."""
    found = findings if nums is content_numbers else terms  # a same visit is held to all it names
    look = vocab if nums is content_numbers else words
    have_v = set().union(*(nums(b) for b in same_day)) if same_day else set()
    have_w = set().union(*(look(b) for b in same_day)) if same_day else set()
    keep = [f"{s} {c}" for s, c in _sentences(e) if (nums(s) - have_v) or (found(s) - have_w)]
    if not keep:
        return None
    lost_v = nums(e) - have_v - set().union(*(nums(k) for k in keep))
    lost_t = found(e) - have_w - set().union(*((look if nums is content_numbers else terms)(k) for k in keep))
    if lost_v or lost_t:
        return None  # a value or finding outside every kept sentence would be lost: keep the entry whole
    lines = e.splitlines()
    return "\n".join([lines[0], f"{B.provider_of(e)} | {ADDITIONAL}", "", ADDITIONAL, *keep])


def _body(e: str) -> str:
    """The entry's body, normalised: lowercase words, citations and headings
    furniture removed, so two copies of one text compare equal."""
    t = B.CITE.sub(" ", "\n".join(e.splitlines()[2:])).lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _restates(e: str, by: str) -> bool:
    """True when `e`'s body is `by`'s, or inside it, near-exactly."""
    a, b = _body(e), _body(by)
    return bool(a) and (a in b or difflib.SequenceMatcher(None, a, b).ratio() >= NEAR)


def _plain(e: str) -> bool:
    """No value and no finding term: nothing the value-and-term test can see."""
    body = "\n".join(e.splitlines()[2:])  # the date line's own digits are not a value
    return not numbers(body) and not terms(body)


def keep_decision(e: str, base_ents: list[str], canon: Canon) -> tuple[str, str | None]:
    """('keep', entry) | ('keep: additional findings', row) | (reason, None).
    An entry with no value and no finding term is dropped only when its text
    restates a same-visit delivered entry: the value-and-term test cannot see
    what it says, and a visit dropped here is never read again. Where that
    keeps it, a SAME VISIT in other words (``same_visit``) is decided on
    content values (``_reworded``)."""
    same_day = [b for b in base_ents if B.iso(b) == B.iso(e)]
    verdict, row = _by_heading(e, base_ents, same_day, canon)
    if row is None:
        return verdict, row
    visit = [b for b in same_day if same_visit(e, b, canon)]
    if not visit:
        return verdict, row
    if _reworded(e, visit, same_day):
        return REWORDED, None
    if _plain(e):
        return verdict, row
    cut = additional(e, same_day, content_numbers if all(apart(e, t) for t in visit) else numbers)
    return ("keep: additional findings", cut) if cut else ("keep", e)


def _by_heading(e: str, base_ents: list[str], same_day: list[str], canon: Canon) -> tuple[str, str | None]:
    """The decision on a delivered entry of the visit's date and provider
    heading (or alike opening), every number counted."""
    same = _same(e, base_ents, canon)
    if not same:
        return "keep", e
    if _plain(e):
        return (SAME_VISIT, None) if any(_restates(e, b) for b in same) else ("keep", e)
    have_v = set().union(*(numbers(b) for b in same_day))
    have_w = set().union(*(words(b) for b in same_day))
    if not _new_in(e, have_v, have_w):
        return SAME_VISIT, None
    row = additional(e, same_day)
    return ("keep: additional findings", row) if row else ("keep", e)


def apart(e: str, kept: str) -> bool:
    """Two entries composed from different exhibits: what one copy of a
    record says in other words, never an edit of the same text."""
    return not (B.cited(e) & B.cited(kept))


def _reworded(e: str, visit: list[str], stays: list[str]) -> bool:
    """`e`, a same-visit copy of `visit`, drops: every content value and
    finding of it is in `stays` (``covered``), or, with neither, PLAIN_MIN of
    its words are in one of them. Times of day and bare years are set aside
    only when every same-visit survivor was composed from another exhibit
    (``apart``); else every number counts."""
    if _plain(e):
        return any(body_score(e, t) >= PLAIN_MIN for t in visit)
    nums = content_numbers if all(apart(e, t) for t in visit) else numbers
    return covered(e, stays, nums)


def _name_words(e: str) -> set[str]:
    """The words of an entry's provider that can name a person: facility,
    credential and office words aside."""
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", B.provider_of(e)):
        if FACILITY_WORD.search(w) or CRED_SEG.match(w) or w.lower() in _NOT_A_NAME or _SPECIALTY.match(w):
            continue
        out.add(w.lower())
    return out


def provider_score(a: str, b: str, canon: Canon) -> float:
    """1.0 when the canonical providers agree, else the Jaccard of the
    provider lines' name words ("Surname, Given (M.D.)" and "Clinic
    Department, Given Surname" are one clinician)."""
    if canon(B.provider_of(a)) == canon(B.provider_of(b)):
        return 1.0
    ta, tb = _name_words(a), _name_words(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _body_words(e: str) -> set[str]:
    body = B.CITE.sub(" ", "\n".join(e.splitlines()[2:])).lower()
    return set(re.findall(r"[a-z]{4,}", body))


def body_score(a: str, b: str) -> float:
    """The share of the shorter body's distinct words (four letters or more,
    citations aside) that the other body states."""
    wa, wb = _body_words(a), _body_words(b)
    small = min(len(wa), len(wb))
    return len(wa & wb) / small if small else 0.0


_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?!\d)")


def _clocks(e: str) -> set[str]:
    return {f"{int(h)}:{m}" for h, m in _CLOCK.findall(B.CITE.sub(" ", "\n".join(e.splitlines()[2:])))}


def same_visit(a: str, b: str, canon: Canon) -> bool:
    """One visit written twice: one visit date, matching providers
    (PROVIDER_MIN), agreeing bodies (BODY_MIN), and no two times of day
    apart: two entries that each state a time and share none are two
    encounters (two draws, two visits) of one day."""
    if B.iso(a) != B.iso(b) or provider_score(a, b, canon) < PROVIDER_MIN or body_score(a, b) < BODY_MIN:
        return False
    ca, cb = _clocks(a), _clocks(b)
    return not (ca and cb and not ca & cb)


_NUM_AT = re.compile(r"\d+(?:[.,/:]\d+)*")
_WORD_AT = re.compile(r"[A-Za-z]+")


def anchors(text: str) -> set[str]:
    """The word each content number is stated for: the nearest word of four
    letters or more before it, within three words ("cyclobenzaprine 10 mg",
    "pulse 74", "follow up on 03/18/2026"). A drug or a test named only
    beside its number is not a finding term (``values.terms``), and the
    bare number can be found elsewhere ("6 of 10"), so the word is held too."""
    t = B.CITE.sub(" ", text or "")
    want = content_numbers(t)
    out = set()
    for m in _NUM_AT.finditer(t):
        if norm_number(m.group(0)) not in want:
            continue
        before = _WORD_AT.findall(t[max(0, m.start() - 60) : m.start()])[-3:]
        near = [w.lower() for w in before if len(w) >= 4]
        if near:
            out.add(near[-1])
    return out


def findings(text: str) -> set[str]:
    """What a text names that must not be lost: its finding terms and the
    word each of its content numbers is stated for (``anchors``)."""
    return terms(text) | anchors("\n".join(text.splitlines()[2:]) if B.HEAD.match(text) else text)


def vocab(text: str) -> set[str]:
    """Every word of four letters or more, citations aside: where a finding is looked up."""
    return set(re.findall(r"[a-z]{4,}", B.CITE.sub(" ", text or "").lower()))


def covered(e: str, stays: list[str], nums: Callable[[str], set[str]] = content_numbers) -> bool:
    """THE SAFETY PROPERTY: every content value of `e` and everything it
    names (``findings``) is in the text that stays, so dropping `e` loses
    none of them."""
    if not stays:
        return False
    have_v = set().union(*(nums(b) for b in stays))
    have_w = set().union(*(vocab(b) for b in stays))
    return nums(e) <= have_v and findings(e) <= have_w


_DRAW_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
_ACCESSION = re.compile(
    r"(?i)\b(?:accession|acc|specimen(?:\s*id)?)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,})"
)
_PANEL = re.compile(
    r"(?i)\b(cbc|complete blood count|cmp|comprehensive metabolic panel|bmp|basic metabolic panel|lipid panel|"
    r"urinalysis|hemoglobin a1c|a1c|tsh|hepatic function panel|coagulation panel|pt/inr)\b"
)
SAME_DRAW = "merged: one lab draw entered from two new documents"


def _draw(e: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """(draw time or accession marks, panels) of a lab entry, or None."""
    body = "\n".join(e.splitlines()[2:])
    panels = frozenset(p.lower() for p in _PANEL.findall(body))
    marks = frozenset(_DRAW_TIME.findall(body)) | frozenset(a.upper() for a in _ACCESSION.findall(body))
    return (marks, panels) if panels and marks else None


def merge_draws(kept: list[str]) -> tuple[list[str], int]:
    """New entries with one lab draw entered twice (the same date, the same
    draw time or accession, the same panels: two documents carrying one
    report) become one entry: the longer one, plus each sentence of the other
    stating a value it lacks, plus a line citing the other document, so no
    value is lost and both sources are cited."""
    out = list(kept)
    merged = 0
    i = 0
    while i < len(out):
        j, joined = None, ""
        for k in range(i + 1, len(out)):
            joined = _merge_one(out[i], out[k])
            if joined:
                j = k
                break
        if j is None:
            i += 1
            continue
        out[i] = joined
        del out[j]
        merged += 1
    return out, merged


def _merge_one(a: str, b: str) -> str:
    """The two entries as one when they carry one lab draw and nothing is
    lost by joining them, else ''."""
    da, db = _draw(a), _draw(b)
    if not (da and db and B.iso(a) == B.iso(b) and da[1] == db[1] and da[0] & db[0]):
        return ""
    big, small = (a, b) if len(a) >= len(b) else (b, a)
    extra = [f"{s} {c}" for s, c in _sentences(small) if numbers(s) - numbers(big)]
    cites = sorted({m.group(0) for m in B.CITE.finditer(small)} - {m.group(0) for m in B.CITE.finditer(big)})
    joined = "\n".join([big, *extra, *[f"The same results are reported in {c}." for c in cites[:1]]])
    return "" if numbers(small) - numbers(joined) else joined


def _covered(e: str, by: str) -> bool:
    if _plain(e):
        return _restates(e, by)
    return numbers(e) <= numbers(by) and terms(e) <= words(by)


def fold_new(big: str, small: str) -> str:
    """`big` with each sentence of `small` that states a number or a finding
    term `big` lacks, each with its own citation; '' when one of `small`'s
    numbers or terms would still be missing (every number counts here: the
    fold carries `small`'s own citations, so it is held to all of them)."""
    have_v, have_w = numbers(big), vocab(big)
    extra = [f"{s} {c}" for s, c in _sentences(small) if (numbers(s) - have_v) or (findings(s) - have_w)]
    joined = "\n".join([big.rstrip("\n"), *extra])
    return joined if extra and covered(small, [joined], numbers) else ""


def _twin_of(e: str, kept: list[str], canon: Canon) -> bool:
    return any(
        _covered(e, t) for t in kept if B.iso(t) == B.iso(e) and canon(B.provider_of(t)) == canon(B.provider_of(e))
    )


def dedupe_new(kept: list[str], canon: Canon) -> tuple[list[str], int]:
    """``dedupe_report`` with one count: the entries dropped or folded."""
    out, rep = dedupe_report(kept, canon)
    return out, sum(rep.values())


def dedupe_report(kept: list[str], canon: Canon) -> tuple[list[str], dict[str, int]]:
    """New entries less any whose date and provider another new entry shares
    and whose values and terms that entry covers (the longer one stays), and
    less any that is the SAME VISIT as one kept (``same_visit``): dropped when
    what stays covers it (``covered``), folded into it when it adds a value
    (``fold_new``), kept whole otherwise. Returns {reason: count}."""
    order = sorted(range(len(kept)), key=lambda i: -len(kept[i]))
    cur = list(kept)
    out: list[int] = []
    rep: dict[str, int] = {}
    for i in order:
        e = cur[i]
        visit = [j for j in out if same_visit(e, cur[j], canon)]
        if _twin_of(e, [cur[j] for j in out], canon):
            why = REPEAT_NEW
        elif visit and _reworded(e, [cur[j] for j in visit], [cur[j] for j in visit]):
            why = REWORDED_NEW
        else:
            joined = fold_new(cur[visit[0]], e) if visit and not _plain(e) else ""
            if not joined:
                out.append(i)
                continue
            cur[visit[0]], why = joined, FOLDED_NEW
        rep[why] = rep.get(why, 0) + 1
    return [cur[i] for i in sorted(out)], rep


def merge_additional(kept: list[str]) -> tuple[list[str], int]:
    """Every "Additional findings" row of one visit (the same date and
    provider lines) as one row under one heading, each line once."""
    out: list[str] = []
    at: dict[str, int] = {}
    merged = 0
    for e in kept:
        lines = e.split("\n")
        head = "\n".join(lines[:2])
        if len(lines) < 4 or lines[1].rpartition(" | ")[2] != ADDITIONAL or lines[3] != ADDITIONAL:
            out.append(e)
            continue
        if head not in at:
            at[head] = len(out)
            out.append(e)
            continue
        have = out[at[head]].split("\n")
        out[at[head]] = "\n".join(have + [ln for ln in lines[4:] if ln.strip() and ln not in have])
        merged += 1
    return out, merged
