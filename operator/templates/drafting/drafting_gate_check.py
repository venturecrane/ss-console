#!/usr/bin/env python3
"""Mechanical quality gate for the drafting lane (drafting-discipline.md Part II).

CANONICAL SOURCE: ``operator/templates/drafting/drafting_gate_check.py``. Every
drafting skill runs this checker against its draft before surfacing the draft to
the requesting attorney. Stdlib only, Python 3.10+, so it runs on the seat under
``execute_code`` with no install step.

What this gate decides
----------------------
It is the CHECKER half of the ten-gate enforcement map. It answers mechanical
questions only, and it answers them from the draft text and the source record:

  2a  QUOTE CONTIGUITY   every quoted passage is verbatim-contiguous in a source
  2b  QUESTION PAIRING   a transcript quote's cited range includes the question
                         it answered (best-effort, fail-open when unparseable)
  1/6 PRIVILEGE + WALL   no held-out text, no internal paths in the draft body
  3   SELF-CERTIFICATION no blanket completeness sentences
  7   COVERAGE           every propounded item got a response heading
  8   SPROG LINT         one fact per special interrogatory, no subparts
  9   VISIBLE MARKERS    reservations survive rendering, markers are closed
  MI  MARKER INTEGRITY   FILL markers keep their source note

What this gate deliberately does NOT decide
-------------------------------------------
Nothing about judgment, characterization, or legal merit. Gate 2c
(characterization review), gate 4 (source-over-summary), gate 5 (content-neutral
transformation) and gate 10 (form-text lookup) are PROSE or CONTEXT enforcement
points and are out of scope here. A clean run means the draft cleared the
mechanical floor, never that the draft is correct.

Exit behavior
-------------
  0  no FAIL findings (WARN and INFO may still be present)
  1  one or more FAIL findings
  2  usage or IO error

Malformed input never raises out of ``main``. Anything unexpected is reported as
a FAIL and the run fails closed, because a checker that crashes is
indistinguishable from a checker that passed.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

SEVERITY_FAIL = "FAIL"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"

_SEVERITY_ORDER = {SEVERITY_FAIL: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}

# Source files the checker will read when handed a directory.
_SOURCE_SUFFIXES = (".md", ".txt", ".markdown", ".text")

# Minimum quoted length, in words, before gate 2a takes an interest. Short
# quoted fragments ("signaled", "on time") are ordinary English and checking
# them produces noise, not findings.
_MIN_QUOTE_WORDS = 4

# Consecutive-word run that counts as held-out leakage. Eight is long enough
# that shared legal boilerplate does not trip it.
_HELD_OUT_NGRAM = 8

# Longest source gap an ellipsis may bridge before the passage stops being an
# elision and starts being a jump to different testimony.
_MAX_ELISION_CHARS = 300

# Gate 3 seed list. Extend here, not at the call site. Each entry is
# (pattern, requires_uncited, description).
_SELF_CERT_PATTERNS: tuple[tuple[str, bool, str], ...] = (
    (r"all responsive documents", False, "blanket completeness claim"),
    (r"fully (?:responds|addresses|complies)", False, "blanket sufficiency claim"),
    (r"complete and accurate", False, "blanket accuracy certification"),
    (r"no responsive documents exist", True, "uncited nonexistence certification"),
    (
        r"this (?:draft|response) (?:is complete|covers all)",
        False,
        "draft certifying itself",
    ),
)

# Gate 6 external-document wall. Firm-internal plumbing that must never appear
# in text an attorney may send outside the firm.
_INTERNAL_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<![\w/])operator/[\w./-]+", "internal repo path"),
    (r"r2://[\w./-]*", "internal object-store URI"),
    (r"(?<![\w/])vaults?/[\w./-]+", "internal vault path"),
    (r"`[^`\n]*\.(?:md|py)`", "backticked internal file reference"),
    (r"(?<![\w/])\.claude/[\w./-]+", "internal agent-config path"),
    (r"/Users/[\w./-]+", "absolute local filesystem path"),
)

_SMART_CHARS = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "″": '"',
    "–": "-",
    "—": "-",
    "‒": "-",
    "−": "-",
    "…": "...",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",
}

_SMART_RE = re.compile("|".join(re.escape(k) for k in _SMART_CHARS))
_WS_RE = re.compile(r"\s+")

# Inline markdown syntax, removed symmetrically from source and quote.
_MD_INLINE_RE = re.compile(r"\*\*|__|~~|\*|`")
_MD_BLOCKQUOTE_RE = re.compile(r"(?:^|(?<=\s))>+(?=\s|$)", re.MULTILINE)

# Transcript gutter: " 17   A.  A little behind." The two-space floor after the
# number keeps ordered lists ("1. First item") out of the match.
_GUTTER_RE = re.compile(r"^[ \t]{0,10}(\d{1,3})[ \t]{2,}(.*)$")
_PAGE_RE = re.compile(r"^[ \t]*(?:Page|PAGE)[ \t]+(\d{1,4})[ \t]*$")
_QUESTION_RE = re.compile(r"^(?:BY [A-Z][^:]{0,60}:\s*)?Q[.:]?\s")
_ANSWER_RE = re.compile(r"^A[.:]?\s")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HELD_OUT_HEADING_RE = re.compile(r"^#{1,6}\s+.*held\s+out", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

# "22:15 to 22:17", "39:22 to 40:13", "23:15-24", "23:15 - 23:24"
_RANGE_RE = re.compile(r"(\d{1,4}):(\d{1,3})\s*(?:to|through|thru|-|--)\s*(\d{1,4})(?::(\d{1,3}))?")
_POINT_RE = re.compile(r"(\d{1,4}):(\d{1,3})")

# "..." after normalization, and the spaced legal form ". . ."
_ELLIPSIS_SPLIT_RE = re.compile(r"\.\s*\.\s*\.")

_FILL_RE = re.compile(r"\{\{FILL:(.*?)\}\}", re.DOTALL)
_VISIBLE_MARKER_TOKENS = ("{{NOT IN RECORD", "{{FILL", "{{ATTORNEY")

_SPROG_ITEM_RE = re.compile(r"SPECIAL\s+INTERROGATOR(?:Y|IES)(?:\s+NO\.?)?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_SPROG_SCOPE_RE = re.compile(r"SPECIAL\s+INTERROGATOR(?:Y|IES)", re.IGNORECASE)
_LETTERED_LEAD_RE = re.compile(r"^\s*\(([a-h])\)\s+\S")
_LETTERED_INLINE_RE = re.compile(r"\(([a-h])\)")

# Propounded-item id aliases. Left side is what an items file may carry, right
# side is what a response heading may read.
_ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "SROG": ("SPECIAL INTERROGATORY", "INTERROGATORY", "SROG"),
    "SI": ("SPECIAL INTERROGATORY", "INTERROGATORY", "SROG"),
    "FROG": ("FORM INTERROGATORY", "INTERROGATORY", "FROG", "SECTION"),
    "FI": ("FORM INTERROGATORY", "INTERROGATORY", "FROG", "SECTION"),
    "ROG": ("INTERROGATORY", "ROG"),
    "INTERROGATORY": ("INTERROGATORY",),
    "RFP": (
        "REQUEST FOR PRODUCTION",
        "DEMAND FOR PRODUCTION",
        "REQUEST FOR DOCUMENTS",
        "RFP",
    ),
    "RPD": ("REQUEST FOR PRODUCTION", "DEMAND FOR PRODUCTION", "RPD"),
    "RFPD": ("REQUEST FOR PRODUCTION", "DEMAND FOR PRODUCTION", "RFPD"),
    "RFA": ("REQUEST FOR ADMISSION", "REQUESTS FOR ADMISSION", "RFA"),
    "RFAD": ("REQUEST FOR ADMISSION", "RFAD"),
}
# Response headings appear both long-form ("REQUEST FOR PRODUCTION NO. 2") and
# short-form ("RFP NO. 2:", "SROG 4", "SECTION 1.1:"). Real drafts group form
# interrogatories by section ("SECTIONS 2.1-2.8"), so the range form is a
# heading too; gate_coverage expands ranges when matching. The 2026-07-29 live
# rehearsal scored a complete 53-item draft as 53 misses because only the
# long forms were recognized here.
_HEADING_ITEM_RE = re.compile(
    r"(SPECIAL INTERROGATORY|FORM INTERROGATORY|INTERROGATORY|"
    r"REQUEST FOR PRODUCTION|DEMAND FOR PRODUCTION|REQUEST FOR DOCUMENTS|"
    r"REQUESTS? FOR ADMISSION|"
    r"\bSROG\b|\bFROG\b|\bRFPD?\b|\bRPD\b|\bRFAD?\b|\bROG\b|\bSECTIONS?\b)"
    r"[^0-9\n]{0,30}?(\d+(?:\.\d+)?)"
)
_HEADING_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|TO)\s*(\d+(?:\.\d+)?)")


def _expand_heading_numbers(heading: str) -> set[str]:
    """All item numbers a heading covers, expanding 'SECTIONS 2.1-2.8' ranges.

    Only same-major dotted ranges and plain integer ranges expand; a malformed
    or reversed range contributes its endpoints only (fail-closed toward
    reporting a miss rather than inventing coverage).
    """
    numbers: set[str] = set(re.findall(r"\d+(?:\.\d+)?", heading))
    for lo_raw, hi_raw in _HEADING_RANGE_RE.findall(heading):
        if "." in lo_raw and "." in hi_raw:
            lo_major, lo_minor = lo_raw.split(".", 1)
            hi_major, hi_minor = hi_raw.split(".", 1)
            if lo_major == hi_major and lo_minor.isdigit() and hi_minor.isdigit():
                lo_n, hi_n = int(lo_minor), int(hi_minor)
                if lo_n <= hi_n and hi_n - lo_n <= 200:
                    numbers.update(f"{lo_major}.{n}" for n in range(lo_n, hi_n + 1))
        elif lo_raw.isdigit() and hi_raw.isdigit():
            lo_n, hi_n = int(lo_raw), int(hi_raw)
            if lo_n <= hi_n and hi_n - lo_n <= 200:
                numbers.update(str(n) for n in range(lo_n, hi_n + 1))
    return numbers


class GateUsageError(Exception):
    """Usage or IO problem. Exits 2, never 1: nothing was checked."""


class Finding(NamedTuple):
    gate: str
    severity: str
    line: int | None
    message: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Smart characters to ASCII, whitespace collapsed to single spaces."""
    text = _SMART_RE.sub(lambda m: _SMART_CHARS[m.group(0)], text)
    return _WS_RE.sub(" ", text).strip()


def strip_markdown(text: str) -> str:
    """Drop inline markdown syntax, keeping every word.

    A source that writes **RESPONSE:** and a draft that quotes RESPONSE: are
    quoting the same words. Emphasis, code ticks, and blockquote carets are
    typography, not testimony, so they are removed from both sides before any
    contiguity comparison. Applied symmetrically, this can only remove false
    failures: a splice does not become contiguous when asterisks come off.
    """
    text = _MD_BLOCKQUOTE_RE.sub(" ", text)
    text = _MD_INLINE_RE.sub("", text)
    return text


def word_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric-ish tokens, for n-gram comparison."""
    return re.findall(r"[a-z0-9$.,]+", normalize(text).lower().replace(",", ""))


class SourceView:
    """One flattened, offset-indexed view of a source file.

    Two views are built per file. ``raw`` keeps every line as written. ``gutter``
    strips transcript line numbers so that a quote spanning transcript lines is
    contiguous in the flattened text. A splice does not become contiguous under
    either view, because the intervening question text is still present, so the
    second view only ever removes false failures.
    """

    def __init__(self, path: Path, kind: str, lines: list[str]) -> None:
        self.path = path
        self.kind = kind
        chunks: list[str] = []
        starts: list[int] = []
        line_numbers: list[int] = []
        cursor = 0
        for index, raw_line in enumerate(lines, start=1):
            content = raw_line
            if kind == "gutter":
                match = _GUTTER_RE.match(raw_line)
                if match:
                    content = match.group(2)
            piece = normalize(strip_markdown(content))
            if not piece:
                continue
            if chunks:
                cursor += 1
                chunks.append(" ")
            starts.append(cursor)
            line_numbers.append(index)
            chunks.append(piece)
            cursor += len(piece)
        self.text = "".join(chunks)
        self._starts = starts
        self._lines = line_numbers

    def line_at(self, offset: int) -> int:
        if not self._starts:
            return 1
        index = bisect.bisect_right(self._starts, offset) - 1
        index = max(0, min(index, len(self._lines) - 1))
        return self._lines[index]

    def find(self, needle: str) -> int:
        return self.text.find(needle)


class SourceDoc:
    """A source file plus the transcript structure the checker can see in it."""

    def __init__(self, path: Path, text: str) -> None:
        self.path = path
        self.lines = text.splitlines()
        self.views = (
            SourceView(path, "raw", self.lines),
            SourceView(path, "gutter", self.lines),
        )
        self.page_line: dict[int, tuple[int, int]] = {}
        self.stripped: dict[int, str] = {}
        self._index_transcript()

    def _index_transcript(self) -> None:
        current_page: int | None = None
        for index, raw_line in enumerate(self.lines, start=1):
            page_match = _PAGE_RE.match(raw_line)
            if page_match:
                current_page = int(page_match.group(1))
                continue
            gutter_match = _GUTTER_RE.match(raw_line)
            if not gutter_match:
                continue
            self.stripped[index] = gutter_match.group(2).strip()
            if current_page is not None:
                self.page_line[index] = (current_page, int(gutter_match.group(1)))

    @property
    def has_page_line(self) -> bool:
        return len(self.page_line) >= 5

    @property
    def is_transcript(self) -> bool:
        if len(self.stripped) < 5:
            return False
        questions = sum(1 for v in self.stripped.values() if _QUESTION_RE.match(v))
        answers = sum(1 for v in self.stripped.values() if _ANSWER_RE.match(v))
        return questions >= 2 and answers >= 2

    def governing_question_line(self, source_line: int) -> int | None:
        """Walk back from a source line to the question line that governs it."""
        candidates = [n for n in self.stripped if n <= source_line]
        for number in sorted(candidates, reverse=True):
            if _QUESTION_RE.match(self.stripped[number]):
                return number
        return None


# ---------------------------------------------------------------------------
# Draft model
# ---------------------------------------------------------------------------


class Draft:
    def __init__(self, path: Path, text: str) -> None:
        self.path = path
        self.text = text
        self.lines = text.splitlines()
        self.held_out_start = self._find_held_out()
        self.body_lines = self.lines if self.held_out_start is None else self.lines[: self.held_out_start - 1]
        self.held_out_lines = [] if self.held_out_start is None else self.lines[self.held_out_start - 1 :]
        self.body_text = "\n".join(self.body_lines)
        self.held_out_text = "\n".join(self.held_out_lines)
        self.fenced = self._fenced_lines()

    def _find_held_out(self) -> int | None:
        for index, line in enumerate(self.lines, start=1):
            if _HELD_OUT_HEADING_RE.match(line):
                return index
        return None

    def _fenced_lines(self) -> set[int]:
        inside = False
        fenced: set[int] = set()
        for index, line in enumerate(self.lines, start=1):
            if _FENCE_RE.match(line):
                inside = not inside
                fenced.add(index)
                continue
            if inside:
                fenced.add(index)
        return fenced

    def line_of_offset(self, offset: int, in_body: bool = True) -> int:
        text = self.body_text if in_body else self.text
        return text.count("\n", 0, max(0, offset)) + 1


# ---------------------------------------------------------------------------
# Quote extraction
# ---------------------------------------------------------------------------


class Quote(NamedTuple):
    raw: str
    normalized: str
    line: int
    start: int


def marker_spans(text: str) -> tuple[list[tuple[int, int]], list[int]]:
    """Walk ``{{`` / ``}}`` as a balanced pair language.

    Markers legitimately nest: a CANDIDATE OBJECTION marker may carry a
    NOT IN RECORD marker inside its basis clause. Returns the outermost spans
    plus the offsets of any opener that never closed.
    """
    stack: list[int] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        opener = text.find("{{", cursor)
        closer = text.find("}}", cursor)
        if opener < 0 and closer < 0:
            break
        if opener >= 0 and (closer < 0 or opener < closer):
            stack.append(opener)
            cursor = opener + 2
            continue
        if stack:
            start = stack.pop()
            if not stack:
                spans.append((start, closer + 2))
        cursor = closer + 2
    return spans, list(stack)


def mask_markers(text: str) -> str:
    """Blank out marker bodies, preserving length and line breaks.

    Marker text is drafting apparatus, not an assertion in the draft. A skeleton
    FILL that illustrates its own format ("Via Certified Mail and Email") is not
    a record quotation, and checking it for contiguity produces a false failure.
    """
    spans, _ = marker_spans(text)
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def extract_quotes(body_text: str) -> list[Quote]:
    """Every double-quoted string of at least ``_MIN_QUOTE_WORDS`` words.

    Straight and curly pairs both. Quoted text inside a ``{{...}}`` marker is
    excluded. A candidate spanning a blank line or a markdown heading is
    dropped: that is an unbalanced quote character, not a quotation.
    """
    body_text = mask_markers(body_text)
    quotes: list[Quote] = []
    seen: set[tuple[str, int]] = set()
    patterns = (
        re.compile(r'"([^"]{1,700})"'),
        re.compile("“([^”]{1,700})”"),
    )
    for pattern in patterns:
        for match in pattern.finditer(body_text):
            inner = match.group(1)
            if "\n\n" in inner or re.search(r"^\s*#{1,6}\s", inner, re.MULTILINE):
                continue
            normalized = normalize(strip_markdown(inner))
            if len(normalized.split()) < _MIN_QUOTE_WORDS:
                continue
            line = body_text.count("\n", 0, match.start(1)) + 1
            key = (normalized, line)
            if key in seen:
                continue
            seen.add(key)
            quotes.append(Quote(inner, normalized, line, match.start(1)))
    quotes.sort(key=lambda q: q.start)
    return quotes


def quote_variants(normalized: str) -> list[str]:
    """Normalized quote, plus the tolerated capture and citation artifacts.

    Exactly three are tolerated, all of them conventions of quotation rather
    than changes to the quoted words: a leading letter the draft case-folded to
    fit its sentence, the bracketed form of that same alteration ("[a]s hard as
    I could"), and trailing punctuation the draft added outside the source
    clause, including the closing mark of a nested quotation, where American
    convention turns a source period into a comma. Nothing else is loosened,
    and nothing inside the passage is touched.
    """
    variants: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in variants:
            variants.append(value)

    bases = [normalized]
    bracket = re.match(r"^\[([A-Za-z])\](.*)$", normalized, re.DOTALL)
    if bracket:
        letter, rest = bracket.group(1), bracket.group(2)
        bases.append(letter + rest)
        bases.append(letter.swapcase() + rest)

    for base in bases:
        add(base)
        trimmed = base
        while trimmed and trimmed[-1] in ",.;:!?-'\"":
            trimmed = trimmed[:-1].strip()
            add(trimmed)

    for base in list(variants):
        if base and base[0].isalpha():
            add(base[0].swapcase() + base[1:])
    return variants


class QuoteHit(NamedTuple):
    doc: SourceDoc
    view: SourceView
    offset: int
    variant: str


def locate_quote(quote: Quote, docs: list[SourceDoc]) -> QuoteHit | None:
    for variant in quote_variants(quote.normalized):
        for doc in docs:
            for view in doc.views:
                offset = view.find(variant)
                if offset >= 0:
                    return QuoteHit(doc, view, offset, variant)
    return None


def check_elision(quote: Quote, docs: list[SourceDoc]) -> Finding | None:
    """Resolve a quote whose omission is marked with an ellipsis.

    A marked elision is a legitimate convention and also the exact vector for
    excising a hedge, so the checker neither passes it silently nor fails it
    blind. Every segment must appear in one source, in order, separated by gaps
    small enough to be an elision rather than a jump to different testimony. The
    finding reports the omitted words so the attorney can confirm what was cut.
    """
    segments = [s.strip() for s in _ELLIPSIS_SPLIT_RE.split(quote.normalized)]
    segments = [s for s in segments if s]
    if len(segments) < 2:
        return None
    for doc in docs:
        for view in doc.views:
            bounds: list[tuple[int, int]] = []
            cursor = 0
            for index, segment in enumerate(segments):
                candidates = quote_variants(segment) if index == 0 else [segment]
                if index == len(segments) - 1:
                    candidates = quote_variants(segment)
                located = -1
                length = 0
                for candidate in candidates:
                    found = view.text.find(candidate, cursor)
                    if found >= 0:
                        located, length = found, len(candidate)
                        break
                if located < 0:
                    break
                bounds.append((located, located + length))
                cursor = located + length
            if len(bounds) != len(segments):
                continue
            gaps = [view.text[bounds[i][1] : bounds[i + 1][0]].strip() for i in range(len(bounds) - 1)]
            if any(len(gap) > _MAX_ELISION_CHARS for gap in gaps):
                continue

            # Discipline rule 6: an ellipsis may never span an intervening
            # question. A gap that swallows one is a splice wearing an
            # ellipsis, and it fails rather than warns.
            spanned: list[str] = []
            for index in range(len(bounds) - 1):
                first = view.line_at(bounds[index][1])
                last = view.line_at(bounds[index + 1][0])
                for line_no in range(first, last + 1):
                    content = doc.stripped.get(line_no, "")
                    if _QUESTION_RE.match(content):
                        spanned.append(f"{doc.path.name} line {line_no}: {content}")
            if spanned:
                return Finding(
                    "2a",
                    SEVERITY_FAIL,
                    quote.line,
                    "ellipsis spans an intervening question, which joins an "
                    f'answer to testimony it did not give: "{quote.normalized}"',
                    " | ".join(spanned[:3]),
                )

            return Finding(
                "2a",
                SEVERITY_WARN,
                quote.line,
                f'quoted passage elides source text with an ellipsis; confirm no hedge was cut: "{quote.normalized}"',
                f"{view.path.name} omits: " + " / ".join(f'"{g}"' for g in gaps),
            )
    return None


def closest_region(quote: str, docs: list[SourceDoc]) -> tuple[float, str, str] | None:
    """Best fuzzy region for a missing quote, to aid attorney review.

    Anchors on the quote's leading words, then its trailing words. Returns
    (ratio, source label, source snippet) or None when nothing anchors.
    """
    words = quote.split()
    best: tuple[float, str, str] | None = None
    anchor_sets: list[str] = []
    for size in (5, 4, 3):
        if len(words) >= size:
            anchor_sets.append(" ".join(words[:size]))
    for size in (5, 4, 3):
        if len(words) >= size:
            anchor_sets.append(" ".join(words[-size:]))
    for anchor in anchor_sets:
        for doc in docs:
            for view in doc.views:
                start = 0
                hits = 0
                while hits < 25:
                    index = view.text.find(anchor, start)
                    if index < 0:
                        break
                    hits += 1
                    left = max(0, index - len(quote))
                    window = view.text[left : index + len(quote) + 80]
                    ratio = difflib.SequenceMatcher(None, quote, window).ratio()
                    if best is None or ratio > best[0]:
                        label = f"{view.path.name}:{view.line_at(index)}"
                        snippet = window.strip()
                        if len(snippet) > 300:
                            snippet = snippet[:300] + " ..."
                        best = (ratio, label, snippet)
                    start = index + 1
        if best is not None:
            break
    return best


# ---------------------------------------------------------------------------
# Gate 2a and 2b
# ---------------------------------------------------------------------------


def gate_quote_contiguity(draft: Draft, docs: list[SourceDoc]) -> tuple[list[Finding], list[tuple[Quote, QuoteHit]]]:
    findings: list[Finding] = []
    hits: list[tuple[Quote, QuoteHit]] = []
    if not docs:
        findings.append(
            Finding(
                "2a",
                SEVERITY_FAIL,
                None,
                "no source documents were readable, so no quote can be verified",
            )
        )
        return findings, hits
    for quote in extract_quotes(draft.body_text):
        hit = locate_quote(quote, docs)
        if hit is not None:
            hits.append((quote, hit))
            continue
        elision = check_elision(quote, docs)
        if elision is not None:
            findings.append(elision)
            continue
        detail = ""
        near = closest_region(quote.normalized, docs)
        if near is not None:
            ratio, label, snippet = near
            detail = f"closest region {label} (similarity {ratio:.2f}): {snippet}"
        else:
            detail = "no anchoring region found in any source"
        findings.append(
            Finding(
                "2a",
                SEVERITY_FAIL,
                quote.line,
                f'quoted passage is not contiguous in any source: "{quote.normalized}"',
                detail,
            )
        )
    return findings, hits


def citation_after(body_text: str, quote: Quote) -> tuple[str, int, int] | None:
    """The page:line range cited for a quote, as (kind, page, line) pairs.

    Looks forward from the quote's end to the end of its paragraph, capped, for
    the first range or point cite. Returns None when the sentence carries
    neither.
    """
    tail_start = quote.start + len(quote.raw)
    window = body_text[tail_start : tail_start + 400]
    paragraph_end = window.find("\n\n")
    if paragraph_end >= 0:
        window = window[:paragraph_end]
    window = normalize(window)
    range_match = _RANGE_RE.search(window)
    if range_match:
        page_a = int(range_match.group(1))
        line_a = int(range_match.group(2))
        third = int(range_match.group(3))
        fourth = range_match.group(4)
        if fourth is not None:
            page_b, line_b = third, int(fourth)
        else:
            page_b, line_b = page_a, third
        return ("range", (page_a, line_a), (page_b, line_b))  # type: ignore[return-value]
    point_match = _POINT_RE.search(window)
    if point_match:
        point = (int(point_match.group(1)), int(point_match.group(2)))
        return ("point", point, point)  # type: ignore[return-value]
    return None


class Occurrence(NamedTuple):
    doc: SourceDoc
    view: SourceView
    offset: int
    pin: tuple[int, int]


def transcript_occurrences(quote: Quote, docs: list[SourceDoc]) -> list[Occurrence]:
    """Every place a quote appears in a page:line transcript, with its pin.

    A phrase like "I thought I had room" can appear in more than one document
    and more than once in the same one. Pairing must be judged at the place the
    draft cited, not at whichever copy the contiguity search happened to reach
    first.
    """
    found: list[Occurrence] = []
    variants = quote_variants(quote.normalized)
    for doc in docs:
        if not (doc.is_transcript and doc.has_page_line):
            continue
        for view in doc.views:
            for variant in variants:
                start = 0
                while len(found) < 60:
                    index = view.text.find(variant, start)
                    if index < 0:
                        break
                    pin = doc.page_line.get(view.line_at(index))
                    if pin is not None:
                        found.append(Occurrence(doc, view, index, pin))
                    start = index + 1
                if found:
                    break
    return found


def gate_question_pairing(draft: Draft, docs: list[SourceDoc], hits: list[tuple[Quote, QuoteHit]]) -> list[Finding]:
    """Gate 2b. Fail-open by design wherever the record cannot answer the question.

    The only FAIL class is an explicit cited range that excludes the line of the
    question the quoted answer actually answered. Everything else (no transcript
    structure, no page:line markers, no range on the sentence) is reported as an
    INFO note so the attorney knows the sub-check did not run.
    """
    findings: list[Finding] = []

    def note(quote: Quote, message: str) -> None:
        findings.append(Finding("2b", SEVERITY_INFO, quote.line, message))

    def question_pin_for(occurrence: Occurrence) -> tuple[tuple[int, int], str] | None:
        line_no = occurrence.doc.governing_question_line(occurrence.view.line_at(occurrence.offset))
        if line_no is None or line_no not in occurrence.doc.page_line:
            return None
        return (
            occurrence.doc.page_line[line_no],
            f"{occurrence.doc.path.name} line {line_no}: {occurrence.doc.stripped.get(line_no, '')}",
        )

    for quote, hit in hits:
        if hit.doc.is_transcript and not hit.doc.has_page_line:
            note(
                quote,
                f"{hit.doc.path.name} has no parseable page:line markers, so "
                "question pairing was not checked (fail-open)",
            )
            continue

        occurrences = transcript_occurrences(quote, docs)
        if not occurrences:
            # Nothing with transcript structure carries this quote, so there is
            # no question to pair it with.
            continue

        cite = citation_after(draft.body_text, quote)
        if cite is None:
            note(
                quote,
                "no page:line cite follows this quote, so pairing was not "
                "checked (fail-open); note that an uncited quotation is a gate 2 "
                "defect on its own",
            )
            continue

        kind, start_pin, end_pin = cite
        if kind != "range":
            pinned = question_pin_for(occurrences[0])
            where = f"; the question it answered is at {pinned[0][0]}:{pinned[0][1]}" if pinned else ""
            note(
                quote,
                f"quote cites a single point {start_pin[0]}:{start_pin[1]} rather "
                f"than a range, so pairing was not checked (fail-open){where}",
            )
            continue

        in_range = [o for o in occurrences if start_pin <= o.pin <= end_pin]
        if not in_range:
            elsewhere = ", ".join(f"{o.doc.path.name} {o.pin[0]}:{o.pin[1]}" for o in occurrences[:4])
            if hit.doc not in {o.doc for o in occurrences}:
                # The quote's contiguous home is a document without transcript
                # structure, so the cite may point there instead. Fail open.
                note(
                    quote,
                    "quoted passage is contiguous in "
                    f"{hit.doc.path.name}, which carries no page:line structure, "
                    "so the cited range could not be adjudicated (fail-open); it "
                    f"also appears at {elsewhere}",
                )
                continue
            findings.append(
                Finding(
                    "2b",
                    SEVERITY_FAIL,
                    quote.line,
                    f"quoted passage does not appear within the cited range "
                    f"{start_pin[0]}:{start_pin[1]} to {end_pin[0]}:{end_pin[1]}: "
                    f'"{quote.normalized}"',
                    f"it appears at {elsewhere}",
                )
            )
            continue

        pinned = question_pin_for(in_range[0])
        if pinned is None:
            note(
                quote,
                "no governing question located above the quoted passage in "
                f"{in_range[0].doc.path.name}, so pairing was not checked "
                "(fail-open)",
            )
            continue
        question_pin, context = pinned
        if start_pin <= question_pin <= end_pin:
            continue
        findings.append(
            Finding(
                "2b",
                SEVERITY_FAIL,
                quote.line,
                f"cited range {start_pin[0]}:{start_pin[1]} to "
                f"{end_pin[0]}:{end_pin[1]} excludes the question this answer "
                f"answered, at {question_pin[0]}:{question_pin[1]}: "
                f'"{quote.normalized}"',
                context,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Gate 1 and 6
# ---------------------------------------------------------------------------


def _line_map(text: str) -> tuple[list[str], list[int]]:
    """Word tokens for ``text`` with the draft line each token came from."""
    tokens: list[str] = []
    lines: list[int] = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        for token in word_tokens(raw_line):
            tokens.append(token)
            lines.append(index)
    return tokens, lines


def _ngrams(tokens: Iterable[str], size: int) -> set[tuple[str, ...]]:
    items = list(tokens)
    return {tuple(items[i : i + size]) for i in range(len(items) - size + 1)}


def _overlap_findings(
    gate: str,
    body_text: str,
    other_text: str,
    label: str,
) -> list[Finding]:
    findings: list[Finding] = []
    other_grams = _ngrams(word_tokens(other_text), _HELD_OUT_NGRAM)
    if not other_grams:
        return findings
    tokens, lines = _line_map(body_text)
    index = 0
    while index <= len(tokens) - _HELD_OUT_NGRAM:
        window = tuple(tokens[index : index + _HELD_OUT_NGRAM])
        if window in other_grams:
            end = index + _HELD_OUT_NGRAM
            while end < len(tokens) and tuple(tokens[end - _HELD_OUT_NGRAM + 1 : end + 1]) in other_grams:
                end += 1
            run = " ".join(tokens[index:end])
            findings.append(
                Finding(
                    gate,
                    SEVERITY_FAIL,
                    lines[index],
                    f"draft body reproduces {end - index} consecutive words from {label}",
                    run[:300],
                )
            )
            index = end
            continue
        index += 1
    return findings


def gate_held_out_and_wall(draft: Draft, held_out_docs: list[tuple[Path, str]]) -> list[Finding]:
    findings: list[Finding] = []

    for path, text in held_out_docs:
        findings.extend(_overlap_findings("1", draft.body_text, text, path.name))

    if draft.held_out_text:
        findings.extend(
            _overlap_findings(
                "1",
                draft.body_text,
                draft.held_out_text,
                "the draft's own HELD OUT section",
            )
        )

    # Gate 6 is scoped to the body. The HELD OUT section is a firm-internal
    # register of references and is stripped before anything is sent, so its
    # document names are not wall violations.
    #
    # A draft that cites its record by filename repeats the same violation on
    # every cite, so hits are grouped: one finding per distinct string, with
    # every line it occupies. The failure is unchanged, the report is readable.
    hits: dict[tuple[str, str], list[int]] = {}
    for index, raw_line in enumerate(draft.body_lines, start=1):
        for pattern, description in _INTERNAL_PATH_PATTERNS:
            for match in re.finditer(pattern, raw_line):
                hits.setdefault((description, match.group(0)), []).append(index)

    for (description, matched), lines in hits.items():
        shown = ", ".join(str(n) for n in lines[:12])
        if len(lines) > 12:
            shown += f", and {len(lines) - 12} more"
        occurrences = "1 occurrence" if len(lines) == 1 else f"{len(lines)} occurrences"
        findings.append(
            Finding(
                "6",
                SEVERITY_FAIL,
                lines[0],
                f"{description} in draft body: {matched}",
                f"{occurrences} at line(s) {shown}",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Gate 3
# ---------------------------------------------------------------------------


def _sentence_around(text: str, offset: int) -> str:
    start = max(
        text.rfind(". ", 0, offset),
        text.rfind("\n", 0, offset),
        text.rfind("! ", 0, offset),
        text.rfind("? ", 0, offset),
    )
    start = 0 if start < 0 else start + 1
    ends = [text.find(marker, offset) for marker in (". ", "\n", "! ", "? ")]
    ends = [e for e in ends if e >= 0]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end]


def gate_self_certification(draft: Draft) -> list[Finding]:
    findings: list[Finding] = []
    body = draft.body_text
    for pattern, requires_uncited, description in _SELF_CERT_PATTERNS:
        for match in re.finditer(pattern, body, re.IGNORECASE):
            sentence = _sentence_around(body, match.start())
            if requires_uncited and re.search(r"\([^)]{3,}\)", sentence):
                continue
            line = body.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    "3",
                    SEVERITY_FAIL,
                    line,
                    f"{description}: {match.group(0)}",
                    normalize(sentence)[:300],
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Gate 7
# ---------------------------------------------------------------------------


def parse_propounded(path: Path) -> list[str]:
    items: list[str] = []
    for raw_line in read_text(path).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            items.append(line)
    return items


def _item_key(item: str) -> tuple[tuple[str, ...], str] | None:
    match = re.match(r"^\s*([A-Za-z]+)[\s.:#-]*(\d+(?:\.\d+)?)\s*$", item)
    if not match:
        return None
    kind = match.group(1).upper()
    number = match.group(2)
    aliases = _ITEM_ALIASES.get(kind)
    if aliases is None:
        aliases = (kind,)
    return aliases, number


def gate_coverage(draft: Draft, items: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    headings: list[tuple[int, str]] = []
    for index, raw_line in enumerate(draft.body_lines, start=1):
        heading = _HEADING_RE.match(raw_line)
        if heading:
            headings.append((index, normalize(heading.group(2)).upper()))
            continue
        bare = normalize(raw_line).strip("*_ ").upper()
        if _HEADING_ITEM_RE.search(bare) and len(bare) <= 120:
            headings.append((index, bare))

    matched_headings: set[int] = set()
    for item in items:
        key = _item_key(item)
        if key is None:
            findings.append(
                Finding(
                    "7",
                    SEVERITY_WARN,
                    None,
                    f"propounded item is not parseable as <type> <number>: {item}",
                )
            )
            continue
        aliases, number = key
        found = False
        for line_no, heading in headings:
            if normalize(item).upper() in heading:
                found = True
                matched_headings.add(line_no)
                continue
            for alias in aliases:
                if alias not in heading:
                    continue
                if number in _expand_heading_numbers(heading):
                    found = True
                    matched_headings.add(line_no)
                    break
            if found:
                break
        if not found:
            findings.append(
                Finding(
                    "7",
                    SEVERITY_FAIL,
                    None,
                    f"propounded item received no response heading in the draft: {item}",
                )
            )

    for line_no, heading in headings:
        if line_no in matched_headings:
            continue
        if not _HEADING_ITEM_RE.search(heading):
            continue
        findings.append(
            Finding(
                "7",
                SEVERITY_WARN,
                line_no,
                f"response heading has no matching propounded item: {heading}",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Gate 8
# ---------------------------------------------------------------------------


class SprogItem(NamedTuple):
    line: int
    label: str
    text: str


def _sprog_items(draft: Draft) -> list[SprogItem]:
    starts: list[tuple[int, str]] = []
    for index, raw_line in enumerate(draft.body_lines, start=1):
        match = _SPROG_ITEM_RE.search(raw_line)
        if match:
            starts.append((index, normalize(raw_line).strip("#* ")))
    items: list[SprogItem] = []
    for position, (line_no, label) in enumerate(starts):
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(draft.body_lines)
        body = "\n".join(draft.body_lines[line_no - 1 : end])
        items.append(SprogItem(line_no, label, body))
    return items


def gate_sprog_lint(draft: Draft) -> list[Finding]:
    findings: list[Finding] = []
    if not _SPROG_SCOPE_RE.search(draft.body_text):
        return [
            Finding(
                "8",
                SEVERITY_INFO,
                None,
                "no special interrogatory sections found, so the subpart lint did not run",
            )
        ]
    items = _sprog_items(draft)
    if not items:
        return [
            Finding(
                "8",
                SEVERITY_INFO,
                None,
                "special interrogatories are mentioned but no numbered items were "
                "found, so the subpart lint did not run",
            )
        ]
    for item in items:
        item_lines = item.text.splitlines()
        lettered: list[str] = []
        for offset, raw_line in enumerate(item_lines):
            if _LETTERED_LEAD_RE.match(raw_line):
                findings.append(
                    Finding(
                        "8",
                        SEVERITY_FAIL,
                        item.line + offset,
                        "special interrogatory carries an explicit lettered "
                        "subpart, which CCP 2030.060(f) does not permit: "
                        f"{normalize(raw_line)[:160]}",
                        item.label,
                    )
                )
            lettered.extend(_LETTERED_INLINE_RE.findall(raw_line))
        distinct = sorted(set(lettered))
        if len(distinct) >= 2 and "a" in distinct and "b" in distinct:
            findings.append(
                Finding(
                    "8",
                    SEVERITY_FAIL,
                    item.line,
                    "special interrogatory carries inline lettered subparts "
                    f"({', '.join('(' + d + ')' for d in distinct)}), which "
                    "CCP 2030.060(f) does not permit",
                    item.label,
                )
            )
        flat = normalize(item.text)
        if re.search(r";[^;]{0,200}?\band\s+(?:state|identify|describe|list)\b", flat, re.IGNORECASE):
            findings.append(
                Finding(
                    "8",
                    SEVERITY_WARN,
                    item.line,
                    "semicolon-chained clauses joined by a second directive; this "
                    "reads as more than one fact per interrogatory",
                    item.label,
                )
            )
        if re.search(r"each and every\b.{0,200}?\band\b", flat, re.IGNORECASE):
            findings.append(
                Finding(
                    "8",
                    SEVERITY_WARN,
                    item.line,
                    "conjunctive 'each and every ... and ...' pattern; likely compound",
                    item.label,
                )
            )
        directives = re.findall(r"\b(state|identify|describe|list|set forth)\b", flat, re.IGNORECASE)
        if len(directives) >= 2:
            findings.append(
                Finding(
                    "8",
                    SEVERITY_WARN,
                    item.line,
                    f"{len(directives)} directives in one interrogatory "
                    f"({', '.join(d.lower() for d in directives)}); one fact per "
                    "special interrogatory",
                    item.label,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Gate 9 and marker integrity
# ---------------------------------------------------------------------------


def gate_visible_markers(draft: Draft) -> list[Finding]:
    findings: list[Finding] = []
    text = draft.text

    for match in re.finditer(r"<!--(.*?)-->", text, re.DOTALL):
        inner = match.group(1)
        for token in _VISIBLE_MARKER_TOKENS:
            if token in inner:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    Finding(
                        "9",
                        SEVERITY_FAIL,
                        line,
                        f"{token}" + "}} marker is inside an HTML comment, so it vanishes on render",
                        normalize(inner)[:200],
                    )
                )

    _, unclosed = marker_spans(text)
    for offset in unclosed:
        line = text.count("\n", 0, offset) + 1
        findings.append(
            Finding(
                "9",
                SEVERITY_FAIL,
                line,
                "unclosed marker: an opening {{ has no matching }}",
                normalize(text[offset : offset + 120]),
            )
        )

    for index, raw_line in enumerate(draft.lines, start=1):
        if index not in draft.fenced:
            continue
        for token in _VISIBLE_MARKER_TOKENS:
            if token in raw_line:
                findings.append(
                    Finding(
                        "9",
                        SEVERITY_WARN,
                        index,
                        f"{token}" + "}} marker sits inside a code fence, where it may not read as a reservation",
                        normalize(raw_line)[:200],
                    )
                )
                break
    return findings


def gate_marker_integrity(draft: Draft) -> list[Finding]:
    findings: list[Finding] = []
    for match in _FILL_RE.finditer(draft.text):
        inner = match.group(1)
        if "|" in inner:
            continue
        line = draft.text.count("\n", 0, match.start()) + 1
        findings.append(
            Finding(
                "MI",
                SEVERITY_WARN,
                line,
                "FILL marker carries no source note; the skeleton convention is {{FILL: what goes here | source}}",
                normalize(match.group(0))[:200],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GateUsageError(f"cannot read {path}: {exc}") from exc


def collect_files(spec: str, what: str) -> list[Path]:
    paths: list[Path] = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        candidate = Path(piece).expanduser()
        if candidate.is_dir():
            for found in sorted(candidate.rglob("*")):
                if found.is_file() and found.suffix.lower() in _SOURCE_SUFFIXES:
                    paths.append(found)
        elif candidate.is_file():
            paths.append(candidate)
        else:
            raise GateUsageError(f"{what} not found: {piece}")
    return paths


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            _SEVERITY_ORDER.get(f.severity, 9),
            f.gate,
            f.line if f.line is not None else 0,
        ),
    )


def render_report(
    draft_path: Path,
    source_paths: list[Path],
    held_out_paths: list[Path],
    findings: list[Finding],
) -> str:
    lines = [
        "DRAFTING GATE CHECK",
        f"  draft:    {draft_path}",
        f"  sources:  {len(source_paths)} file(s)",
    ]
    if held_out_paths:
        lines.append(f"  held out: {len(held_out_paths)} file(s)")
    lines.append("")

    if not findings:
        lines.append("  no findings")
    for finding in findings:
        where = f"line {finding.line}" if finding.line is not None else "document"
        lines.append(f"  {finding.severity:<4}  gate {finding.gate:<3} {where:<12} {finding.message}")
        if finding.detail:
            lines.append(f"          {finding.detail}")

    fails = sum(1 for f in findings if f.severity == SEVERITY_FAIL)
    warns = sum(1 for f in findings if f.severity == SEVERITY_WARN)
    infos = sum(1 for f in findings if f.severity == SEVERITY_INFO)
    lines.append("")
    verdict = "FAIL" if fails else "PASS"
    lines.append(f"RESULT: {verdict} ({fails} failure(s), {warns} warning(s), {infos} note(s))")
    return "\n".join(lines)


def render_json(
    draft_path: Path,
    source_paths: list[Path],
    held_out_paths: list[Path],
    findings: list[Finding],
) -> str:
    fails = sum(1 for f in findings if f.severity == SEVERITY_FAIL)
    payload = {
        "draft": str(draft_path),
        "sources": [str(p) for p in source_paths],
        "heldOut": [str(p) for p in held_out_paths],
        "result": "fail" if fails else "pass",
        "counts": {
            "fail": fails,
            "warn": sum(1 for f in findings if f.severity == SEVERITY_WARN),
            "info": sum(1 for f in findings if f.severity == SEVERITY_INFO),
        },
        "findings": [
            {
                "gate": f.gate,
                "severity": f.severity,
                "line": f.line,
                "message": f.message,
                "detail": f.detail,
            }
            for f in findings
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drafting_gate_check.py",
        description=(
            "Mechanical quality gate for attorney-review drafts. Exit 0 clean, "
            "1 on gate failure, 2 on usage or IO error."
        ),
    )
    parser.add_argument("--draft", required=True, help="path to the draft markdown")
    parser.add_argument(
        "--sources",
        required=True,
        help="directory (or comma list of files and directories) of record sources",
    )
    parser.add_argument(
        "--held-out",
        default="",
        help="file or comma list of documents held out for privilege review",
    )
    parser.add_argument(
        "--propounded",
        default="",
        help="file listing one propounded item id per line, for gate 7",
    )
    parser.add_argument(
        "--sprog-lint",
        action="store_true",
        help="run the special-interrogatory subpart lint (gate 8)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine JSON")
    return parser


def run_checks(
    draft: Draft,
    docs: list[SourceDoc],
    held_out_docs: list[tuple[Path, str]],
    propounded: list[str] | None,
    sprog_lint: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    contiguity, hits = gate_quote_contiguity(draft, docs)
    findings.extend(contiguity)
    findings.extend(gate_question_pairing(draft, docs, hits))
    findings.extend(gate_held_out_and_wall(draft, held_out_docs))
    findings.extend(gate_self_certification(draft))
    if propounded is not None:
        findings.extend(gate_coverage(draft, propounded))
    if sprog_lint:
        findings.extend(gate_sprog_lint(draft))
    findings.extend(gate_visible_markers(draft))
    findings.extend(gate_marker_integrity(draft))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    try:
        draft_path = Path(args.draft).expanduser()
        if not draft_path.is_file():
            raise GateUsageError(f"draft not found: {draft_path}")
        draft = Draft(draft_path, read_text(draft_path))

        source_paths = collect_files(args.sources, "sources")
        if not source_paths:
            raise GateUsageError(f"no readable source files under: {args.sources}")
        docs = [SourceDoc(p, read_text(p)) for p in source_paths]

        held_out_paths = collect_files(args.held_out, "held-out") if args.held_out else []
        held_out_docs = [(p, read_text(p)) for p in held_out_paths]

        propounded: list[str] | None = None
        if args.propounded:
            propounded_path = Path(args.propounded).expanduser()
            if not propounded_path.is_file():
                raise GateUsageError(f"propounded items file not found: {propounded_path}")
            propounded = parse_propounded(propounded_path)
    except GateUsageError as exc:
        sys.stderr.write(f"[drafting-gate] {exc}\n")
        return 2

    try:
        findings = run_checks(draft, docs, held_out_docs, propounded, args.sprog_lint)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash open
        findings = [
            Finding(
                "checker",
                SEVERITY_FAIL,
                None,
                f"the checker raised while evaluating this draft: {exc!r}",
                "treat this run as a failure; the draft was not fully checked",
            )
        ]

    findings = sort_findings(findings)
    if args.json:
        print(render_json(draft_path, source_paths, held_out_paths, findings))
    else:
        print(render_report(draft_path, source_paths, held_out_paths, findings))
    return 1 if any(f.severity == SEVERITY_FAIL for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
