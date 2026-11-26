"""Does an email in a filed draft's thread ask for NEW documents? (2026-11-24)

An attorney's thread holds both kinds of email: a change to what was filed
("the interrogatories need follow-up questions") and a request for more ("Please
also prepare sets of discovery to the middle school and school district"). The
first revises the filed documents (or is SMD's, ``drafting_route``); the second
is a new job, and must never be held for SMD or carried into a draft already
running. Read by code from the request's own words, never by the model:

* a CUE: also / additional / new / another / more, a few words before a kind of
  document (sets, discovery, interrogatories, RFAs, RFPs, requests), in a
  sentence that does not ask for a change (need, should, revise, fix, ...);
* or a PARTY named after a kind of document and "to", "directed to" or "for"
  ("sets of discovery to the school district") whose words appear neither in
  the earlier job's request nor in its filed files' names. A "to" that names a
  count or a rank ("cut the special interrogatories to the most important
  120125", "to 35", "to the top 20") names no party; a party named in a
  sentence that also asks for a change is still new.

Either is a new ask. Anything else is about what was filed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

_DOC = r"(?:sets?|discovery|interrogator(?:y|ies)|rfas?|rfps?|requests?|special\s+rogs?)"
_CUE = re.compile(rf"\b(?:also|additional|new|another|more)\b(?:\W+\w+){{0,6}}?\W+{_DOC}\b", re.I)
_CHANGE = re.compile(r"\b(?:needs?|should|revise|change|fix|update|edit|correct|follow[\s-]?ups?)\b", re.I)
_PARTY = re.compile(
    rf"\b{_DOC}\W+(?:directed\s+to|to|for)\s+(?!production\b|admission\b|inspection\b)([^.;:!?\n]{{1,160}})", re.I
)
#: Where a party's name ends: the clause after it says what to do, not who.
_PARTY_END = re.compile(
    r",|\b(?:so|that|because|with|which|who|whom|to|by|about|regarding|including|asking|on|in|is|are|was|were|"
    r"should|need|needs|must|please)\b",
    re.I,
)
#: A "to" that names how many, not whom (2026-11-24, "cut the special
#: interrogatories to the most important 120125"): a count or range, after an
#: optional rank ("the most important", "the top", "the first", "about").
_NUMBER_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|dozen)"
)
_QUANTITY = re.compile(
    r"(?i)^\s*(?:(?:the|about|around|roughly|approximately|at\s+most|no\s+more\s+than|under|fewer\s+than|just|only)"
    r"\s+)*(?:(?:most\s+\w+|top|first|last|\w+est|key)\s+)?"
    rf"(?:\d[\d,]*(?:\s*(?:-|\u2013|to|through)\s*\d[\d,]*)?|{_NUMBER_WORD}(?:[\s-]+{_NUMBER_WORD})*)"
    r"(?:\s+(?:or\s+so|or\s+fewer|or\s+less|in\s+all|total|items?|requests?|questions?|interrogatories|ones))?\s*$"
)
_SENTENCE = re.compile(r"[^.!?\n]+")
_WORD = re.compile(r"[a-z][a-z'-]{2,}")
#: Words that name no party.
_STOP = frozenset(
    "the and each all any our your their his her its this that these those with from into onto upon "
    "please thanks thank you also set sets discovery defendant defendants plaintiff plaintiffs party parties "
    "propound propounded prepare draft serve send them him who what when".split()
)
#: Where the earlier request's words are kept (``record``), so a new party is told
#: from a known one. A row written before it has none: its files' names are read.
COLUMN_SQL = "ALTER TABLE drafting_jobs ADD COLUMN request_words TEXT"
MAX_WORDS = 4000


def ensure(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafting_jobs)")}
    if "request_words" not in cols:
        conn.execute(COLUMN_SQL)


def record(conn: sqlite3.Connection, job_id: str, text: str) -> None:
    """The request's words on its row, in the submit's own transaction."""
    conn.execute("UPDATE drafting_jobs SET request_words=? WHERE id=?", (str(text)[:MAX_WORDS], job_id))


def _words(text: str) -> set[str]:
    return set(_WORD.findall(str(text).lower())) - _STOP


def known_words(row: dict[str, Any]) -> set[str]:
    """The earlier job's request and its filed files' names."""
    out = _words(row.get("request_words") or "")
    try:
        files = (json.loads(row.get("delivery_json") or "{}") or {}).get("files") or []
    except ValueError:
        files = []
    for f in files:
        if isinstance(f, dict):
            out |= _words(f.get("name") or "")
    return out


def asks_new(text: str, prior_rows: list[dict[str, Any]]) -> bool:
    """True when ``text`` asks for documents the earlier jobs did not cover."""
    known: set[str] = set()
    for row in prior_rows:
        known |= known_words(row)
    for sentence in _SENTENCE.findall(str(text)):
        if _CUE.search(sentence) and not _CHANGE.search(sentence):
            return True
        for m in _PARTY.finditer(sentence):
            name = _PARTY_END.split(m.group(1), maxsplit=1)[0]
            if _QUANTITY.match(name):
                continue  # "cut the specials to the most important 120125": a number, never a party
            party = _words(name)
            if party and not party <= known:
                return True
    return False


__all__ = ["asks_new", "ensure", "known_words", "record"]
