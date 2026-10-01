"""Client names for statute-watch, read from the matter title.

No I/O. The firm titles every matter ``NNNNNN - Last, First - ...``: the file
number, then the client (co-clients joined by " | " or " & "), then case
descriptors. The second segment is the client as the firm wrote it, so it is
the name this report prints.

Two renderings, both from the same parse:

* ``workbook_name``: ``Last, First`` per client, co-clients joined by "; ".
* ``body_name``: last names only, co-clients "A and B" (three or more:
  "A, B and C").

Every token is cleaned the way ``report.surname`` cleans a last name: periods
removed, characters outside letters, apostrophes and hyphens dropped, a token
carrying a digit dropped, single-letter initials dropped, a parenthetical
group dropped, and the name cut at a "v", "vs", "versus" or "in re" token. A
middle initial "V." is what turns "Maria V. Garcia" into a case caption the
send gate refuses; with initials gone no rendered name can read as one.

A title that does not have the shape returns ``None``; the caller falls back
to the contact's last name, then to "client name not available".
"""

from __future__ import annotations

import re

_CAPTION_TOKENS = frozenset({"v", "vs", "versus"})
_PART_MAX_CHARS = 40
#: A file number (one token carrying a digit: "913353", "2026-PI-107"), then the client segment.
_TITLE_RE = re.compile(r"^\s*(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{2,19}\s+-\s+(.+?)(?:\s+-\s+.*)?$")
_CO_CLIENT_RE = re.compile(r"\s+[|&]\s+")
_PAREN_RE = re.compile(r"\([^)]*\)")


def clean_words(raw: object) -> str | None:
    """Letters-only words of one name part, never a caption, or None."""
    if not isinstance(raw, str):
        return None
    kept: list[str] = []
    for token in _PAREN_RE.sub(" ", raw).replace(".", " ").split():
        if any(ch.isdigit() for ch in token):
            continue
        clean = re.sub(r"[^A-Za-z'\-]", "", token).strip("'-")
        lowered = clean.lower()
        if lowered in _CAPTION_TOKENS:
            break
        if lowered == "re" and kept and kept[-1].lower() == "in":
            kept.pop()
            break
        if len(clean) < 2:
            continue
        kept.append(clean)
    name = " ".join(kept)[:_PART_MAX_CHARS].strip()
    return name or None


def parse_title(title: object) -> list[tuple[str, str | None]] | None:
    """``[(last, first or None), ...]`` from the title's client segment, or
    None when the title does not read ``NNNNNN - Last, First - ...``."""
    if not isinstance(title, str):
        return None
    match = _TITLE_RE.match(title)
    if match is None:
        return None
    clients: list[tuple[str, str | None]] = []
    for part in _CO_CLIENT_RE.split(match.group(1)):
        last_raw, _sep, first_raw = part.partition(",")
        last = clean_words(last_raw)
        if last is None:
            continue
        clients.append((last, clean_words(first_raw)))
    return clients or None


def workbook_name(clients: list[tuple[str, str | None]]) -> str:
    return "; ".join(last + (", " + first if first else "") for last, first in clients)


def join_and(names: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def body_name(clients: list[tuple[str, str | None]]) -> str:
    seen: list[str] = []
    for last, _first in clients:
        if last not in seen:
            seen.append(last)
    return join_and(seen)


def names_for(title: object, contact_last_name: object) -> tuple[str | None, str | None]:
    """``(workbook name, body name)``: from the title, else the contact's last
    name (both columns), else ``(None, None)``."""
    clients = parse_title(title)
    if clients:
        return workbook_name(clients), body_name(clients)
    fallback = clean_words(contact_last_name)
    return fallback, fallback


def person_name(first: object, last: object) -> str | None:
    """A staff member's full name, initials dropped."""
    parts = [clean_words(first), clean_words(last)]
    joined = " ".join(p for p in parts if p)
    return joined or None
