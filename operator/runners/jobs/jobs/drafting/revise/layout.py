"""The filed brief's own layout: read off the Word file, carried onto the
revision, and compared, so a revision never quietly flattens it.

THE FILED LAYOUT WINS. A brief the attorney accepted is his document as laid:
its paper (pleading or plain), its first page and signature block, its chart
borders, inner rules and fills. A revision renders the edited markdown with
the firm's renderer and then carries that layout onto it unchanged, unless
the attorney's request asks to change a part of it (a formatting item's
layout key, or "on pleading paper" / "plain paper" in his words).

``read`` is the layout as facets; ``compare`` names every facet that differs.
The base proof (``prior.prove``) compares the filed file against a re-render
of the base markdown, and the revision is compared again after it renders:
a facet lost either way stops the job (fail closed), because a flattened
filed brief is the harm this module exists to prevent.

A heading is read by its ROLE, never one style name: a paragraph style (or
its base chain) named "... Heading" or "Heading N", or carrying an outline
level, is a heading, so SMD's styles, Word's built-ins and a firm's own
("Brief Heading") all read the same.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from .. import briefpage, frontpage, pleading

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_EDGES = ("top", "left", "bottom", "right", "insideH", "insideV")
_HEADING_NAME = re.compile(r"(?i)\bheading(?:\s*(\d))?\s*$")
#: The first-page and signature options, each inferred from the filed file.
FRONT_KEYS = (
    "front_single_spaced",
    "front_attorney_gap_lines",
    "front_court_gap_lines",
    "front_caption_gap_lines",
    "caption_rule_below",
    "signature_date_beside",
    "signature_single_spaced",
    "signature_gap_lines",
)
_BODY_KEYS = ("line_spacing", "line_spacing_pt")
_HEAD_KEYS = ("heading_line_spacing", "heading_line_spacing_pt", "heading_space_before_pt", "heading_space_after_pt")
_TABLE_KEYS = ("table_font_pt", "table_border_pt", "table_header_fill", "table_total_fill")


# ---- roles and emphasis -------------------------------------------------------------
def _chain(style: Any) -> list[Any]:
    out = []
    while style is not None and len(out) < 12:
        out.append(style)
        style = style.base_style
    return out


def _outline(el: Any) -> int | None:
    ppr = el.find(f"{_W}pPr") if el is not None else None
    lvl = ppr.find(f"{_W}outlineLvl") if ppr is not None else None
    v = lvl.get(f"{_W}val") if lvl is not None else None
    return int(v) + 1 if v is not None and v.isdigit() and int(v) < 9 else None


def heading_level(p: Any) -> int:
    """The paragraph's heading level by role; 0 for a paragraph that is not one."""
    direct = _outline(p._p)
    if direct:
        return direct
    for s in _chain(p.style):
        m = _HEADING_NAME.search(s.name or "")
        lvl = _outline(s.element)
        if lvl:
            return lvl
        if m:
            return int(m.group(1)) if m.group(1) else 1
    return 0


def is_list(p: Any) -> bool:
    ppr = p._p.pPr
    numbered = ppr is not None and ppr.find(f"{_W}numPr") is not None
    return numbered or "List" in (p.style.name if p.style is not None else "")


def flag(run: Any, para: Any, attr: str) -> bool:
    """Bold or italic as Word resolves it: the run, its character style chain,
    then the paragraph's style chain (a heading's bold is usually its style's)."""
    v = getattr(run, attr)
    if v is not None:
        return bool(v)
    for s in [*_chain(run.style if run.style is not None else None), *_chain(para.style)]:
        f = getattr(s, "font", None)
        if f is not None and getattr(f, attr) is not None:
            return bool(getattr(f, attr))
    return False


# ---- the facets ---------------------------------------------------------------------
def paper_of(blob: bytes) -> str:
    """``pleading`` when a header carries a numbered-lines text box, else ``plain``."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    headers = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith("word/header"))
    for box in re.findall(r"<w:txbxContent>(.*?)</w:txbxContent>", headers, re.S):
        nums = re.findall(r"<w:t>(\d+)</w:t>", box)
        if len(nums) >= 10 and nums == [str(n) for n in range(1, len(nums) + 1)]:
            return "pleading"
    return "plain"


def _page(doc: Any) -> tuple[float, ...]:
    s = doc.sections[0]
    vals = (s.page_width, s.page_height, s.top_margin, s.right_margin, s.bottom_margin, s.left_margin)
    return tuple(round(int(v or 0) / 12700, 1) for v in vals)  # points, to a tenth


def eff(p: Any, attr: str) -> Any:
    """A paragraph-format value as Word resolves it: the paragraph's, else its style chain's."""
    v = getattr(p.paragraph_format, attr)
    for s in _chain(p.style):
        if v is not None:
            break
        v = getattr(s.paragraph_format, attr)
    return v


def _spacing(paras: list[Any], before_after: bool) -> Any:
    """The most common effective spacing among ``paras`` (None when there are none)."""
    keys = []
    for p in paras:
        rule = eff(p, "line_spacing_rule")
        line = eff(p, "line_spacing")
        k: tuple[Any, ...] = (str(rule), line)
        if before_after:
            k += (int(eff(p, "space_before") or 0), int(eff(p, "space_after") or 0))
        keys.append(k)
    return Counter(keys).most_common(1)[0][0] if keys else None


def _edges(parent: Any) -> tuple[tuple[str, str | None, str | None], ...]:
    if parent is None:
        return ()
    out = []
    for e in _EDGES:
        el = parent.find(f"{_W}{e}")
        if el is not None:
            out.append((e, el.get(f"{_W}val"), el.get(f"{_W}sz")))
    return tuple(out)


def _cell_look(c: Any) -> tuple[str, Any]:
    pr = c._tc.tcPr
    shd = pr.find(f"{_W}shd") if pr is not None else None
    fill = (shd.get(f"{_W}fill") or "").upper() if shd is not None else ""
    return fill, _edges(pr.find(f"{_W}tcBorders") if pr is not None else None)


def table_look(t: Any) -> tuple[Any, ...]:
    """A table's borders, inner rules and every cell's fill and edges."""
    pr = t._tbl.tblPr
    edges = _edges(pr.find(f"{_W}tblBorders") if pr is not None else None)
    return edges, tuple(tuple(_cell_look(c) for c in row.cells) for row in t.rows)


def front_options(doc: Any) -> dict[str, Any]:
    """The first-page and signature options the filed file is laid with."""
    out: dict[str, Any] = {}
    fronts = [p for p in briefpage.front_paragraphs(doc) if p.text.strip()]
    out["front_single_spaced"] = bool(fronts) and all(briefpage.single_ok(p) for p in fronts)
    paras, table = frontpage._front(doc)
    at = next((i for i, p in enumerate(paras) if frontpage._FOR.match(p.text)), None)
    court = frontpage._court_at(paras)
    out["front_attorney_gap_lines"] = frontpage._blanks_before(paras, at) if at is not None else 0
    out["front_court_gap_lines"] = frontpage._blanks_before(paras, court) if court is not None else 0
    out["front_caption_gap_lines"] = frontpage._blanks_before(paras, len(paras)) if table is not None else 0
    tbl = briefpage._caption_table(doc)
    rule: Any = False
    if tbl is not None:
        rule = "left" if not briefpage._check_rule(tbl, "left") else (not briefpage._check_rule(tbl, True))
    out["caption_rule_below"] = rule
    texts = [p.text for p in doc.paragraphs]
    out["signature_date_beside"] = any(briefpage._DATED.match(t) and "By:" in t for t in texts)
    lines = briefpage.signature_lines(doc)
    out["signature_single_spaced"] = bool(lines) and all(briefpage.single_ok(p) for p in lines)
    blk = briefpage._block(doc)
    gap = 0
    if blk is not None and blk[1] == -1:
        ps, _d, by = blk
        while by - 1 - gap >= 0 and not ps[by - 1 - gap].text.strip():
            gap += 1
    out["signature_gap_lines"] = gap
    return out


def anchors(doc: Any) -> dict[str, tuple[bool, str]]:
    """Per first-page key: whether the document has the line it lays, and its name."""
    paras, table = frontpage._front(doc)
    fors = any(frontpage._FOR.match(p.text) for p in paras)
    court = frontpage._court_at(paras) is not None
    sig = briefpage._block(doc) is not None
    has = {
        "front_single_spaced": (bool(briefpage.front_paragraphs(doc)), "first page"),
        "front_attorney_gap_lines": (fors, '"Attorneys for" line'),
        "front_court_gap_lines": (court, "court's name"),
        "front_caption_gap_lines": (table is not None and court, "caption under the court's name"),
        "caption_rule_below": (briefpage._caption_table(doc) is not None, "caption table"),
    }
    for k in ("signature_date_beside", "signature_single_spaced", "signature_gap_lines"):
        has[k] = (sig, "signature block")
    return has


def read(path: Path) -> dict[str, Any]:
    """The filed file's layout as named facets."""
    import docx

    blob = Path(path).read_bytes()
    doc = docx.Document(io.BytesIO(blob))
    paras = [p for p in doc.paragraphs if p.text.strip()]
    heads = [p for p in paras if heading_level(p)]
    body = [p for p in paras if not heading_level(p) and not is_list(p)]
    facets: dict[str, Any] = {
        "paper": paper_of(blob),
        "page": _page(doc),
        "headings": [(heading_level(p), " ".join(p.text.split())) for p in heads],
        "roles": [("h%d" % heading_level(p)) if heading_level(p) else ("list" if is_list(p) else "p") for p in paras],
        "tables": [table_look(t) for t in doc.tables],
        "spacing:body": _spacing(body, False),
        "spacing:heading": _spacing(heads, True),
    }
    facets.update({f"front:{k}": v for k, v in front_options(doc).items()})
    return facets


def changed_by(layout: dict[str, Any], request_text: str) -> set[str]:
    """The facets a request asks to change, so ``compare`` does not refuse them."""
    out = {f"front:{k}" for k in layout if k in FRONT_KEYS}
    if any(k in layout for k in _BODY_KEYS):
        out.add("spacing:body")
    if any(k in layout for k in _HEAD_KEYS):
        out.add("spacing:heading")
    if any(k in layout for k in _TABLE_KEYS):
        out.add("tables")
    if pleading.wants_pleading(request_text) or pleading.NOT_PLEADING.search(request_text or ""):
        out |= {"paper", "page"}
    return out


def compare(filed: dict[str, Any], ours: dict[str, Any], ignore: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Every facet of the filed layout the candidate does not carry."""
    out = []
    for k, want in filed.items():
        if k in ignore or ours.get(k) == want:
            continue
        if k == "headings":
            lost = [t for _lvl, t in want if t not in {x for _l, x in ours.get(k) or []}]
            out.append(
                f"headings: {len(want)} filed, {len(ours.get(k) or [])} rendered"
                + (f"; lost {lost[0]!r}" if lost else "; levels differ")
            )
        elif k == "tables":
            out.append("tables: a chart's borders, inner rules or fills differ from the filed brief")
        else:
            out.append(f"{k}: filed {want!r}, rendered {ours.get(k)!r}")
    return out


# ---- the carry ----------------------------------------------------------------------
#: Blank lines ``frontpage.space`` ADDS to what the renderer lays: inferred
#: as the filed count less the count a render without them lays.
_ADDED = ("front_attorney_gap_lines", "front_court_gap_lines", "front_caption_gap_lines")


def options(filed: dict[str, Any], natural: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    """The layout keys the revision renders with: the filed file's first page
    and signature (``natural`` is the same markdown rendered with none of
    them), then the request's own asks over them."""
    out: dict[str, Any] = {}
    for k in FRONT_KEYS:
        want = filed.get(f"front:{k}")
        if k in _ADDED:
            out[k] = max(0, int(want or 0) - int(natural.get(f"front:{k}") or 0))
        else:
            out[k] = want or False
    out.update(layout)
    return out


def without_front(fmt: dict[str, Any], cls: str) -> dict[str, Any]:
    """``fmt`` with none of the first-page and signature options set."""
    out = copy.deepcopy(fmt)
    lay = out.setdefault("layout", {}).setdefault(cls, {})
    for k in FRONT_KEYS:
        lay.pop(k, None)
    return out


def paper(filed: dict[str, Any], request_text: str) -> str:
    if pleading.NOT_PLEADING.search(request_text or ""):
        return "plain"
    if pleading.wants_pleading(request_text):
        return "pleading"
    return str(filed.get("paper") or "plain")


def _replace(parent: Any, tag: str, src: Any) -> None:
    for old in parent.findall(f"{_W}{tag}"):
        parent.remove(old)
    if src is not None:
        parent.append(copy.deepcopy(src))


def _carry_cell(dst: Any, src: Any, fills: bool, borders: bool) -> None:
    s_pr, d_pr = src._tc.tcPr, dst._tc.get_or_add_tcPr()
    for tag, on in (("tcBorders", borders), ("shd", fills)):
        if on:
            _replace(d_pr, tag, s_pr.find(f"{_W}{tag}") if s_pr is not None else None)


def _row_for(src: Any, i: int, n: int) -> Any:
    rows = list(src.rows)
    if i == 0 or len(rows) == n:
        return rows[min(i, len(rows) - 1)]
    if i == n - 1:
        return rows[-1]
    return rows[min(i, len(rows) - 2)] if len(rows) > 1 else rows[0]


def carry_tables(path: Path, filed_path: Path, layout: dict[str, Any]) -> None:
    """Each table of the rendered file takes the filed table's borders, inner
    rules and fills (matched by position, else by header row), except what a
    formatting item asks to change."""
    import docx

    borders = "table_border_pt" not in layout
    fills = not ({"table_header_fill", "table_total_fill"} & set(layout))
    if not (borders or fills):
        return
    ours, theirs = docx.Document(str(path)), docx.Document(str(filed_path))
    heads = {tuple(c.text.strip() for c in t.rows[0].cells): t for t in theirs.tables if t.rows}
    for i, t in enumerate(ours.tables):
        src = theirs.tables[i] if len(theirs.tables) == len(ours.tables) else None
        src = src or (heads.get(tuple(c.text.strip() for c in t.rows[0].cells)) if t.rows else None)
        if src is None or not src.rows:
            continue
        if borders:
            _replace(t._tbl.tblPr, "tblBorders", src._tbl.tblPr.find(f"{_W}tblBorders"))
        rows = list(t.rows)
        for r_i, row in enumerate(rows):
            s_row = _row_for(src, r_i, len(rows))
            s_cells = list(s_row.cells)
            for c_i, c in enumerate(row.cells):
                _carry_cell(c, s_cells[min(c_i, len(s_cells) - 1)], fills, borders)
    ours.save(str(path))
