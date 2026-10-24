"""The chronology's summary tables, re-derived after the review leaves an
entry out. $0.

WHY (2026-10-24). The review now leaves an entry out by rule when its cited
page names another person (``review_evidence.leave_out``). The Treatment
Timeline (visits and period per provider) and the Diagnostic Highlights (first
diagnosed date and reference per code) are computed FROM THE ENTRIES by
``build_doc``; with an entry gone they would still count it. So they are
computed again here, with ``build_doc``'s own functions over the entries that
remain, never patched by hand:

* Treatment Timeline: ``build_doc.provider_rows`` over the remaining entries.
  A provider row no longer in the table (removed earlier by the review as
  unsupported) is not brought back; a provider left with no entry goes.
* Diagnostic Highlights: ``build_doc.icd_codes`` over the remaining entries'
  dates. A code the index carried only on the removed entry's date goes; a
  code it still carries gets its first date and reference again; a code the
  index cannot derive at all is kept as it stands (nothing to re-derive it
  from).

The rest of the document is untouched, byte for byte.
"""

from __future__ import annotations

import re
from typing import Any

from . import build_doc as BD
from .base import StageRun
from .group import Canon

TIMELINE, HIGHLIGHTS = "## Treatment Timeline", "## Diagnostic Highlights"
_FIRST_ENTRY = re.compile(r"(?m)^\d{2}/\d{2}/\d{4}\s*$")


def _entries(md: str) -> list[dict[str, Any]]:
    """The dated entries of the chronology body (``build_doc.parse_entries``)."""
    start = md.find("## Medical Chronology")
    end = md.find("## Exhibit List", start)
    body = md[start : end if end > 0 else len(md)] if start >= 0 else ""
    m = _FIRST_ENTRY.search(body)
    return BD.parse_entries(body[m.start() :]) if m else []


def _section(lines: list[str], head: str) -> tuple[int, int] | None:
    """(first data row, end) of a table section: rows after its header row
    (and rule row), up to the next heading."""
    try:
        at = next(i for i, ln in enumerate(lines) if ln.strip() == head)
    except StopIteration:
        return None
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    rows = [i for i in range(at + 1, end) if lines[i].lstrip().startswith("|")]
    if not rows:
        return None
    first = rows[0] + 1
    if first < end and set(lines[first].strip()) <= set("|-: ") and lines[first].strip():
        first += 1
    last = max(rows) + 1
    return first, max(first, last)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def rederive(sr: StageRun, before: str, after: str) -> str:
    """`after` (the chronology with entries left out) with its Treatment
    Timeline and Diagnostic Highlights computed again from its entries;
    `before` is the chronology the review started from (its entries bound
    what the index can derive)."""
    lines = after.split("\n")
    entries = _entries(after)
    sec = _section(lines, TIMELINE)
    if sec is not None:
        first, end = sec
        shown = {_cells(ln)[0] for ln in lines[first:end] if ln.strip().startswith("|")}
        rows = [r for r in BD.provider_rows(entries, Canon(sr.cfg)) if r[1] in shown]
        lines[first:end] = [f"| {prov} | {period} | {n} | {ref} |" for _, prov, period, n, ref in rows]
    sec = _section(lines, HIGHLIGHTS)
    if sec is not None:
        rd = sr.slug_dir / "runs" / sr.unit.unit
        was = BD.icd_codes(rd, {e["iso"] for e in _entries(before)})
        now = BD.icd_codes(rd, {e["iso"] for e in entries})
        first, end = sec
        out: list[str] = []
        for ln in lines[first:end]:
            if not ln.strip().startswith("|"):
                out.append(ln)
                continue
            c = _cells(ln)
            key = c[0].replace(".", "")
            if key not in was:
                out.append(ln)  # not derivable from the index: kept as it stands
                continue
            if key not in now:
                continue  # only the removed entry carried it
            day, code, prov = now[key]
            same_day = [e for e in entries if e["iso"] == day]
            best = max(
                same_day, key=lambda e: len(BD._toks(str(e.get("provider") or "")) & BD._toks(prov)), default=None
            )
            desc = c[1] if len(c) > 1 else ""
            out.append(f"| {code} | {desc} | {day[5:7]}/{day[8:10]}/{day[:4]} | {best['ref'] if best else ''} |")
        lines[first:end] = out
    return "\n".join(lines)
