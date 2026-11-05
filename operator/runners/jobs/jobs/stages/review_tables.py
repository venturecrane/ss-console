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

CHECK 4 IS DECIDED HERE (ADR 0104 W8, audit finding B1). A reviewer's "this
summary has no entry behind it" used to delete the line on the model's word,
though the model sees a sample of the entries and these tables are computed from
all of them. Code now answers the question for the lines code can answer it for
(``decide_check4``): a Treatment Timeline or Diagnostic Highlights row is
recomputed from the entries, and a Prior Medical History line, whose entries the
body no longer carries, is held against the pre-incident entries on disk. Kept,
corrected or removed, the attorney's Review Notes say which.
"""

from __future__ import annotations

import re
from typing import Any

from ..medchron.audit import diag
from . import build_doc as BD, summarize
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
            out.append(_highlight_row(now[key], c[1] if len(c) > 1 else "", entries))
        lines[first:end] = out
    return "\n".join(lines)


def _highlight_row(derived: tuple[str, str, str], desc: str, entries: list[dict[str, Any]]) -> str:
    day, code, prov = derived
    same_day = [e for e in entries if e["iso"] == day]
    best = max(same_day, key=lambda e: len(BD._toks(str(e.get("provider") or "")) & BD._toks(prov)), default=None)
    return f"| {code} | {desc} | {day[5:7]}/{day[8:10]}/{day[:4]} | {best['ref'] if best else ''} |"


def _timeline_line(sr: StageRun, md: str, line: str) -> str | None:
    """The row as the entries compute it; None when no entry names the provider."""
    c = _cells(line)
    for _first, prov, period, n, ref in BD.provider_rows(_entries(md), Canon(sr.cfg)):
        if prov == c[0]:
            return line if c == [prov, period, str(n), ref] else f"| {prov} | {period} | {n} | {ref} |"
    return None


def _highlight_line(sr: StageRun, md: str, line: str) -> str | None:
    """The row as the entries compute it. A code the maps do not derive (an
    audited code in the spelling the tables read) stands while an entry carries
    its date; None when no entry does."""
    entries = _entries(md)
    c = _cells(line)
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", c[2] if len(c) > 2 else "")
    day = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""
    now = BD.icd_codes(sr.slug_dir / "runs" / sr.unit.unit, {e["iso"] for e in entries})
    derived = now.get(c[0].replace(".", ""))
    if derived is not None:
        return line if derived[0] == day else _highlight_row(derived, c[1] if len(c) > 1 else "", entries)
    return line if any(e["iso"] == day for e in entries) else None


def prior_history_line(sr: StageRun, line: str) -> str | None:
    """The line when every page it cites is a page the pre-incident entries cite
    (``summarize.PRE_ENTRIES``, carried through the strip's page remap); None
    when one is not, or it cites nothing, or a job built before that file existed
    has nothing to hold it against, so the line goes as it always did."""
    src = sr.slug_dir / "runs" / sr.unit.unit / summarize.PRE_ENTRIES
    if not src.is_file():
        return None
    remap = diag.page_remap(sr.slug_dir, sr.unit.unit)
    have = {
        ex: {remap.get(ex, {}).get(p, p) for p in pages}
        for ex, pages in summarize.pages_by_exhibit(src.read_text(encoding="utf-8")).items()
    }
    want = summarize.pages_by_exhibit(line)
    return line if want and all(ex in have and pages <= have[ex] for ex, pages in want.items()) else None


def decide_check4(sr: StageRun, md: str, at: int | None) -> tuple[str, str | None, str | None]:
    """(the document, the line removed, what to tell the attorney) for a check 4
    finding on summary line ``at``. ``at`` None (the quote names no single line)
    decides nothing: the finding stays as it was, for the repair round."""
    if at is None:
        return md, None, None
    lines = md.splitlines(keepends=True)
    line = lines[at].rstrip("\n")
    head = next((ln.strip() for ln in reversed(lines[:at]) if ln.startswith("## ")), "")
    decide = {TIMELINE: _timeline_line, HIGHLIGHTS: _highlight_line}.get(head)
    new = decide(sr, md, line) if decide else prior_history_line(sr, line)
    if new is None:
        del lines[at]
        return re.sub(r"\n{3,}", "\n\n", "".join(lines)), line, "removed before delivery, no entry supports it"
    if new == line:
        return md, None, "checked against the entries and kept"
    lines[at] = new + "\n"
    return "".join(lines), None, "corrected from the entries before delivery"
