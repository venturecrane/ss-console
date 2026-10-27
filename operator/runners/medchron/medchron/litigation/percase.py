"""`percase`: the same status list arranged one sheet per case.

WHY (2026-10-27). An attorney asked for the list as one printable page per
case; it was made by hand from a filed list once. This builds it on every run,
from the SAME ``final.json`` values the main workbook is built from (never a
second read), and the run files it next to the main list.

Each case gets one tab, in the order of the main list's Case notes tab: the
case title, a facts block (the case-level columns of All defendants, lifted
out), then that case's defendant rows with the same values in the same order
as All defendants. Every cell passes ``book.clean`` exactly as the main list's
cells do, and the run scans the built file with ``gates.leak_scan``.

Layout is for print: landscape Letter at 100% (never fit-to-page, which shrank
type below reading size), columns sized to fit, the Defendants header repeated
on every page, and every row height SET, because Excel and LibreOffice auto
heights left text overlapping or cut on the hand-made copy. Heights come from
a character-width estimate with a safety margin (no font files on the seat).
A tab that would print over two pages at 9pt drops to 8pt, never smaller.
"""

from __future__ import annotations

import collections
import datetime as dt
import io
import re
from pathlib import Path
from typing import Any

from . import book, vocab

SUFFIX = " one sheet per case"
FONT = "Calibri"
BODY_PT, SMALL_PT, TITLE_PT, SECTION_PT = 9, 8, 14, 12
DEF_COLS = book.DEF_HEADER[7:]  # Defendant .. Notes: the per-defendant columns of All defendants
WIDTHS = [14, 10, 9, 17, 16, 12, 9, 11, 36]  # 134 units: landscape Letter at 100% with 0.3in side margins
NO_DEFENDANTS = "No defendants listed for this case."
NO_COMPLAINT = "No complaint filed"
NO_COMPLAINT_WHY = "No filed complaint was found in the files read"
FOOTER = "&A  page &P of &N"
#: Printable height of landscape Letter less the 0.5in top and bottom
#: margins, with a margin for the estimate (points).
PAGE_PT = (8.5 - 1.0) * 72 * 0.96
MAX_ROW_PT = 409.0  # Excel's ceiling for one row
_BAD = re.compile(r"[\[\]:*?/\\']")


def path_for(main: Path) -> Path:
    """The per-case file, named like the main list: '<main stem> one sheet per case.xlsx'."""
    return main.with_name(f"{main.stem}{SUFFIX}{main.suffix}")


def tab_names(titles: list[str]) -> list[str]:
    """Excel-safe, unique (case-insensitively) tab names, in order."""
    used: set[str] = set()
    out = []
    for t in titles:
        base = _BAD.sub("", t).strip()[:31].strip() or "Case"
        name, i = base, 2
        while name.lower() in used:
            name = f"{base[:27].strip()} ({i})"
            i += 1
        used.add(name.lower())
        out.append(name)
    return out


# ---- the height estimate ------------------------------------------------------------------
def _em(ch: str) -> float:
    """A Calibri advance width in ems, rounded UP by class."""
    if ch == " ":
        return 0.23
    if ch in "iljtfrI.,;:'!|()[]":
        return 0.34
    if ch in "mwMW@%":
        return 0.86
    if ch.isupper() or ch in "&#":
        return 0.6
    return 0.51


def _width(text: str, pt: float, bold: bool) -> float:
    return sum(_em(c) for c in text) * pt * (1.06 if bold else 1.0) * 1.04


def col_pt(units: float) -> float:
    """Usable text width (points) of a column ``units`` wide, less cell padding."""
    return (7 * units + 5) * 0.75 - 5


def _para_lines(para: str, pt: float, width_pt: float, bold: bool) -> int:
    line, n = "", 1
    for tok in re.findall(r"\S+(?:\s+[.,;:!?)\]}]\S*)*\s*", para):
        if _width(line + tok.rstrip(), pt, bold) <= width_pt:
            line += tok
            continue
        if line:
            n += 1
        while _width(tok.rstrip(), pt, bold) > width_pt:  # a run longer than the column breaks anywhere
            k = len(tok)
            while k > 1 and _width(tok[:k], pt, bold) > width_pt:
                k -= 1
            tok, n = tok[k:], n + 1
        line = tok
    return n


def lines(text: Any, pt: float, width_pt: float, bold: bool = False) -> int:
    """Wrapped line count by a greedy word wrap; never fewer than one."""
    if text is None or str(text) == "":
        return 1
    return sum(_para_lines(p, pt, width_pt, bold) for p in str(text).split("\n"))


def height(n: int, pt: float) -> float:
    return min(MAX_ROW_PT, round(n * pt * 1.22 + 3, 1))


def pages(heights: list[float], header_at: int) -> int:
    """Printed pages for rows of these heights, the row at ``header_at``
    repeating at the top of every page after the first."""
    n, used = 1, 0.0
    for i, h in enumerate(heights):
        if used + h > PAGE_PT and used > 0:
            n += 1
            used = heights[header_at] if i > header_at else 0.0
        used += h
    return n


# ---- one case -----------------------------------------------------------------------------
def _case(m: dict[str, Any]) -> str:
    return str(m.get("case_name") or m.get("title") or "")


def complaint(m: dict[str, Any]) -> tuple[str, str]:
    """(Complaint filed, its source line or ""). The value is the matter's own
    ``complaint_filed`` (the case group's value, never a defendant row's);
    with none, "No complaint filed" for a case on the Not filed list, else the
    list's "Not in file" and why."""
    v = book._date(m.get("complaint_filed"))
    if v:
        return v, ""
    if book._status(m) == vocab.NOT_FILED:
        return NO_COMPLAINT, ""
    return book.NOT_IN_FILE, NO_COMPLAINT_WHY


def facts(m: dict[str, Any], role: tuple[str, str] | None) -> list[tuple[str, Any]]:
    """The facts block, label and value, in print order."""
    filed, why = complaint(m)
    out: list[tuple[str, Any]] = [
        ("Matter #", m.get("number")),
        ("Responsible (Smokeball)", m.get("responsible") or ""),
        ("Court / case no.", " ".join(x for x in (m.get("court"), m.get("case_number")) if x)),
        ("Complaint filed", filed),
    ]
    if why:
        out.append(("Complaint filed (source)", why))
    out += [("Case status", book._status(m)), ("Next court date", book._date(m.get("next_court_date")))]
    if book._status(m) == vocab.NOT_FILED:
        out.append(("Not filed: where it stands", book.not_filed_text(m)))
    if role is not None:
        out.append(role)
    out.append(("Case notes", book.case_notes_text(m)))
    return [(k, book.clean(v)) for k, v in out]


def roles(tagged: tuple[list[str], list[list[Any]]] | None) -> tuple[str, dict[str, str]] | None:
    """(the role column's header, matter # -> that matter's role text) from a
    people scope's tagged-tab rows; with several people, each one named."""
    if tagged is None:
        return None
    header, rows = tagged
    several = header[0] == "Person"
    i = 3 if several else 2
    by: dict[str, list[str]] = collections.defaultdict(list)
    for r in rows:
        num = str(r[1] if several else r[0])
        by[num].append(f"{r[0]}: {r[i]}" if several else str(r[i]))
    return header[i], {k: "; ".join(v) for k, v in by.items()}


def _cell(ws: Any, r: int, c: int, v: Any, font: Any, fill: Any = None) -> None:
    from openpyxl.styles import Alignment

    cell = ws.cell(r, c, v)
    cell.font = font
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    if fill is not None:
        cell.fill = fill


def plan(title: str, fs: list[tuple[str, Any]], drows: list[list[Any]], pt: float) -> tuple[list[float], int]:
    """Every row's height (index 0 is row 1) and the index of the header row."""
    full, rest = col_pt(sum(WIDTHS)), col_pt(sum(WIDTHS[1:]))
    hs = [height(lines(title, TITLE_PT, full, True), TITLE_PT), height(1, pt)]
    hs += [height(max(lines(v, pt, rest), lines(k, pt, col_pt(WIDTHS[0]), True)), pt) for k, v in fs]
    hs += [height(1, pt), height(1, SECTION_PT)]
    at = len(hs)
    hs.append(height(max(lines(h, pt, col_pt(w), True) for h, w in zip(DEF_COLS, WIDTHS)), pt))
    hs += [height(max(lines(v, pt, col_pt(w)) for v, w in zip(row, WIDTHS)), pt) for row in drows]
    return hs, at


def _tab(wb: Any, name: str, m: dict[str, Any], drows: list[list[Any]], role: tuple[str, str] | None) -> int:
    """One case's tab; returns its body point size."""
    from openpyxl.styles import Font, PatternFill

    title, fs = book.clean(_case(m)), facts(m, role)
    rows = drows or [[NO_DEFENDANTS]]
    pt = BODY_PT
    hs, at = plan(title, fs, rows, pt)
    if pages(hs, at) > 2:
        pt = SMALL_PT
        hs, at = plan(title, fs, rows, pt)
    ws = wb.create_sheet(name)
    body, bold = Font(name=FONT, size=pt), Font(name=FONT, size=pt, bold=True)
    last = len(WIDTHS)
    _cell(ws, 1, 1, title, Font(name=FONT, size=TITLE_PT, bold=True))
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last)
    for i, (k, v) in enumerate(fs, start=3):
        _cell(ws, i, 1, k, bold)
        _cell(ws, i, 2, v, body)
        ws.merge_cells(start_row=i, start_column=2, end_row=i, end_column=last)
    _cell(ws, at, 1, "Defendants", Font(name=FONT, size=SECTION_PT, bold=True))
    hdr = at + 1
    for j, h in enumerate(DEF_COLS, 1):
        _cell(ws, hdr, j, h, bold, PatternFill("solid", fgColor=book.HEADER_FILL))
    for i, row in enumerate(rows, start=hdr + 1):
        for j in range(1, last + 1):
            _cell(ws, i, j, row[j - 1] if j - 1 < len(row) else None, body)
    _layout(ws, hs, hdr)
    return pt


def _layout(ws: Any, hs: list[float], hdr: int) -> None:
    """Row heights, column widths and the print setup."""
    from openpyxl.utils import get_column_letter

    for i, h in enumerate(hs, start=1):
        ws.row_dimensions[i].height = h
    for j, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.scale = 100
    ws.sheet_properties.pageSetUpPr.fitToPage = False
    ws.print_title_rows = f"{hdr}:{hdr}"
    ws.oddFooter.center.text = FOOTER
    ws.page_margins.left = ws.page_margins.right = 0.3
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.page_margins.header = ws.page_margins.footer = 0.2  # inside the 0.5in margin, or the footer is dropped


def build_per_case(
    ms: list[dict[str, Any]],
    out: Path,
    as_of: dt.date,
    *,
    tagged: tuple[list[str], list[list[Any]]] | None = None,
) -> dict[str, Any]:
    """Build the one-sheet-per-case workbook at ``out`` from final.json's
    matters; returns its stats. ``as_of`` is the list's date (the file name
    carries it); ``tagged`` is the main list's people-scope tab, when any."""
    from openpyxl import Workbook

    order = book.case_notes_order(ms)
    by: dict[int, list[list[Any]]] = collections.defaultdict(list)
    for r in book.defendant_rows(ms):  # All defendants' order; grouping keeps it per case
        by[id(r["m"])].append([book.clean(v) for v in r["cells"][7:]])
    rl = roles(tagged)
    wb = Workbook()
    blank = wb.active
    if blank is not None:  # the default empty sheet; every tab here is a case's
        wb.remove(blank)
    names = tab_names([f"{m.get('number') or ''} {book.clean(_case(m))}" for m in order])
    small = 0
    for name, m in zip(names, order):
        role = (rl[0], rl[1].get(str(m.get("number")), "")) if rl else None
        small += _tab(wb, name, m, by.get(id(m), []), role) == SMALL_PT
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {
        "as_of": as_of.isoformat(),
        "cases": len(order),
        "defendant_rows": sum(len(v) for v in by.values()),
        "small_type_tabs": small,
    }


# ---- the read-back check ------------------------------------------------------------------
def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _trim(row: list[str]) -> list[str]:
    while row and row[-1] == "":
        row = row[:-1]
    return row


def _read(xlsx: bytes) -> tuple[int, collections.Counter[tuple[str, ...]], list[str]]:
    """(tabs, defendant rows as text, problems) from the file's bytes."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    got: collections.Counter[tuple[str, ...]] = collections.Counter()
    bad: list[str] = []
    try:
        for ws in wb.worksheets:
            rows = [[_s(c) for c in r] for r in ws.iter_rows(values_only=True)]
            at = next((i for i, r in enumerate(rows) if r[:1] == [DEF_COLS[0]]), None)
            if at is None:
                bad.append(f"a per-case tab has no Defendants header ({len(rows)} rows)")
                continue
            for r in rows[at + 1 :]:
                t = _trim(r)
                if t and t != [NO_DEFENDANTS]:
                    got[tuple(t)] += 1
        return len(wb.worksheets), got, bad
    finally:
        wb.close()


def check(xlsx: bytes, ms: list[dict[str, Any]]) -> list[str]:
    """The built per-case file re-read from its bytes against the data: one
    tab per case, every defendant row once with the main list's values.
    Empty means it agrees."""
    tabs, got, out = _read(xlsx)
    want = collections.Counter(
        tuple(_trim([_s(book.clean(v)) for v in r["cells"][7:]])) for r in book.defendant_rows(ms)
    )
    if tabs != len(ms):
        out.append(f"the per-case list has {tabs} tabs for {len(ms)} cases")
    if got != want:
        out.append(
            f"the per-case list's defendant rows differ from the data: {sum((want - got).values())} missing, "
            f"{sum((got - want).values())} extra"
        )
    return out
