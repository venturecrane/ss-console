"""Build the DISC-001 catalogue from the Judicial Council's own PDF.

    python tools/forms/build_catalog.py            # rewrite the JSON (from operator/runners/jobs/)
    python tools/forms/build_catalog.py --check    # exit 1 on drift

A build tool, not seat code: it and the official PDF live under ``tools/``, so
neither ships in the jobs wheel and the PDF reading here is not a reader inside
``jobs/`` (the one reader of a firm's file is ``jobs/matter/pdf.py``). The JSON
it writes is the package data ``jobs/drafting/forms/`` loads.

Everything in the catalogue is READ from the PDF, never typed: the text of
each interrogatory from its text layer (whitespace normalised, a word broken
across a line end rejoined), the section titles from the form's own table of
contents, the checkbox rectangles from the form's drawn boxes and its AcroForm
widgets. Two things are authored here, each a fact about the form's printed
wording and each checked by a test against the extracted text:

* ``KEEP_HYPHEN``: line-end hyphens that are part of the word ("self-" in
  2.6(b) prints "self-employment"); every other line-end hyphen is a break;
* ``REPEATS_OVER``: what an interrogatory asks about "for each" of, where its
  wording says so (the phrase is stored and must appear in the text).

The PDF is untrusted data: it is read through PyMuPDF only, nothing in it runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parents[1]  # operator/runners/jobs
SOURCE = HERE / "disc001-rev-2024-01-01.pdf"
OUTPUT = RUNNER / "jobs" / "drafting" / "forms" / "disc001-rev-2024-01-01.json"
SOURCE_SHA256 = "d7b3dfa21d04759e7059eab9426afba0e44c50a256b4cc0ad2189cf8f2106f1e"

COLUMN_X = 300.0  # the form is two columns on a 612-point page
HEADER_Y = 45.0  # the "DISC-001" running head sits above this
FOOTER_Y = 725.0  # page footers, the form id and the print buttons sit below this
CAPTION_Y = 265.0  # page 1: the caption box (names, court, case number) sits above this

#: Line-end hyphens that belong to the word (read on page 2, item 2.6(b)).
KEEP_HYPHEN = frozenset({"self-"})
EN_DASH = chr(0x2013)  # a number range broken after its dash closes up

#: Defined terms of Sec. 4, singular first; plurals as printed ("PERSONS").
DEFINED_TERMS = (
    "YOU OR ANYONE ACTING ON YOUR BEHALF",
    "HEALTH CARE PROVIDER",
    "INCIDENT",
    "PERSON",
    "DOCUMENT",
    "ADDRESS",
)

#: What an interrogatory repeats over, ONLY where its own words say so; the
#: phrase must occur in the extracted text (tests). "none": the interrogatory
#: asks for the answering party's own single facts ("State: your name").
#: Every number not listed is "unknown": a judgment call left to the lead.
REPEATS_OVER: dict[str, tuple[str, str]] = {
    "1.1": ("person", "of each PERSON who prepared"),
    "2.1": ("none", "State:"), "2.2": ("none", "State the date and place of your birth"),
    "2.3": ("none", "did you have a driver's"), "2.4": ("none", "did you have any other"),
    "2.5": ("none", "your present residence"), "2.8": ("item", "for each conviction"),
    "2.9": ("none", "Can you speak English"), "2.10": ("none", "Can you read and write"),
    "2.11": ("none", "were you acting as an agent"), "2.12": ("person", "for each person state"),
    "2.13": ("person", "for each person state"),
    "3.1": ("none", "Are you a corporation"), "3.2": ("none", "Are you a partnership"),
    "3.3": ("none", "Are you a limited liability company"), "3.4": ("none", "Are you a joint venture"),
    "3.5": ("none", "Are you an unincorporated association"), "3.6": ("item", "for each fictitious name"),
    "3.7": ("item", "for each license or registration"),
    "4.1": ("item", "for each policy"), "4.2": ("none", "Are you self-insured"),
    "6.1": ("none", "Do you attribute any physical"), "6.3": ("item", "for each complaint"),
    "6.4": ("provider", "for each HEALTH CARE PROVIDER"), "6.5": ("item", "for each medication"),
    "6.6": ("item", "for each service"), "6.7": ("item", "for each injury"),
    "7.1": ("item", "for each item of property"), "7.2": ("item", "for each estimate or evaluation"),
    "7.3": ("item", "for each item"),
    "8.1": ("none", "Do you attribute any loss of income"), "8.2": ("none", "the nature of your work"),
    "8.3": ("none", "State the last date"), "8.4": ("none", "State your monthly income"),
    "8.5": ("employer", "at each place of employment"), "8.6": ("none", "State the dates you did not work"),
    "8.7": ("none", "State the total income"), "8.8": ("none", "Will you lose income"),
    "9.1": ("item", "for each item of damage"),
    "10.1": ("item", "If so, for each state"), "10.3": ("item", "for each incident giving rise"),
    "11.1": ("item", "for each action, claim, or demand"), "11.2": ("item", "for each claim or demand"),
    "12.2": ("person", "for each individual state"), "12.3": ("item", "for each statement"),
    "12.5": ("item", "for each item"), "12.7": ("item", "for each inspection"),
    "13.1": ("item", "for each surveillance"), "13.2": ("item", "for each written report"),
    "14.2": ("person", "for each PERSON state"), "15.1": ("item", "and for each:"),
    "16.1": ("person", "for each PERSON:"), "16.2": ("none", "plaintiff was not injured"),
    "16.3": ("item", "for each injury:"), "16.9": ("person", "for each plaintiff state"),
    "16.10": ("person", "for each plaintiff state"), "17.1": ("item", "for each response that is not"),
    "20.1": ("none", "State the date, time, and place"), "20.2": ("vehicle", "For each vehicle involved"),
    "20.3": ("none", "where your trip"), "20.4": ("none", "Describe the route"),
    "20.5": ("vehicle", "of each vehicle involved"), "20.6": ("none", "occur at an intersection"),
    "20.7": ("none", "a traffic signal facing you"),
    "50.1": ("item", "For each agreement alleged"), "50.2": ("item", "for each breach"),
}  # fmt: skip

_SEC_HEAD = re.compile(r"^Sec\.\s*(\d)\.\s*(.*)$")
_SECTION = re.compile(r"^(\d{1,3})\.0\s+(.*\S)\s*$")
_NUMBER = re.compile(r"^(\d{1,3})\.(\d{1,2})\s+(.*\S)\s*$")
_LETTER = re.compile(r"^\(([a-z])\)\s*(.*)$")
_DIGIT = re.compile(r"^\((\d)\)\s*(.*)$")
_DEF_LABEL = re.compile(r"^\(([a-z]|\d)\)\s*(.*)$")
_REVISION = re.compile(r"DISC-001\s*\[(Rev\.\s+[A-Z][a-z]+\s+\d{1,2},\s+\d{4})\]")
_FIELD_NUMBER = re.compile(r"List(\d+)\\?\.(\d+)")


@dataclass(frozen=True)
class Line:
    page: int
    col: int
    y: float
    x: float
    text: str


def ws(text: str) -> str:
    return " ".join(text.split())


def join_lines(parts: list[str], repairs: list[dict[str, str]], where: str) -> str:
    """Printed lines into one string: a word broken at a line end ("com-" /
    "plaints") is rejoined and the repair recorded; "self-" keeps its hyphen;
    a number range broken after its dash ("2034.210-" / "2034.310") closes up."""
    acc = ""
    for raw in parts:
        nxt = ws(raw)
        if not nxt:
            continue
        if not acc:
            acc = nxt
            continue
        last = acc.split()[-1]
        if acc.endswith("-") and last not in KEEP_HYPHEN and acc[-2:-1].isalpha() and nxt[:1].islower():
            repairs.append(
                {"where": where, "printed": f"{last} / {nxt.split()[0]}", "joined": last[:-1] + nxt.split()[0]}
            )
            acc = acc[:-1] + nxt
        elif acc.endswith("-") or acc.endswith(EN_DASH):
            repairs.append({"where": where, "printed": f"{last} / {nxt.split()[0]}", "joined": last + nxt.split()[0]})
            acc = acc + nxt
        else:
            acc = f"{acc} {nxt}"
    return ws(acc)


def read_lines(doc: Any) -> list[Line]:
    """Every text line, in reading order: page, then column, then top to bottom,
    without running heads, footers or the page-1 caption box."""
    out: list[Line] = []
    for pi, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                text = "".join(s["text"] for s in ln["spans"])
                x0, y0 = ln["bbox"][0], ln["bbox"][1]
                if not text.strip() or y0 < HEADER_Y or y0 >= FOOTER_Y or (pi == 0 and y0 < CAPTION_Y):
                    continue
                out.append(Line(pi, 0 if x0 < COLUMN_X else 1, round(y0, 1), round(x0, 1), text))
    out.sort(key=lambda ln: (ln.page, ln.col, ln.y, ln.x))
    return out


def split_front(lines: list[Line]) -> tuple[list[Line], list[Line]]:
    """(front matter: Sec. 1 to the Sec. 5 contents, the interrogatories)."""
    for i, ln in enumerate(lines):
        if _SECTION.match(ws(ln.text)) and ln.page == 1 and ln.col == 1:
            return lines[:i], lines[i:]
    raise ValueError("the first section heading (1.0) was not found on page 2")


def _labelled(
    lines: list[Line], pattern: re.Pattern[str], repairs: list[dict[str, str]], where: str
) -> list[list[str]]:
    """[[label, text], ...]; lines before the first label go to label ""."""
    groups: list[tuple[str, list[str]]] = [("", [])]
    for ln in lines:
        m = pattern.match(ws(ln.text))
        if m:
            groups.append((m.group(1), [m.group(2)]))
        else:
            groups[-1][1].append(ln.text)
    return [[k, join_lines(v, repairs, f"{where}({k})")] for k, v in groups if k or join_lines(v, [], "")]


def parse_front(lines: list[Line], repairs: list[dict[str, str]]) -> dict[str, Any]:
    secs: dict[str, list[Line]] = {}
    current = ""
    for ln in lines:
        m = _SEC_HEAD.match(ws(ln.text))
        if m:
            current = m.group(1)
            secs[current] = []
            continue
        if current:
            secs[current].append(ln)
    # Sec. 4(a) has two numbered alternatives, (1) and (2), each at a line start.
    defs = [
        [f"a({k})" if k.isdigit() else (k or "intro"), t]
        for k, t in _labelled(secs["4"], _DEF_LABEL, repairs, "Sec. 4")
    ]
    contents: list[list[str]] = []
    for ln in secs["5"]:
        m = _SECTION.match(ws(ln.text))
        if m:
            contents.append([f"{m.group(1)}.0", ws(m.group(2))])
        elif contents:
            contents[-1][1] = ws(f"{contents[-1][1]} {ln.text}")
    return {
        "instructions_answering": _labelled(secs["3"], _LETTER, repairs, "Sec. 3"),
        "definitions": defs,
        "contents": contents,
    }


@dataclass
class _Open:
    number: str
    section: str
    first: Line
    stem: list[str]
    subparts: list[tuple[str, list[str]]]


def _close(cur: _Open, repairs: list[dict[str, str]]) -> dict[str, Any]:
    letters = [k for k, _ in cur.subparts]
    if letters != [chr(ord("a") + i) for i in range(len(letters))]:
        raise ValueError(f"{cur.number}: subparts are not lettered a, b, c in order: {letters}")
    stem = join_lines(cur.stem, repairs, cur.number)
    subs = [[k, join_lines(v, repairs, f"{cur.number}({k})")] for k, v in cur.subparts]
    text = ws(" ".join([stem, *(f"({k}) {t}" for k, t in subs)]))
    kind, phrase = REPEATS_OVER.get(cur.number, ("unknown", ""))
    return {
        "number": cur.number,
        "section": cur.section,
        "stem": stem,
        "subparts": subs,
        "text": text,
        "defined_terms": terms_in(text),
        "repeats_over": kind,
        "repeats_over_phrase": phrase,
        "_first": [cur.first.page, cur.first.y, cur.first.x],
    }


def parse_body(lines: list[Line], repairs: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(sections as printed in the body, interrogatories in form order)."""
    sections: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    cur: _Open | None = None
    for ln in lines:
        t = ws(ln.text)
        sec, num, let = _SECTION.match(t), _NUMBER.match(t), _LETTER.match(t)
        if sec:
            if cur:
                items.append(_close(cur, repairs))
            cur = None
            sections.append({"number": f"{sec.group(1)}.0", "heading": ws(sec.group(2))})
        elif num and sections and num.group(1) == sections[-1]["number"][:-2] and num.group(2) != "0":
            if cur:
                items.append(_close(cur, repairs))
            cur = _Open(f"{num.group(1)}.{num.group(2)}", sections[-1]["number"], ln, [num.group(3)], [])
        elif cur and let:
            cur.subparts.append((let.group(1), [let.group(2)]))
        elif cur:
            (cur.subparts[-1][1] if cur.subparts else cur.stem).append(ln.text)
        else:
            raise ValueError(f"page {ln.page + 1}: text outside any interrogatory: {t!r}")
    if cur:
        items.append(_close(cur, repairs))
    return sections, items


def terms_in(text: str) -> list[str]:
    found: list[str] = []
    rest = text
    for term in DEFINED_TERMS:
        pat = re.compile(rf"\b{re.escape(term)}(?:S|ES)?\b")
        if pat.search(rest):
            found.append(term)
            rest = pat.sub(" ", rest)
    return found


def _r(rect: Any) -> list[float]:
    return [round(float(v), 1) for v in rect]


def _drawn_box(page: Any, wrect: Any) -> list[float]:
    """The printed box around a checkbox widget (an 18-by-9 point rectangle)."""
    cx, cy = (wrect.x0 + wrect.x1) / 2, (wrect.y0 + wrect.y1) / 2
    hits = [
        it[1]
        for d in page.get_drawings()
        for it in d["items"]
        if it[0] == "re" and it[1].x0 <= cx <= it[1].x1 and it[1].y0 <= cy <= it[1].y1 and 6 <= it[1].height <= 13
    ]
    if len(hits) != 1:
        raise ValueError(f"page {page.number + 1}: {len(hits)} printed boxes around the widget at {_r(wrect)}")
    return _r(hits[0])


def checkboxes(doc: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pi, page in enumerate(doc):
        for w in page.widgets():
            if w.field_type_string != "CheckBox":
                continue
            out.append({"page": pi, "widget": _r(w.rect), "box": _drawn_box(page, w.rect), "field": w.field_name})
    return out


def _beside(boxes: list[dict[str, Any]], page: int, y: float, x: float) -> dict[str, Any]:
    """The one checkbox on ``page`` left of a line starting at (x, y), level with it."""
    near = [b for b in boxes if b["page"] == page and abs(b["box"][1] - y) < 6 and 0 < x - b["box"][2] < 30]
    if len(near) != 1:
        raise ValueError(f"page {page + 1}: {len(near)} checkboxes beside the line at y={y}")
    return near[0]


def assign_boxes(doc: Any, items: list[dict[str, Any]], front: list[Line]) -> tuple[dict[str, Any], list[str]]:
    """Each interrogatory's checkbox, and the two Sec. 4(a) definition boxes.
    Returns (definition boxes, notes where a widget's field name disagrees
    with the number printed beside it)."""
    boxes = checkboxes(doc)
    notes: list[str] = []
    used: set[int] = set()
    for it in items:
        page, y, x = it.pop("_first")
        b = _beside(boxes, page, y, x)
        used.add(id(b))
        it["box"] = {"page": page, "rect": b["box"], "widget": b["widget"]}
        m = _FIELD_NUMBER.search(b["field"])
        if not m or f"{m.group(1)}.{m.group(2)}" != it["number"]:
            notes.append(f"{it['number']}: the widget beside it is named {b['field']!r}")
    defs: dict[str, Any] = {}
    for ln in front:
        m = _DIGIT.match(ws(ln.text))
        if m and "INCIDENT" in ln.text:
            b = _beside(boxes, ln.page, ln.y, ln.x)
            used.add(id(b))
            defs[f"4(a)({m.group(1)})"] = {"page": ln.page, "rect": b["box"], "widget": b["widget"]}
    if len(used) != len(boxes):
        raise ValueError(f"{len(boxes) - len(used)} checkboxes on the form are not beside any interrogatory")
    return defs, notes


def build(pdf: Path = SOURCE) -> dict[str, Any]:
    import pymupdf

    data = pdf.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    doc = pymupdf.open(stream=data, filetype="pdf")
    rev = _REVISION.search(str(doc[0].get_text()))
    if not rev:
        raise ValueError("the revision footer was not found on page 1")
    repairs: list[dict[str, str]] = []
    front, body = split_front(read_lines(doc))
    head = parse_front(front, repairs)
    sections, items = parse_body(body, repairs)
    titles = dict((n, t) for n, t in head["contents"])
    for s in sections:
        s["title"] = titles[s["number"]]
    defs, notes = assign_boxes(doc, items, front)
    used = {i["section"] for i in items}
    return {
        "form": "DISC-001",
        "revision": ws(rev.group(1)),
        "title": ws((doc.metadata or {}).get("title") or ""),
        "source": {"file": f"tools/forms/{pdf.name}", "sha256": sha, "bytes": len(data), "pages": doc.page_count},
        "page_size": [round(doc[0].rect.width, 1), round(doc[0].rect.height, 1)],
        "generated_by": "python tools/forms/build_catalog.py",
        "defined_terms": list(DEFINED_TERMS),
        "instructions_answering": head["instructions_answering"],
        "definitions": head["definitions"],
        "contents": head["contents"],
        "sections": [s for s in sections if s["number"] in used],
        "reserved_sections": [s["number"] for s in sections if s["number"] not in used],
        "interrogatories": items,
        "definition_boxes": defs,
        "repairs": repairs,
        "field_name_notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the DISC-001 catalogue from the official PDF.")
    ap.add_argument("--check", action="store_true", help="exit 1 when the committed JSON differs from the PDF")
    args = ap.parse_args(argv)
    built = build()
    if built["source"]["sha256"] != SOURCE_SHA256:
        print(f"the source PDF's sha256 is {built['source']['sha256']}, not the pinned one", file=sys.stderr)
        return 1
    if args.check:
        same = json.loads(OUTPUT.read_text(encoding="utf-8")) == built
        print("catalogue matches the PDF" if same else "catalogue DRIFTED from the PDF: rebuild it")
        return 0 if same else 1
    OUTPUT.write_text(json.dumps(built, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.name}: {len(built['interrogatories'])} interrogatories, {len(built['repairs'])} repairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
