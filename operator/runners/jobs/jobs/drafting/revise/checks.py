"""What code checks about a revision, item by item and across the brief.

Per item (``check_item``): ``met`` or ``not_met`` with the reason, or ``read``
when code cannot decide it (an ``other`` item), which the narrow model reading
(``reading.py``) and the attorney notes then carry.

Across the brief (``invariants``), whatever the items say:

* every section no item names is byte for byte the filed text;
* a section's heading line stays;
* a table stays byte for byte unless an item for its section is about tables;
* no ``{{ATTORNEY ...}}`` marker is lost (the reserved judgment, render.py);
* no figure, number, date, quotation or citation appears in an edited section
  that the filed brief, or the attorney's own words, does not carry.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .checklist import Item
from . import sections as sec

_REC = r"\((?=[^()]*\bFILE:)[^()]*\)|\((?:Ex\.|Exh\.|Exhibit)\s*\d+[^()]*\)"
_DEP = r"\((?=[^()]*\bDep\.\s*\d+:\d+)[^()]*\)"
_CASE = (
    r"\b[A-Z][\w.'&-]*(?:\s+[A-Z&][\w.'&-]*)*\s+v\.\s+[A-Z][\w.'&, -]*?\s*\(\d{4}\)\s*\d+\s+[A-Z][\w. ]*?\d+(?:,\s*\d+)?"
    r"|\b\d+\s+(?:Cal\.|U\.S\.|F\.\s?\d?d|F\.\s?Supp\.|S\.\s?Ct\.)[\w. ]*?\s\d+"
)
_STAT = (
    r"§+\s*\d[\w.()-]*"
    r"|\b(?:Civ\.\s*Code|Code\s+Civ\.\s*Proc\.|Evid\.\s*Code|Veh\.\s*Code|Gov\.\s*Code|Ins\.\s*Code)\s*,?\s*§*\s*\d+[\w.()-]*"
)
CITES = {
    "record": re.compile(_REC),
    "deposition": re.compile(_DEP),
    "case": re.compile(_CASE),
    "statute": re.compile(_STAT),
}
CITES["all"] = re.compile("|".join(f"(?:{p.pattern})" for p in CITES.values()))
#: Removed by code, never by a model: a parenthetical drops out cleanly.
CODE_REMOVABLE = ("record", "deposition")

_FIGURE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?(?![\w])")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:,\s*\d{{4}})?|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b")
_QUOTE = re.compile(r"\"([^\"\n]{12,})\"")
#: A count written in words is a figure too ("three witnesses").
_NUMWORD = re.compile(
    r"(?i)\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|dozen|several|multiple|numerous)\b"
)
MARKER = re.compile(r"\{\{[^{}]*\}\}")
ATTORNEY = re.compile(r"\{\{\s*ATTORNEY\b")


@dataclass
class Status:
    id: str
    status: str  # met | not_met | read
    how: str  # code | reading | not checked
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plain(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return " ".join(s.split())


def strip_cites(text: str, kind: str) -> tuple[str, int]:
    """Code's own removal of record and deposition parentheticals, and the
    space a removal leaves before punctuation."""
    pat = CITES[kind]
    n = len(pat.findall(text))
    out = pat.sub("", text)
    out = re.sub(r"[ \t]+([.,;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out, n


def _phrases(item: Item, text: str) -> list[str]:
    p, t, out = item.params, plain(text).lower(), []
    for ph in p.get("must_include") or []:
        if plain(ph).lower() not in t:
            out.append(f"does not say {ph!r}")
    for ph in p.get("must_exclude") or []:
        if plain(ph).lower() in t:
            out.append(f"still says {ph!r}")
    return out


def _target_text(item: Item, edited: list[sec.Section]) -> str:
    return "".join(s.text for s in sec.find(edited, item.target))


def check_item(item: Item, prior: list[sec.Section], edited: list[sec.Section]) -> Status:
    """One item against the edited brief (formatting items: ``check_docx``)."""
    old, new = _target_text(item, prior), _target_text(item, edited)
    if item.kind == "formatting":
        return Status(item.id, "read", "code", "checked in the rendered Word file")
    if not new and item.kind != "restructure_section":
        return Status(item.id, "not_met", "code", f"the section {item.target!r} is not in the revised brief")
    probs = _phrases(item, new)
    if item.kind == "remove_citations":
        kind = item.params["cite_kind"]
        left = CITES[kind].findall(new)
        was = len(CITES[kind].findall(old))
        if left:
            probs.append(f"{len(left)} {kind} citation(s) remain, first: {plain(left[0])[:80]!r}")
        elif not probs:
            return Status(item.id, "met", "code", f"{was} {kind} citation(s) removed; none remain")
    elif item.kind == "shorten_section":
        probs += _shorter(item, old, new)
    elif item.kind == "restructure_section":
        probs += _restructured(item, edited, new)
    elif item.kind == "replace_text":
        o, n = plain(item.params["old"]).lower(), plain(item.params["new"]).lower()
        if o in plain(new).lower() and o not in n:
            probs.append(f"still says {item.params['old']!r}")
        if n and n not in plain(new).lower():
            probs.append(f"does not say {item.params['new']!r}")
    elif item.kind == "other":
        if probs:
            return Status(item.id, "not_met", "code", "; ".join(probs))
        return Status(item.id, "read", "reading", "code cannot decide this item; read against the request")
    if probs:
        return Status(item.id, "not_met", "code", "; ".join(probs))
    return Status(item.id, "met", "code", "checked by code")


def _shorter(item: Item, old: str, new: str) -> list[str]:
    p, out = item.params, []
    counts = {
        "max_paragraphs": (len(sec.paragraphs(new)), "paragraphs"),
        "max_sentences": (len(sec.sentences(new)), "sentences"),
        "max_words": (sec.words(new), "words"),
    }
    for key, (have, what) in counts.items():
        if key in p and have > int(p[key]):
            out.append(f"{have} {what}, over the {p[key]} asked for")
    w_old, w_new = sec.words(old), sec.words(new)
    if "max_ratio" in p and w_new > float(p["max_ratio"]) * w_old:
        out.append(f"{w_new} words of {w_old}, over the {p['max_ratio']:.0%} asked for")
    if w_new >= w_old:
        out.append(f"not shorter ({w_new} words, was {w_old})")
    return out


def _restructured(item: Item, edited: list[sec.Section], new: str) -> list[str]:
    p, out = item.params, []
    if p.get("subheadings"):
        heads = [sec.norm_title(m.group(2)) for ln in new.splitlines() for m in [sec.HEADING.match(ln)] if m]
        want = [sec.norm_title(h) for h in p["subheadings"]]
        pos = [heads.index(w) if w in heads else -1 for w in want]
        if -1 in pos:
            out.append("missing sub-heading(s): " + ", ".join(h for h, i in zip(p["subheadings"], pos) if i < 0))
        elif pos != sorted(pos):
            out.append("the sub-headings are not in the order asked for")
    if p.get("into_list") and len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+\S", new)) < 2:
        out.append("not set out as a list")
    for key, before in (("move_before", True), ("move_after", False)):
        if key not in p:
            continue
        titles = [sec.norm_title(s.title) for s in edited if s.heading]
        me, other = sec.find(edited, item.target), sec.find(edited, p[key])
        if not me or not other:
            out.append(f"{key}: a section it names is not in the revised brief")
            continue
        a, b = titles.index(sec.norm_title(me[0].title)), titles.index(sec.norm_title(other[0].title))
        if (a > b) if before else (a < b):
            out.append(f"not moved {'before' if before else 'after'} {p[key]!r}")
    return out


# ---- across the brief --------------------------------------------------------------
def tokens(text: str) -> set[str]:
    t = plain(text)
    out = {m.group(0).replace(" ", "") for m in _FIGURE.finditer(t)}
    out |= {m.group(0).rstrip(",") for m in _NUMBER.finditer(t)}
    out |= {" ".join(m.group(0).split()) for m in _DATE.finditer(t)}
    out |= {m.group(1).strip().lower() for m in _QUOTE.finditer(t)}
    out |= {" ".join(m.group(0).split()) for m in CITES["all"].finditer(t)}
    out |= {m.group(0) for m in MARKER.finditer(t)}
    out |= {f"#{m.group(1).lower()}" for m in _NUMWORD.finditer(t)}
    return out


def _token_known(tok: str, known: str, known_tokens: set[str]) -> bool:
    return tok in known_tokens or tok.lower() in known


def _about_tables(items: Iterable[Item]) -> bool:
    return any(
        "table" in plain(i.quote).lower() or i.params.get("tables") is True for i in items if i.kind != "formatting"
    )


def invariants(
    prior: list[sec.Section], edited: list[sec.Section], items: list[Item], allowed_text: str
) -> list[tuple[str, str]]:
    """(section id, finding) that hold whatever the items say; empty is
    clean. A finding about the whole brief carries the id ``""``."""
    out: list[tuple[str, str]] = []
    targeted: dict[str, list[Item]] = {}
    for it in items:
        if it.kind == "formatting":
            continue
        for s in sec.find(prior, it.target):
            targeted.setdefault(s.id, []).append(it)
    by_id = {s.id: s for s in edited}
    known = plain(sec.join(prior) + "\n" + allowed_text).lower()
    known_tokens = tokens(sec.join(prior) + "\n" + allowed_text)
    for s in prior:
        name = s.title or "the preamble"
        e = by_id.get(s.id)
        if e is None:
            out.append((s.id, f"section {name!r} is missing from the revision"))
            continue
        if s.id not in targeted:
            if e.text != s.text:
                out.append((s.id, f"section {name!r} changed, and no item asks for it"))
            continue
        if e.heading.strip() != s.heading.strip() and not _replaces_in(targeted[s.id], s.heading):
            out.append((s.id, f"the heading {name!r} changed"))
        if not _about_tables(targeted[s.id]):
            for t in sec.tables(s.body):
                if t not in e.text:
                    out.append((s.id, f"a table in {name!r} changed, and no item asks for it"))
        new = [x for x in tokens(e.text) - tokens(s.text) if not _token_known(x, known, known_tokens)]
        if new:
            out.append((s.id, f"{name!r} brings in what the brief does not carry: " + ", ".join(sorted(new)[:8])))
    if len(ATTORNEY.findall(sec.join(edited))) < len(ATTORNEY.findall(sec.join(prior))):
        out.append(("", "an attorney marker ({{ATTORNEY ...}}) was removed"))
    return out


def _replaces_in(items: list[Item], heading: str) -> bool:
    return any(i.kind == "replace_text" and plain(i.params["old"]).lower() in plain(heading).lower() for i in items)


# ---- formatting, in the rendered Word file ------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _spacing_ok(p: Any, want: float, size_pt: float) -> bool:
    sp = p._p.pPr.find(f"{_W}spacing") if p._p.pPr is not None else None
    if sp is None or sp.get(f"{_W}line") is None:
        return False
    line, rule = int(sp.get(f"{_W}line")), sp.get(f"{_W}lineRule") or "auto"
    if rule == "auto":
        return line == round(240 * want)
    return line >= round(size_pt * 20 * want)


def _exact_ok(p: Any, want_pt: float) -> bool:
    sp = p._p.pPr.find(f"{_W}spacing") if p._p.pPr is not None else None
    if sp is None or sp.get(f"{_W}line") is None:
        return False
    return sp.get(f"{_W}lineRule") == "exact" and int(sp.get(f"{_W}line")) == round(want_pt * 20)


def check_docx(
    path: Any, layout: dict[str, Any], size_pt: float, skip_tables: int, cls: str = "mediation_brief"
) -> list[str]:
    """What a formatting item asked of the file, read from its XML. Empty is
    met; every key it can carry is read here, so none is met on trust."""
    import docx

    from .. import briefpage
    from . import filed, layout as lay

    d, out = docx.Document(str(path)), []
    # The body is what follows the first heading, by role (the firm's style
    # names or SMD's): the front matter above it keeps the caption's own
    # spacing by design, and the signature lines are briefpage's.
    held = {id(p._p) for p in briefpage.signature_lines(d)}
    body = [p for p in filed.body_paragraphs(d) if id(p._p) not in held]
    heads = [p for p in d.paragraphs if lay.heading_level(p) and p.text.strip()]
    for paras, what, exact_key, multiple_key in (
        (body, "body paragraph(s)", "line_spacing_pt", "line_spacing"),
        (heads, "heading(s)", "heading_line_spacing_pt", "heading_line_spacing"),
    ):
        if exact_key in layout:  # exact points win over the multiple (the renderer's own rule)
            want_pt = float(layout[exact_key])
            bad = [p for p in paras if not _exact_ok(p, want_pt)]
            label = f"{want_pt:g} pt exactly"
        elif multiple_key in layout:
            bad = [p for p in paras if not _spacing_ok(p, float(layout[multiple_key]), size_pt)]
            label = f"line spacing {layout[multiple_key]}"
        else:
            continue
        if bad or not paras:
            out.append(f"{len(bad)} of {len(paras)} {what} not at {label}")
    out += _heading_space(heads, layout)
    out += _front(d, layout, cls)
    tables = d.tables[skip_tables:]
    if "table_font_pt" in layout:
        want = float(layout["table_font_pt"])
        runs = [
            r
            for t in tables
            for row in t.rows
            for c in row.cells
            for p in c.paragraphs
            for r in p.runs
            if r.text.strip()
        ]
        bad = [r for r in runs if r.font.size is None or abs(r.font.size.pt - want) > 0.01]
        if bad or not runs:
            out.append(f"{len(bad)} of {len(runs)} table run(s) not at {want:g} pt")
    if "table_header_fill" in layout:
        want_fill = str(layout["table_header_fill"]).upper()
        bad = [t for t in tables if not _row_filled(t.rows[0], want_fill)] if tables else [None]
        if bad:
            out.append(f"{len(bad)} table header row(s) not shaded {want_fill}")
    if "table_total_fill" in layout:
        from smokeball_connector.docx_classes import is_total_row

        want_fill = str(layout["table_total_fill"]).upper()
        totals = [row for t in tables for row in list(t.rows)[1:] if is_total_row([row.cells[0].text])]
        bad = [row for row in totals if not _row_filled(row, want_fill)]
        if bad or not totals:
            out.append(f"{len(bad)} of {len(totals)} table total row(s) not shaded {want_fill}")
    if "table_border_pt" in layout:
        want_sz = round(float(layout["table_border_pt"]) * 8)
        bad = [t for t in tables if _border_sz(t) != want_sz] if tables else [None]
        if bad:
            out.append(f"{len(bad)} table(s) without {layout['table_border_pt']} pt borders")
    return out


def _heading_space(heads: list[Any], layout: dict[str, Any]) -> list[str]:
    from docx.shared import Pt

    from . import layout as lay

    out = []
    for key, attr in (("heading_space_before_pt", "space_before"), ("heading_space_after_pt", "space_after")):
        if key not in layout:
            continue
        want = Pt(float(layout[key]))
        bad = [p for p in heads if (lay.eff(p, attr) or Pt(0)) != want]
        if bad or not heads:
            out.append(f"{len(bad)} of {len(heads)} heading(s) without {layout[key]:g} pt {attr.replace('_', ' ')}")
    return out


def _front(d: Any, layout: dict[str, Any], cls: str) -> list[str]:
    """The first-page and signature asks, by the renderer's own checks; a
    document without the line a key is about is not met (never vacuous)."""
    from .. import briefpage, frontpage
    from . import layout as lay

    asked = {k: v for k, v in layout.items() if k in lay.FRONT_KEYS}
    if not asked:
        return []
    fmt = {"layout": {cls: asked}}
    out = frontpage.check(d, fmt, cls) + briefpage.check(d, fmt, cls)
    have = lay.anchors(d)
    out += [f"{k}: the brief has no {have[k][1]} to lay it on" for k in asked if asked[k] and not have[k][0]]
    blk = briefpage._block(d)
    if asked.get("signature_gap_lines") and blk is not None and blk[1] != -1:
        # briefpage counts the gap only once the date sits on the "By:" line.
        paras, _dated, by = blk
        n = 0
        while by - 1 - n >= 0 and not paras[by - 1 - n].text.strip():
            n += 1
        if n != int(asked["signature_gap_lines"]):
            out.append(f"signature: {n} blank line(s) above the By line, not {asked['signature_gap_lines']}")
    return out


def _row_filled(row: Any, fill: str) -> bool:
    for c in row.cells:
        tcpr = c._tc.tcPr
        shd = tcpr.find(f"{_W}shd") if tcpr is not None else None
        if shd is None or str(shd.get(f"{_W}fill") or "").upper() != fill:
            return False
    return True


def _border_sz(t: Any) -> int | None:
    tblpr = t._tbl.tblPr
    borders = tblpr.find(f"{_W}tblBorders") if tblpr is not None else None
    top = borders.find(f"{_W}top") if borders is not None else None
    v = top.get(f"{_W}sz") if top is not None else None
    return int(v) if v is not None and str(v).isdigit() else None
