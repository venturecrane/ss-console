"""A date read off a HANDWRITTEN page is checked against the typed record. $0.

The vision pass opens a page whose dates or entries are handwritten with the
line ``[handwritten]`` (``stages/vision.SYSTEM``). A handwritten 7 reads as a
1, a 3 as an 8; the visit is real, its date is a reading. The typed
statements and bills of the same provider state the dates of service, so:

* exactly ONE typed date of service of that provider within 7 days of the
  reading: the entry takes that date (re-dated when it differs);
* none, or more than one: the date is unsupported, and the entry shows the
  reading as an OPEN date, "MM/DD/YYYY (handwritten date, unconfirmed)",
  never a guess between candidates.

The audit of a claim cited to a handwritten page is given the same typed
statements as evidence (``statement_block``).
"""

from __future__ import annotations

import re
from datetime import date

MARK = "[handwritten]"
OPEN = "(handwritten date, unconfirmed)"
WINDOW_DAYS = 7
STATEMENT = re.compile(
    r"(?i)\b(?:statement|date of service|dates of service|dos|charges?|balance due|ledger|invoice|itemi[sz]ed)\b"
)
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
_GENERIC = {
    "clinic", "center", "centre", "medical", "health", "group", "associates", "hospital", "care", "services",
    "chiropractic", "therapy", "physical", "imaging", "radiology", "orthopedic", "orthopedics", "family",
    "urgent", "practice", "office", "institute", "inc", "llc",
}  # fmt: skip


def is_handwritten(page_text: str | None) -> bool:
    return (page_text or "").lstrip().lower().startswith(MARK)


def _names(provider: str, text: str) -> bool:
    """True when `text` carries the provider's distinctive words (all of them)."""
    ws = [w for w in re.findall(r"[a-z]{3,}", provider.split(",")[0].lower()) if w not in _GENERIC]
    if not ws:
        ws = re.findall(r"[a-z]{4,}", provider.lower())
    low = (text or "").lower()
    return bool(ws) and all(re.search(rf"\b{re.escape(w)}\b", low) for w in ws)


def _dates(text: str) -> set[date]:
    out: set[date] = set()
    for mm, dd, yy in _DATE.findall(text or ""):
        y = int(yy) + (2000 if len(yy) == 2 else 0)
        try:
            out.add(date(y, int(mm), int(dd)))
        except ValueError:
            continue
    return out


def statements(pages: list[str], provider: str) -> list[str]:
    """The typed statement and bill pages of `pages` that name `provider`."""
    return [t for t in pages if not is_handwritten(t) and STATEMENT.search(t or "") and _names(provider, t)]


def service_dates(pages: list[str], provider: str) -> set[date]:
    out: set[date] = set()
    for t in statements(pages, provider):
        out |= _dates(t)
    return out


def decide(reading: date, dos: set[date]) -> tuple[str, date | None]:
    """('confirmed' | 'redated', the date) or ('open', None)."""
    near = sorted(d for d in dos if abs((d - reading).days) <= WINDOW_DAYS)
    if len(near) != 1:
        return "open", None
    return ("confirmed" if near[0] == reading else "redated"), near[0]


def _mdy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def redate(entry: str, dos: set[date]) -> tuple[str, dict[str, str]]:
    """(the entry with its date settled, a log row). The entry's own date is
    the handwritten reading."""
    head, _, rest = entry.partition("\n")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", head)
    if not m:
        return entry, {"entry": head, "verdict": "no date"}
    reading = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    verdict, got = decide(reading, dos)
    if got is not None:
        return _mdy(got) + "\n" + rest, {"reading": _mdy(reading), "verdict": verdict, "date": _mdy(got)}
    return f"{_mdy(reading)} {OPEN}\n" + rest, {"reading": _mdy(reading), "verdict": "open"}


def statement_block(pages: list[str], provider: str, limit: int = 12_000) -> str:
    """The audit's extra evidence for a claim cited to a handwritten page."""
    got = statements(pages, provider)
    if not got:
        return ""
    body = "\n\n".join(t.strip() for t in got)[:limit]
    return (
        "The cited page is handwritten. Typed statements and bills of the same provider follow, for the date "
        "of service only; the claim's other facts are judged on the cited page.\n\n" + body
    )
