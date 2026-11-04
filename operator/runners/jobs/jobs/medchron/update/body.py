"""The chronology markdown's shape, read the way build_doc writes it.

Sections in order: title, Treatment Timeline, Diagnostic Highlights, Medical
Chronology (an optional preamble, then the dated entries), Exhibit List, and
Records Reviewed and Limitations, which a chronology rendered before that
section existed may lack (a section the delivered document lacks is not one
an update invents).
"""

from __future__ import annotations

import difflib
import re

SECTIONS = (
    "## Treatment Timeline",
    "## Diagnostic Highlights",
    "## Medical Chronology",
    "## Exhibit List",
    "## Records Reviewed and Limitations",
)
#: An entry's date line: MM/DD/YYYY, or a month and day with no year
#: ("07/18 (year not stated)"), which compose writes for a handwritten log row
#: that shows none. Both start an entry: a yearless one read as body text
#: rode inside the entry before it, unseen by the handwritten re-date
#: (2026-10-14 parity run). collect gives it a year (``handwritten.with_years``).
HEAD = re.compile(r"\d{2}/\d{2}/\d{4}\s*(?:\(|$)|\d{1,2}/\d{1,2}(?![/\d])\s*(?:\(|$)", re.M)
ENTRY = re.compile(r"(?m)^(?=" + HEAD.pattern + ")")
DATED = re.compile(r"\d{2}/\d{2}/\d{4}")
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
#: The delivered summarize-only note ("[NTD: N additional pre-incident
#: encounters ... not itemized ...]"); an update that itemizes removes it.
PRE_NOTE = re.compile(r"\[NTD: \d+ additional pre-incident encounters?[^\]]*not itemized[^\]]*\]\s*")
PMH_HEAD = re.compile(r"(?m)^\**Prior Medical History\**\s*$")


def sections(md: str) -> dict[str, str]:
    """{title, <section header>: the text after it up to the next header}."""
    pos = []
    for h in SECTIONS:
        m = re.search(r"(?m)^" + re.escape(h) + r"\s*$", md)
        if m:
            pos.append((m.start(), m.end(), h))
        elif h != SECTIONS[4]:
            raise ValueError(f"missing section {h!r}")
    pos.sort()
    out = {"title": md[: pos[0][0]]}
    for i, (_s, e, h) in enumerate(pos):
        out[h] = md[e : pos[i + 1][0] if i + 1 < len(pos) else len(md)]
    return out


def split_body(body: str) -> tuple[str, list[str]]:
    """(preamble, [entries]); an entry runs from its date line to the next."""
    parts = ENTRY.split(body)
    pre = "" if HEAD.match(parts[0].lstrip()) else parts[0]
    return pre, [p.strip("\n") for p in parts if HEAD.match(p)]


def entries_of(md: str) -> tuple[str, list[str]]:
    """(preamble, entries) of a whole chronology markdown."""
    return split_body(sections(md)["## Medical Chronology"])


def iso(e: str) -> str:
    m = DATE.match(e)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def other_date(dropped: str, survivor: str) -> str:
    """'' when `survivor`'s date line states the date `dropped`'s does, else
    why not. An entry dropped or folded into another loses its own date line,
    and the body comparison never sees it (2026-10-15: a heading dated by a
    pathology order's signature date was dropped into another visit and proof
    10 found the date gone)."""
    a, b = iso(dropped), iso(survivor)
    if a and a == b:
        return ""
    return f"it is dated {dropped[:10]}, the survivor {survivor[:10]}; a visit's date is never folded away"


#: A date line whose date is when the record was printed, released, faxed,
#: requested or received ("10/09/2026 (record print date)"), or the as-of or
#: generated date of a printout ("09/11/2026 (problem list as-of date)"): not
#: a date of service. Such an entry is kept, and is never a visit.
RECORDS_MARK = "(records date, not a date of service)"
RECORDS_DATE = re.compile(
    re.escape(RECORDS_MARK)
    + r"|\([^)]*\b(?:print(?:ed)?|release[ds]?|fax(?:ed)?|received|request(?:ed)?|run)\b[^)]*\bdate\b[^)]*\)"
    r"|\([^)]*\bdate\b[^)]*\b(?:print(?:ed)?|release[ds]?|fax(?:ed)?|received|request(?:ed)?)\b[^)]*\)"
    r"|\([^)]*\b(?:as[- ]of|generated|printout)\b[^)]*\)",
    re.I,
)


def is_service(e: str) -> bool:
    """False when the entry's date line marks its date as a records date
    (printed, released, faxed, requested, received), never a visit."""
    head = e.splitlines()[0] if e else ""
    return bool(DATE.match(head)) and not RECORDS_DATE.search(head[10:])


def provider_of(e: str) -> str:
    lines = e.splitlines()
    head = lines[1] if len(lines) > 1 else ""
    return head.split("|")[0].strip() if "|" in head else head.strip()


def cited(text: str) -> set[int]:
    return {int(m.group(1)) for m in CITE.finditer(text or "")}


def _toks(e: str) -> set[str]:
    lines = e.splitlines()
    return set(re.findall(r"[a-z]{3,}", (lines[1] if len(lines) > 1 else "").lower()))


def score(a: str, b: str) -> float:
    """How alike two entries read: provider words, or the opening text."""
    ta, tb = _toks(a), _toks(b)
    j = len(ta & tb) / max(len(ta | tb), 1)
    s = difflib.SequenceMatcher(None, re.sub(r"\s+", " ", a[:500]), re.sub(r"\s+", " ", b[:500])).ratio()
    return max(j, s)


def merge_entries(base_ents: list[str], new_ents: list[str]) -> list[str]:
    """Delivered order kept; each new entry goes after the last delivered or
    already-placed entry dated on or before it."""
    out = list(base_ents)
    for e in sorted(new_ents, key=iso):
        k = max((j + 1 for j, x in enumerate(out) if iso(x) <= iso(e)), default=0)
        out.insert(k, e)
    return out


def table_rows(sec: str) -> list[str]:
    return [ln.strip() for ln in sec.splitlines() if ln.strip().startswith("|")]


def chronology_lines(md: str) -> list[str]:
    """The Medical Chronology section's lines (preamble and entries)."""
    return md.split("## Medical Chronology", 1)[1].split("## Exhibit List", 1)[0].splitlines()


def delivered_numbers(md: str) -> set[int]:
    """Exhibit numbers on a chronology's Exhibit List."""
    sec = md.split("## Exhibit List", 1)[1] if "## Exhibit List" in md else ""
    sec = sec.split("## Records Reviewed", 1)[0]
    return {int(m.group(1)) for m in re.finditer(r"(?m)^\|\s*(\d+)\s*\|", sec)}


def exhibit_titles(md: str) -> dict[int, str]:
    """{number: description} off the Exhibit List rows."""
    sec = sections(md)["## Exhibit List"]
    out: dict[int, str] = {}
    for r in table_rows(sec):
        c = [x.strip() for x in r.strip().strip("|").split("|")]
        if len(c) >= 2 and c[0].isdigit():
            out[int(c[0])] = c[1]
    return out
