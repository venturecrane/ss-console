"""Lint the drafter's markup: anything a reader could check against a page
must be a token, never typed.

Refused, outside tokens:

* a quotation of four words or more (``hand_quote``): the page's characters
  come from ``{{Q:id}}``;
* a dollar figure (``hand_money``), a date (``hand_date``), a count or a
  total the aggregates cover (``hand_count``);
* a cite: ``FILE:``, a pin (``p. 12``), ``Dep.``, a transcript ``45:3``, a
  reporter cite (``123 Cal.App.4th 456``), ``Id.`` (``hand_cite``).

Refused in tokens: an id the ledger does not hold (``unknown_id``) or
rejected (``rejected_id``), a kind the token cannot name (``kind_mismatch``),
a figure or date with no cite in its sentence (``uncited``), and a figure
whose ROLE the sentence's wording contradicts (``role_contradiction``): a
billed amount called paid, opposing counsel's number from a question stated
as a fact (taxonomy item 1), an expert's number with nobody stating it.

Text the firm authored (its skeleton, its standing boilerplate) is exempt: it
is the firm's words. Findings go back for ONE scoped repair (``repair_brief``).
"""

from __future__ import annotations

import re
from typing import Iterable

from . import markup
from .shapes import Finding, Ledger, Token

QUOTE_MIN_WORDS = 4
_NUM_WORDS = (
    "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    "eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|dozen"
)
_COUNT_NOUNS = (
    r"visits?|appointments?|sessions?|treatments?|injections?|surger(?:y|ies)|procedures?|providers?|bills?|"
    r"records?|days?|weeks?|months?|years?|hours?|encounters?|admissions?|x-rays?|mris?|scans?|prescriptions?|"
    r"physicians?|doctors?|therapists?|documents?|pages?|exhibits?|occasions?|times"
)
_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_REPORTER = (
    r"Cal\.\s?(?:App\.\s?)?(?:\d(?:st|nd|rd|th|d)|Supp\.?)?|Cal\.\s?Rptr\.(?:\s?\d(?:d|rd|th))?|"
    r"F\.(?:\s?Supp\.)?(?:\s?\d(?:d|th))?|F\.\s?App'x|U\.S\.|S\.\s?Ct\.|L\.\s?Ed\.(?:\s?\d(?:d))?|"
    r"P\.(?:\s?\d(?:d|rd))?|N\.[EW]\.(?:\s?\d(?:d))?|S\.[EW]\.(?:\s?\d(?:d))?|So\.(?:\s?\d(?:d))?|"
    r"A\.(?:\s?\d(?:d))?|Cal\.\s?Unrep\.|WL"
)
_DETECTORS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("hand_cite", "a cite typed by hand; cite with {{F:id}}", re.compile(r"\bFILE:", re.I)),
    ("hand_cite", "a page pin typed by hand; cite with {{F:id}}", re.compile(r"(?<![A-Za-z])pp?\.\s?\d+")),
    ("hand_cite", "a deposition cite typed by hand; cite with {{F:id}}", re.compile(r"\bDep(?:o)?\.(?=\s|$|,)")),
    (
        "hand_cite",
        "a transcript page and line typed by hand; cite with {{F:id}}",
        re.compile(
            r"(?<![\d:/.$])\b\d{1,4}:\d{1,3}(?:\s?-\s?\d{1,4}(?::\d{1,3})?)?\b(?!\s?(?:[ap]\.?m\b|[ap]\.m\.))", re.I
        ),
    ),
    ("hand_cite", "a reporter cite typed by hand", re.compile(r"\b\d{1,4}\s+(?:" + _REPORTER + r")\s*\d{1,6}\b")),
    ("hand_cite", "an Id. cite typed by hand; cite with {{F:id}}", re.compile(r"(?<![A-Za-z])Id\.(?:\s+at\b)?")),
    (
        "hand_money",
        "a dollar figure typed by hand; use {{N:id}} or {{SUM:ids}}",
        re.compile(r"\$\s?\(?\d[\d,]*(?:\.\d+)?\)?|\b\d[\d,]*(?:\.\d+)?\s+dollars\b", re.I),
    ),
    (
        "hand_date",
        "a date typed by hand; use {{D:id}}",
        re.compile(
            r"\b"
            + _MONTHS
            + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b|\b"
            + _MONTHS
            + r",?\s+(?:of\s+)?(?:19|20)\d{2}\b|"
            r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}-\d{1,2}-(?:19|20)?\d{2}\b"
        ),
    ),
    (
        "hand_count",
        "a count typed by hand; use {{COUNT:ids}} or {{SPAN:a,b}}",
        re.compile(
            r"\b(?:\d+|" + _NUM_WORDS + r")\s+(?:(?:separate|additional|total|more|further|medical|physical|"
            r"therapy|chiropractic|follow-up|office|consecutive|straight)\s+){0,2}(?:" + _COUNT_NOUNS + r")\b",
            re.I,
        ),
    ),
    (
        "hand_count",
        "an age or a total typed by hand; use a token",
        re.compile(r"\b\d+[- ]years?[- ]old\b|\btotal(?:ing|ed|s)?\s+(?:of\s+)?(?:\d|" + _NUM_WORDS + r")\b", re.I),
    ),
    ("hand_count", "a percentage typed by hand; use {{N:id}}", re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.I)),
)
_QUOTES = (
    re.compile(r'"([^"\n]{2,3000}?)"'),
    re.compile(r"“([^”]{2,3000}?)”"),
    re.compile(r"(?<![A-Za-z])'((?:[^'\n]|'(?=[A-Za-z]))+?)'(?![A-Za-z])"),
    re.compile(r"(?m)^[ \t]*>[ \t]?(.+)$"),
)
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")

#: Words that say what a dollar figure is, by role.
_ROLE_WORDS = {
    "billed": re.compile(r"\b(?:bill(?:s|ed|ing)?|charg(?:e|es|ed|ing)|invoic\w*|cost\w*)\b", re.I),
    "paid": re.compile(r"\b(?:paid|pay(?:s|ing|ment|ments)?|reimburs\w*|accept\w*|satisf\w*|collected)\b", re.I),
    "adjusted": re.compile(r"\b(?:adjust\w*|written\s+off|wrote\s+off|write-?offs?|discount\w*|reduc\w*)\b", re.I),
}
_ATTRIBUTION_Q = re.compile(
    r"\b(?:ask\w*|question\w*|suggest\w*|propos\w*|posit\w*|hypothe\w*|counsel|defen[cs]e|defendants?|"
    r"insurer|carrier|claim(?:s|ed)?)\b",
    re.I,
)
_ATTRIBUTION_X = re.compile(
    r"\b(?:testif\w*|opin\w*|report\w*|stat(?:e|es|ed|ing)|found|finds|according|estimat\w*|conclu\w*|wrote|"
    r"writes|said|says|calculat\w*|project\w*|assess\w*|Dr\.|expert|examiner|economist)\b",
    re.I,
)
_ROLE_WINDOW = 60  # characters before a figure token that say what it is


def _norm(s: str) -> str:
    return " ".join(w.lower() for w in _WORD.findall(s))


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    """Rough sentence spans: a blank line, a heading line, or a sentence end
    (``. ! ?`` before whitespace, not an abbreviation)."""
    from .verify import _is_end

    out, start = [], 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\n" and (
            text.startswith("\n", i + 1) or text[i + 1 : i + 2] == "#" or text[i + 1 : i + 3] == "\r\n"
        ):
            out.append((start, i))
            start = i + 1
        elif text[i] in ".!?" and _is_end(text, i):
            j = i + 1
            while j < n and text[j] in "\"')]’”":
                j += 1
            out.append((start, j))
            start = j
            i = j
            continue
        i += 1
    out.append((start, n))
    return [(a, b) for a, b in out if text[a:b].strip()]


def _sentence_of(bounds: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for a, b in bounds:
        if a <= pos < b:
            return a, b
    return (0, 0)


def _exempt_set(exempt: Iterable[str]) -> list[str]:
    return [_norm(markup.strip_tokens(e)) for e in exempt if e and e.strip()]


def _is_exempt(snippet: str, sentence: str, exempt: list[str]) -> bool:
    """The firm's own words: the sentence (three words or more) stands in its
    skeleton or boilerplate."""
    ns = _norm(sentence)
    return len(ns.split()) >= 3 and any(ns in e for e in exempt)


def hand_typed(text: str, exempt: Iterable[str] = (), display: str | None = None) -> list[Finding]:
    """Hand-typed cites, quotations, figures, dates and counts in ``text``
    (whose tokens the caller has blanked, so offsets still index it).
    ``display`` is the same text unblanked, for the sentence a finding
    reports; exemption is judged on the blanked words."""
    ex = _exempt_set(exempt)
    shown = display if display is not None and len(display) == len(text) else text
    bounds = sentence_bounds(text)
    out: list[Finding] = []
    seen: set[tuple[int, int, str]] = set()

    def add(code: str, msg: str, a: int, b: int) -> None:
        sa, sb = _sentence_of(bounds, a)
        snippet = text[a:b]
        if _is_exempt(snippet, text[sa:sb], ex):
            return
        key = (a, b, code)
        if key not in seen:
            seen.add(key)
            out.append(Finding(code, msg, a, b, snippet.strip(), shown[sa:sb].strip()))

    for code, msg, rx in _DETECTORS:
        for m in rx.finditer(text):
            add(code, msg, m.start(), m.end())
    for rx in _QUOTES:
        for m in rx.finditer(text):
            if len(_WORD.findall(m.group(1))) >= QUOTE_MIN_WORDS:
                add(
                    "hand_quote",
                    f"a quotation of {QUOTE_MIN_WORDS}+ words typed by hand; quote with {{{{Q:id}}}}",
                    m.start(),
                    m.end(),
                )
    out.sort(key=lambda f: (f.start, f.code))
    return out


def _cited_ids(tokens: list[Token], a: int, b: int) -> set[str]:
    return {i for t in tokens if a <= t.start < b and t.kind in ("F", "Q", "A") for i in t.ids}


def token_findings(text: str, tokens: list[Token], ledger: Ledger) -> list[Finding]:
    """Uncited figures and dates, and figure roles the wording contradicts."""
    blank = markup.strip_tokens(text)
    bounds = sentence_bounds(blank)
    out: list[Finding] = []
    for t in tokens:
        if t.kind not in ("N", "D", "SUM"):
            continue
        a, b = _sentence_of(bounds, t.start)
        sentence = blank[a:b]
        shown = text[a:b].strip()
        facts = [ledger.get(i) for i in t.ids]
        if any(f is None or not f.usable for f in facts):
            continue  # validate reports it
        if t.kind in ("N", "D"):
            cited = _cited_ids(tokens, a, b)
            if t.ids[0] not in cited:
                out.append(
                    Finding(
                        "uncited",
                        f"{t.ids[0]} is stated with no cite in its sentence; add {{{{F:{t.ids[0]}}}}}",
                        t.start,
                        t.end,
                        t.raw,
                        shown,
                    )
                )
        for f in facts:
            if f is None or f.kind != "figure":
                continue
            msg = _role_problem(f.role or "other", blank, t.start, sentence)
            if msg:
                out.append(Finding("role_contradiction", f"{f.id}: {msg}", t.start, t.end, t.raw, shown))
                break
    return out


def _role_problem(role: str, blank: str, at: int, sentence: str) -> str | None:
    if role == "opposing_question":
        if not _ATTRIBUTION_Q.search(sentence):
            return "opposing counsel's number from a question is stated as a fact; say who put it and how"
        return None
    if role == "expert_stated":
        if not _ATTRIBUTION_X.search(sentence):
            return "an expert's number is stated with nobody stating it; attribute it"
        return None
    if role not in _ROLE_WORDS:
        return None
    window = blank[max(0, at - _ROLE_WINDOW) : at]
    window = window[window.rfind(".") + 1 :] if "." in window else window
    said = {r for r, rx in _ROLE_WORDS.items() if rx.search(window)}
    if said and role not in said:
        return f"a {role} amount is described as {' or '.join(sorted(said))}"
    return None


def lint(text: str, ledger: Ledger, exempt: Iterable[str] = (), authorities: object = None) -> list[Finding]:
    """Every finding in the markup, in text order. Empty means render may run.
    ``authorities`` (the firm's store) checks ``{{L:slug}}`` slugs; without
    it a slug is not judged here (render still refuses one it cannot fill)."""
    tokens, out = markup.parse(text)
    out += markup.validate(tokens, ledger, authorities)
    out += hand_typed(markup.strip_tokens(text), exempt, display=text)
    out += token_findings(text, tokens, ledger)
    out.sort(key=lambda f: (f.start, f.code))
    return out


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.code] = out.get(f.code, 0) + 1
    return out


def repair_brief(findings: list[Finding]) -> str:
    """The findings as the one scoped repair receives them: each sentence,
    what lint refused in it, and why."""
    by: dict[str, list[Finding]] = {}
    for f in findings:
        by.setdefault(f.sentence or f.snippet, []).append(f)
    lines = []
    for sentence, fs in by.items():
        lines.append(f"- SENTENCE: {' '.join(sentence.split())[:600]}")
        for f in fs:
            lines.append(f"  - [{f.code}] {f.message}: {' '.join(f.snippet.split())[:200]}")
    return "\n".join(lines)
