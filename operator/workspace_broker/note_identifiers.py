"""Identifier corroboration for an SMD note: every identifier in the words SMD
has the Operator say must appear in the colleague's own request email.

WHY (2026-10-15). A note is sent by the smd-note skill's DELIVER turn, and the
overlay's outbound identifier gate (``plugins/hermes-smd-trust/outbound.py``
``_check_identifiers`` over ``shared/identifier_filter.check``) judges a draft
against the identifiers the turn READ. The turn reads the note's own text
(``note_job_status`` seeds provenance), so on that path the note verifies
itself: the gate cannot tell a matter number SMD typed wrong from one the firm
wrote. The one place a note can be checked against the firm's own words is
here, at record time, against the anchor email the broker fetched itself. A
note naming a matter number the request never mentioned is the note most
likely to be about the wrong matter, and it is refused.

WHAT COUNTS AS AN IDENTIFIER. A faithful Python mirror of the overlay gate's
BLOCKING set (``outbound._BLOCKING_KINDS``: every ``IdKind`` except NAME):

* ``identifier_filter._A_NUMBER_RE``, ``_RECEIPT_RE``, ``_SSN_RE``, ``_CASE_RE``
  and ``_DATE_RES`` (the overlay's current forms, ordinal day suffixes
  included), canonicalized exactly as ``_canon_a_number`` / ``_canon_digits`` /
  ``_canon_date`` do, so "June 8, 2026" in a note matches "6/8/26" in the email;
* a DATE that is today or yesterday (UTC) is exempt, as the gate's
  ``_ambient_dates`` / ``_is_ambient`` exempt it: the clock vouches for it;
* NAME is excluded, as the gate excludes it: a note's greeting names the person
  the binding already locks the reply to;
* PAIR is not checked: the anchor is read text, and read text registers no
  associations (``ProvenanceRegister.add_read_text``), so the gate would judge
  none either.

PLUS bare matter-number-like digit runs (``identifier_filter.
_BARE_MATTER_NUMBER_RE``, three to ten digits). The overlay sees a bare number
only when a structured record registered it (``check``'s register-anchored pass)
and documents a fabricated bare number as its "residual blindness". A&P's matter
numbers are bare digit runs, so here every such run in the note, outside the
spans of the structured hits above, must appear as a digit run in the anchor.
That is stricter than the overlay on purpose: a refused note costs SMD one
rewrite; a note about the wrong matter costs the firm's trust.

Source of truth for the shapes: the overlay's ``shared/identifier_filter.py``,
the one copy, which the outbound gate runs. A shape change
there is a change here; ``tests/test_note_verbs.py`` pins the parity.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable
from dataclasses import dataclass

# identifier_filter._A_NUMBER_RE / _RECEIPT_RE / _SSN_RE / _CASE_RE, verbatim.
_A_NUMBER_RE = re.compile(r"\bA#?[-\s]?(?:\d[-\s]?){8,9}\b")
_RECEIPT_RE = re.compile(r"\b[A-Z]{3}\d{10}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CASE_RE = re.compile(
    r"\b(?:\d{1,2}:\d{2}-[a-z]{2}-\d{3,6}"
    r"|No\.?\s?\d{2,4}-\d{2,6}"
    r"|\d{4}-[A-Z]{2}-\d{3,4}"
    r"|[A-Z]{2}-\d{4}-\d{4})\b",
    re.IGNORECASE,
)
# identifier_filter._DATE_RES (the overlay's, with ordinal day suffixes).
_DATE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{1,2}-\d{1,2}-\d{2,4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?![\d-])"),
    re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|"
        r"Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June"
        r"|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun"
        r"|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{4}\b",
        re.IGNORECASE,
    ),
)
_DATE_STRPTIME_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m-%d-%y",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)
# identifier_filter._BARE_MATTER_NUMBER_RE, word-anchored for a scan of prose.
_BARE_RUN_RE = re.compile(r"(?<![0-9])\d{3,10}(?![0-9])")


@dataclass(frozen=True)
class Hit:
    kind: str
    raw: str
    canonical: str
    span: tuple[int, int]


def _canon_digits(raw: str) -> str:
    """identifier_filter._canon_digits."""
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def _canon_a_number(raw: str) -> str:
    """identifier_filter._canon_a_number."""
    return "A" + re.sub(r"\D", "", raw)


def _canon_date(raw: str) -> str | None:
    """identifier_filter._canon_date."""
    s = re.sub(r"\s+", " ", raw.strip()).rstrip(".")
    s = s.replace(".", "")
    s = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    for fmt in _DATE_STRPTIME_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def structured(text: str) -> list[Hit]:
    """identifier_filter._extract(text, include_names=False): every structured
    identifier the overlay gate would block on."""
    hits: list[Hit] = []
    if not isinstance(text, str) or not text:
        return hits
    for m in _A_NUMBER_RE.finditer(text):
        hits.append(Hit("a_number", m.group(0), _canon_a_number(m.group(0)), m.span()))
    for m in _RECEIPT_RE.finditer(text):
        hits.append(Hit("receipt_number", m.group(0), _canon_digits(m.group(0)), m.span()))
    for m in _SSN_RE.finditer(text):
        hits.append(Hit("ssn", m.group(0), _canon_digits(m.group(0)), m.span()))
    for m in _CASE_RE.finditer(text):
        hits.append(Hit("case_number", m.group(0), _canon_digits(m.group(0)), m.span()))
    for pat in _DATE_RES:
        for m in pat.finditer(text):
            canon = _canon_date(m.group(0))
            if canon:
                hits.append(Hit("date", m.group(0), canon, m.span()))
    return [h for h in hits if h.canonical]


def _inside(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def bare_runs(text: str, *, outside: Iterable[tuple[int, int]] = ()) -> list[Hit]:
    """Three-to-ten-digit runs not inside one of ``outside``'s spans."""
    spans = list(outside)
    return [
        Hit("bare_number", m.group(0), m.group(0), m.span())
        for m in _BARE_RUN_RE.finditer(text or "")
        if not _inside(m.span(), spans)
    ]


def ambient_dates(now: datetime.datetime | None = None) -> frozenset[str]:
    """outbound._ambient_dates: UTC today and yesterday."""
    today = (now or datetime.datetime.now(datetime.timezone.utc)).date()
    return frozenset({today.isoformat(), (today - datetime.timedelta(days=1)).isoformat()})


#: A range the requester wrote: "120-125", "120 to 125", "120 through 125".
#: Never a piece of a longer hyphenated run (a phone number's "555-1234").
_RANGE_RE = re.compile(
    r"(?<![\d\-\u2013])(\d{1,10})(?:\s*[-\u2013]\s*|\s+(?:to|through|thru)\s+)(\d{1,10})(?![\d\-\u2013])",
    re.IGNORECASE,
)
#: A range counts only as a COUNT the requester asked for: a cutting verb
#: before it and a discovery noun in the same sentence ("reduce the special
#: rogs to the most important 120-125"). "pages 1-500" and "RFP 1-45" are
#: references to items, not counts, and vouch for nothing between their ends.
_COUNT_VERB_RE = re.compile(r"\b(?:cut|trim|reduce|limit|narrow|pare|down\s+to)\b", re.IGNORECASE)
_COUNT_NOUN_RE = re.compile(
    r"\b(?:interrogator(?:y|ies)|rogs?|requests?|rfas?|rfps?|questions)\b",
    re.IGNORECASE,
)
#: The word right before a range that makes it a reference ("RFP 1-45", "pages
#: 1-500", "Nos. 3-9"), never a count.
_REFERENCE_BEFORE_RE = re.compile(
    r"(?:\b(?:rfps?|rfas?|requests?|interrogator(?:y|ies)|rogs?|pages?|pp?\.?|nos?\.?|numbers?|items?|lines?)|#)\s*$",
    re.IGNORECASE,
)
_SENTENCE_BREAK_RE = re.compile(r"[.!?\n]")
#: What before a digit run makes it an identifier, never a run-together count:
#: "Matter 203795", "Case No. 203795", "#203795", "phone 6025551".
_ID_BEFORE_RE = re.compile(
    r"(?:\b(?:matter|case|file|claim|policy|account|acct|invoice|order|ref|reference|no|number|phone|tel|"
    r"fax|cell|mobile|ph|ext)\b\.?|#)\s*[:#.\-]?\s*$",
    re.IGNORECASE,
)


def _split_readings(run: str) -> tuple[str, str] | None:
    """A run-together figure of 4 to 6 digits read as two numbers of equal
    length, the first no larger than the second ("120125" -> 120 and 125), or
    None."""
    if len(run) % 2 or not 4 <= len(run) <= 6:
        return None
    half = len(run) // 2
    lo, hi = run[:half], run[half:]
    if lo.startswith("0") or hi.startswith("0") or int(lo) > int(hi):
        return None
    return lo, hi


def _sentence_around(text: str, start: int, end: int) -> tuple[str, str]:
    """The text of the match's own sentence before it and after it."""
    breaks = [m.end() for m in _SENTENCE_BREAK_RE.finditer(text, 0, start)]
    head = text[breaks[-1] if breaks else 0 : start]
    after = _SENTENCE_BREAK_RE.search(text, end)
    return head, text[end : after.start() if after else len(text)]


def anchor_numbers(anchor: str) -> tuple[set[str], list[tuple[int, int]]]:
    """``(runs, ranges)``: every digit run the anchor carries, with the two
    readings of a run-together count, and every count range the anchor states.

    WHY (A&P, 2026-11-23). An attorney asked to cut a set to "the most important
    120125", meaning 120 to 125; SMD's note saying "cut to the 120 most
    important" was refused because 120 is not literally in his email. A number
    read from his own figure (its two halves only), or one inside a count range
    he wrote, is his number. A matter, case or phone number is never split, and
    a reference range ("pages 1-500") vouches for nothing between its ends."""
    text = anchor or ""
    runs = {m.group(0) for m in _BARE_RUN_RE.finditer(text)}
    ranges: list[tuple[int, int]] = []
    shaped = [h.span for h in structured(text)]
    for m in re.finditer(r"(?<![0-9])\d+(?![0-9])", text):
        split = _split_readings(m.group(0))
        if not split or _inside(m.span(), shaped):
            continue
        if _ID_BEFORE_RE.search(text[max(0, m.start() - 30) : m.start()]):
            continue
        runs.update(split)
    for m in _RANGE_RE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        # A count range, not a date or case number, and not a phone number's
        # tail: endpoints of one length, or both under a thousand.
        same = len(m.group(1)) == len(m.group(2)) or hi < 1000
        if not (lo <= hi and same) or _inside(m.span(), shaped):
            continue
        head, tail = _sentence_around(text, m.start(), m.end())
        if _REFERENCE_BEFORE_RE.search(head) or not _COUNT_VERB_RE.search(head):
            continue
        if not (_COUNT_NOUN_RE.search(head) or _COUNT_NOUN_RE.search(tail)):
            continue
        ranges.append((lo, hi))
    return runs, ranges


def uncorroborated(note: str, anchor: str, *, now: datetime.datetime | None = None) -> list[Hit]:
    """The identifiers in ``note`` that ``anchor`` (the request's subject and
    body as text) does not carry. Empty means every identifier corroborates.

    A bare number corroborates when the anchor carries it as a digit run, as one
    half of a run-together count, or inside a count range the anchor states
    (``anchor_numbers``)."""
    note_hits = structured(note)
    anchor_hits = structured(anchor)
    known = {h.canonical for h in anchor_hits}
    # Every digit run in the anchor counts, inside a structured span or not:
    # "Matter 203795" and "No. 24-203795" both vouch for 203795.
    known_runs, ranges = anchor_numbers(anchor)
    ambient = ambient_dates(now)
    out = [h for h in note_hits if h.canonical not in known and not (h.kind == "date" and h.canonical in ambient)]
    out += [
        h
        for h in bare_runs(note, outside=[h.span for h in note_hits])
        if h.canonical not in known_runs and not any(lo <= int(h.canonical) <= hi for lo, hi in ranges)
    ]
    return out


def new_message_scan(note: str, *, now: datetime.datetime | None = None) -> tuple[list[str], list[Hit]]:
    """A new-message note has no request email to corroborate against
    (2026-10-16). Returns (every distinct bare digit run, in order of first
    appearance; every structured identifier that is not an ambient date).
    The runs must each be a Smokeball matter or a number SMD declared plain
    (``note_verbs``); the structured ones are listed in the dry run SMD reads
    and bound into its digest, since nothing on the seat can vouch for them."""
    hits = structured(note)
    ambient = ambient_dates(now)
    others = [h for h in hits if not (h.kind == "date" and h.canonical in ambient)]
    runs: list[str] = []
    for h in bare_runs(note, outside=[h.span for h in hits]):
        if h.canonical not in runs:
            runs.append(h.canonical)
    return runs, others


__all__ = ["Hit", "ambient_dates", "anchor_numbers", "bare_runs", "new_message_scan", "structured", "uncorroborated"]
