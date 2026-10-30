"""`printout`: the status list as one printable PDF, a case to a page.

WHY (2026-10-29). The attorneys print the list, and a case's dates belong
together on the page they hold: every court date (from the court papers and
from the firm's own calendar, each naming its source, ``courtdates.py``), then
the defendants and the discovery exactly as the workbook shows them.

Cases run in attorney order (the Smokeball responsible person), then by case
name. Every case starts on a new page. Built from the same shown values as
the workbook (never a second read), every string through ``book.clean``, and
the built file is scanned with the workbook's own leak patterns before it is
filed (``scan``).
"""

from __future__ import annotations

import datetime as dt
import html
import io
import re
import unicodedata
from pathlib import Path
from typing import Any

from . import book, percase, vocab

SUFFIX = " court dates"
PAGE_W, PAGE_H = 792.0, 612.0  # landscape Letter, points
MARGIN = 36.0
FOOT = 20.0
CSS = """
* { font-family: sans-serif; }
body { font-size: 8.5pt; }
h1 { font-size: 13pt; margin: 0 0 4pt 0; }
h2 { font-size: 10pt; margin: 8pt 0 3pt 0; }
h3 { font-size: 9pt; margin: 5pt 0 2pt 0; }
p { margin: 0 0 3pt 0; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #dddddd; text-align: left; font-weight: bold; }
th, td { border: 0.5pt solid #999999; padding: 2pt 3pt; vertical-align: top; }
.flag { font-weight: bold; }
"""
COURT_HDR = ["Date", "Time", "What", "Source"]
DISC_HDR = ["Direction", "Set", "Party", "Date served", "Source", "Responses served", "Responses date",
            "Responses (source)"]  # fmt: skip
NO_DATES = "No upcoming court date in the court papers or on the calendar."
CAL_UNREAD = "The calendar could not be read for this case; the court papers' date is shown alone."


def path_for(main: Path) -> Path:
    """'<main stem> court dates.pdf', beside the list it was built with."""
    return main.with_name(f"{main.stem}{SUFFIX}.pdf")


def order(ms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attorney (responsible) order, then case name."""
    return sorted(ms, key=lambda m: ((m.get("responsible") or "~").lower(), str(_case(m)).lower()))


def _case(m: dict[str, Any]) -> str:
    return str(m.get("case_name") or m.get("title") or "")


#: A run of this many characters with no space is broken with zero-width
#: spaces: the layout cannot fit a token wider than its column and would add
#: pages forever (2026-10-29, a 39-character file name in a discovery cell).
#: ``text_of`` strips them again, so the review and the leak scan read the
#: text exactly as written.
_LONG = re.compile(r"\S{%d,}" % 16)
ZWSP = "\u200b"
#: Pages one case may take before the layout is called stuck (the longest real
#: case took 28).
MAX_CASE_PAGES = 80


def _breakable(t: str) -> str:
    return _LONG.sub(lambda m: ZWSP.join(m.group(0)[i : i + 12] for i in range(0, len(m.group(0)), 12)), t)


def _e(v: Any) -> str:
    return html.escape(_breakable(str(book.clean(v) if v is not None else "")))


def _table(header: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def court_rows(m: dict[str, Any]) -> list[list[Any]]:
    """The case's court-date table: the next court date first, then each kind
    the calendar holds; where the papers and the calendar disagree, both."""
    cd = m.get("court_dates") or {}
    out: list[list[Any]] = []
    nxt = m.get("next_court_date") or {}
    cal = cd.get("calendar") or {}
    if nxt.get("date"):
        same = cal.get("date") == nxt["date"]
        t = (cal.get("time") or "") if same else ""
        what = str(nxt.get("event") or "")
        if t and what.startswith(f"{t}, "):
            what = what[len(t) + 2 :]
        out.append(["Next court date", nxt["date"], t, what, book._src(nxt) or ""])
    papers = cd.get("papers")
    if cd.get("flag") and papers:
        out.append(["Court papers say", papers.get("date"), "", papers.get("event") or "", book._src(papers)])
    for label, r in cd.get("rows") or []:
        out.append([label, r["date"], r["time"], r["what"], r["source"]])
    return out


def _facts(m: dict[str, Any]) -> list[tuple[str, Any]]:
    filed, why = percase.complaint(m)
    fs: list[tuple[str, Any]] = [
        ("Matter #", m.get("number")),
        ("Responsible (Smokeball)", m.get("responsible") or ""),
        ("Court / case no.", " ".join(x for x in (m.get("court"), m.get("case_number")) if x)),
        ("Complaint filed", filed + (f" ({why})" if why else "")),
        ("Case status", book._status(m)),
    ]
    if book._status(m) == vocab.NOT_FILED:
        fs.append(("Not filed: where it stands", book.not_filed_text(m)))
    return fs


def case_html(m: dict[str, Any], drows: list[list[Any]], disc: list[list[Any]], discovery_note: str) -> str:
    cd = m.get("court_dates") or {}
    parts = [f"<h1>{_e(m.get('number'))} {_e(_case(m))}</h1>"]
    parts.append("".join(f"<p><b>{_e(k)}:</b> {_e(v)}</p>" for k, v in _facts(m)))
    parts.append("<h2>Court dates</h2>")
    rows = court_rows(m)
    parts.append(_table(["", *COURT_HDR], rows) if rows else f"<p>{_e(NO_DATES)}</p>")
    if cd.get("flag"):
        parts.append(f"<p class='flag'>{_e(cd['flag'])}</p>")
    if cd and not cd.get("read"):
        parts.append(f"<p>{_e(CAL_UNREAD)}</p>")
    parts.append("<h2>Defendants</h2>")
    parts.append(_table(percase.DEF_COLS, drows) if drows else f"<p>{_e(percase.NO_DEFENDANTS)}</p>")
    if isinstance(m.get("discovery12"), dict):
        parts.append(discovery12_html(m["discovery12"]))
    else:
        parts.append("<h2>Discovery</h2>")
        if discovery_note:
            parts.append(f"<p>{_e(discovery_note)}</p>")
        parts.append(_table(DISC_HDR, disc) if disc else "<p>None listed.</p>")
    notes = book.case_notes_text(m)
    if notes:
        parts.append(f"<h2>Case notes</h2><p>{_e(notes)}</p>")
    return "<body>" + "".join(parts) + "</body>"


#: The 12-section discovery breakdown on a case's pages (``discovery12/``).
D12_HDR = ["Item", "Direction", "Party", "Served", "Due", "Response", "Extension", "Notes"]
D12_NOT_READ = "No discovery paper was found by name in the file; not read."
#: A case the reading service declined twice (discovery12/run.py, DECLINED_REASON).
D12_DECLINED = "Not read: the reading service declined this file; check it by hand"


def _dated(v: Any, extra: str = "") -> str:
    """A date with the paper it was read from: '2026-03-05 (paper.pdf, 2026-03-05)'."""
    v = v if isinstance(v, dict) else {}
    src = book._src(v)
    if v.get("unconfirmed"):  # the gates could not find it in its paper (discovery12/sections.py)
        from .discovery12.sections import UNCONFIRMED

        return " ".join(x for x in (UNCONFIRMED, f"({src})" if src else "", extra) if x)
    d = str(v.get("date") or "")
    if not d:
        return extra
    return " ".join(x for x in (d, f"({src})" if src else "", extra) if x)


def d12_rows(entries: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for e in entries:
        resp = e.get("response") or {}
        ext = e.get("extension") or {}
        rows.append(
            [
                e.get("item"),
                e.get("direction"),
                e.get("party"),
                _dated(e.get("served")),
                _dated(e.get("due")),
                " ".join(x for x in (str(resp.get("value") or ""), _dated(resp)) if x),
                _dated(ext, str(ext.get("detail") or "")),
                e.get("notes"),
            ]
        )
    return rows


def discovery12_html(d: dict[str, Any]) -> str:
    """Every section with its entries, or 'None in file'; every date naming its paper."""
    from .discovery12 import sections as d12

    parts = ["<h2>Discovery (12 sections)</h2>"]
    if d.get("declined"):
        return parts[0] + f"<p class='flag'>{_e(D12_DECLINED)}</p>"
    if not d.get("read"):
        return parts[0] + f"<p>{_e(D12_NOT_READ)}</p>"
    for key, label in d12.SECTIONS:
        got = (d.get("sections") or {}).get(key) or []
        parts.append(f"<h3>{_e(label)}</h3>")
        parts.append(_table(D12_HDR, d12_rows(got)) if got else f"<p>{_e(d12.NONE_IN_FILE)}</p>")
    for f in d.get("flags") or []:
        parts.append(f"<p class='flag'>{_e(f)}</p>")
    return "".join(parts)


def d12_dates(m: dict[str, Any]) -> list[str]:
    """Every date the case's discovery sections print (the check reads them back)."""
    from .discovery12 import sections as d12

    d = m.get("discovery12")
    if not isinstance(d, dict) or not d.get("read"):
        return []
    return [str(v.get("date")) for _p, v in d12.dated_values(d)]


def _cover(ms: list[dict[str, Any]], as_of: dt.date, discovery_note: str) -> str:
    flagged = sum(1 for m in ms if (m.get("court_dates") or {}).get("flag"))
    dated = sum(1 for m in ms if (m.get("next_court_date") or {}).get("date"))
    lines = [
        f"Cases: {len(ms)}, in attorney order, then by case name. Each case starts on a new page.",
        f"Cases with an upcoming court date: {dated}.",
        "Each date names its source: the court paper it was read from, or the Smokeball calendar "
        "(entries LawToolBox made are marked LawToolBox).",
        f"Cases where the court papers give a court date the calendar does not have: {flagged}. "
        "Each is marked on its page.",
    ]
    if discovery_note:
        lines.append(discovery_note)
    if any(isinstance(m.get("discovery12"), dict) for m in ms):
        lines.append(
            "Discovery is shown in twelve sections. Each date names the paper it was read from; "
            "a section with nothing found reads None in file."
        )
    body = "".join(f"<p>{_e(x)}</p>" for x in lines)
    return f"<body><h1>Litigation status, court dates, as of {_e(as_of.isoformat())}</h1>{body}</body>"


#: Points taken off the bottom of the frame, tried in turn until a case lays
#: out clean. MuPDF's layout can cycle on one exact break position: on
#: 2026-10-30 one case of 230 repeated the same three pages and a blank one
#: forever (pymupdf 1.28.2) and failed the whole run at the page cap. Moving
#: the break by 4 points laid it out in 10 pages; the effect is not monotonic
#: (16 points cycled again), so every attempt is checked, never assumed.
TRIMS = (0.0, 4.0, 8.0, 24.0, 36.0)


def cycled(pages: list[str]) -> bool:
    """A laid-out case is broken when a page is blank or repeats an earlier
    page's text: the layout engine went round instead of forward."""
    return any(not t.strip() for t in pages) or len(set(pages)) < len(pages)


def _lay(html_: str, trim: float) -> bytes | None:
    """One case laid out alone in a frame ``trim`` points shorter; None when
    it ran past the page cap or cycled."""
    import pymupdf

    where = pymupdf.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN - FOOT - trim)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=html_, user_css=CSS)
    n, more = 0, True
    while more and n < MAX_CASE_PAGES:
        dev = writer.begin_page(pymupdf.Rect(0, 0, PAGE_W, PAGE_H))
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        n += 1
    writer.close()
    if more:
        return None
    with pymupdf.open(stream=buf.getvalue(), filetype="pdf") as doc:
        return None if cycled([p.get_text() for p in doc]) else buf.getvalue()


def _place(doc: Any, html_: str) -> int:
    """Append one case, laid out clean, to ``doc``; returns its page count."""
    import pymupdf

    for trim in TRIMS:
        got = _lay(html_, trim)
        if got is not None:
            with pymupdf.open(stream=got, filetype="pdf") as part:
                doc.insert_pdf(part)
                return part.page_count
    from .outcome import LitigationFailed

    raise LitigationFailed(f"stage_unfinished: book: a case of the print-out did not fit in {MAX_CASE_PAGES} pages")


def build(ms: list[dict[str, Any]], out: Path, as_of: dt.date, *, discovery_note: str = "") -> dict[str, Any]:
    """Write the PDF at ``out`` from the SHOWN values (``courtdates.shown``);
    returns its stats."""
    import pymupdf

    cases = order(ms)
    by_def: dict[int, list[list[Any]]] = {}
    for r in book.defendant_rows(ms):
        by_def.setdefault(id(r["m"]), []).append(r["cells"][7:])
    doc = pymupdf.open()
    pages = _place(doc, _cover(cases, as_of, discovery_note))
    starts = []
    for m in cases:
        disc = [r[3:] for r in book._discovery_rows([m])]
        starts.append(pages)
        pages += _place(doc, case_html(m, by_def.get(id(m), []), disc, discovery_note))
    title = {s: f"{m.get('number') or ''} {book.clean(_case(m))}" for s, m in zip(starts, cases)}
    head = ""
    for i, page in enumerate(doc):
        head = title.get(i, head)
        foot = f"{head}    page {i + 1} of {doc.page_count}".strip()
        page.insert_text((MARGIN, PAGE_H - MARGIN / 2), foot, fontsize=7)
    out.parent.mkdir(parents=True, exist_ok=True)
    # garbage=4 merges identical objects: each case is laid out alone and
    # carries its own copy of the fonts, which made a 1,005-page book 50.7 MB
    # instead of 4.1 MB (2026-10-30).
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    return {
        "as_of": as_of.isoformat(),
        "cases": len(cases),
        "pages": pages,
        "with_court_date": sum(1 for m in cases if (m.get("next_court_date") or {}).get("date")),
        "flagged": sum(1 for m in cases if (m.get("court_dates") or {}).get("flag")),
    }


def text_of(pdf: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return [unicodedata.normalize("NFKC", p.get_text()).replace(ZWSP, "") for p in doc]


def scan(pdf: Path) -> list[tuple[str, str, str]]:
    """The workbook's leak patterns over every page's text: (page, what, match)."""
    from .gates import LEAK_PATTERNS

    out = []
    for i, t in enumerate(text_of(pdf), 1):
        for what, rx in LEAK_PATTERNS:
            hit = rx.search(t)
            if hit:
                out.append((f"page {i}", what, hit.group(0)))
    return out


def check(pdf: Path, ms: list[dict[str, Any]]) -> list[str]:
    """The built PDF re-read from its text against the shown values: every
    case's heading once, and every upcoming court date it shows printed on
    that case's pages. Empty means it agrees."""
    pages = text_of(pdf)
    whole = "\n".join(pages)
    out = []
    for m in order(ms):
        head = f"{m.get('number')} "
        if whole.count(f"\n{head}") + whole.startswith(head) < 1:
            out.append(f"matter {m.get('number')} has no page in the print-out")
            continue
        dates = {r[1] for r in court_rows(m) if r[1]}
        missing = [d for d in sorted(dates) if str(d) not in whole]
        if missing:
            out.append(f"matter {m.get('number')}: {len(missing)} court dates are not printed")
        lost = [d for d in sorted(set(d12_dates(m))) if d not in whole]
        if lost:
            out.append(f"matter {m.get('number')}: {len(lost)} discovery dates are not printed")
    return out


__all__ = ["build", "check", "court_rows", "order", "path_for", "scan"]
