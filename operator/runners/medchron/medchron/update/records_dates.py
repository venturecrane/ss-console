"""An entry dated by when its record was printed, released, faxed or
received is not a visit. $0.

A records-release stamp ("Printed 03/12/2026") on a page with no date of
service once became an entry's date, ended its exhibit's title four weeks
after the last visit, and was counted as a visit (2026-10-13). Compose marks
some such dates itself ("(record print date)"); this marks the rest from the
cited pages: when the entry's date appears on its cited pages ONLY on a
print, release, fax, request or received line, the date line gets
``body.RECORDS_MARK``. The entry is KEPT (what it states is the record's),
and ``body.is_service`` keeps it out of the Treatment Timeline's visits and
the exhibit titles' date ranges. A date the cited pages never state, or
state on any other line, is left as composed.
"""

from __future__ import annotations

import re
from datetime import date

from ..audit import claims as CL
from . import body as B

#: A records-handling line: when the record was printed, released, faxed,
#: requested or received.
RECORDS_LINE = re.compile(r"(?i)\b(?:print(?:ed)?|release[ds]?|fax(?:ed)?|received|requested|run date)\b")
#: A line that dates care, whatever else it says.
SERVICE_LINE = re.compile(r"(?i)\b(?:dos|dates? of service|service date|visit|encounter|admi(?:t|ssion)|seen)\b")
_DATE = re.compile(r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/-])")


def _dates(line: str) -> set[date]:
    out: set[date] = set()
    for mm, dd, yy in _DATE.findall(line):
        try:
            out.add(date(int(yy) + (2000 if len(yy) == 2 else 0), int(mm), int(dd)))
        except ValueError:
            continue
    return out


def only_a_records_date(entry: str, pages: dict[int, dict[int, str]]) -> bool:
    """True when the entry's date is on its cited pages and only on records lines."""
    iso = B.iso(entry)
    if not iso or not B.is_service(entry):
        return False
    d = date(int(iso[:4]), int(iso[5:7]), int(iso[8:10]))
    hits = [
        ln
        for m in B.CITE.finditer(entry)
        for p in CL.parse_pages(m.group(2))
        for ln in (pages.get(int(m.group(1)), {}).get(p) or "").splitlines()
        if d in _dates(ln)
    ]
    return bool(hits) and all(RECORDS_LINE.search(ln) and not SERVICE_LINE.search(ln) for ln in hits)


def mark(entries: list[str], pages: dict[int, dict[int, str]]) -> tuple[list[str], list[dict[str, str]]]:
    """(entries, a log row per entry whose date was marked a records date)."""
    out, log = [], []
    for e in entries:
        if only_a_records_date(e, pages):
            head, nl, rest = e.partition("\n")
            e = head[:10] + " " + B.RECORDS_MARK + head[10:] + nl + rest
            log.append({"entry": head, "provider": B.provider_of(e), "marked": B.RECORDS_MARK})
        out.append(e)
    return out, log
