"""Reading a Word file's layout by role: headings, emphasis, paper, the first
page, as named facets.

A heading is read by its ROLE, never one style name: a paragraph style (or
its base chain) named "... Heading" or "Heading N", or carrying an outline
level, is a heading, so SMD's styles, Word's built-ins and a firm's own
("Brief Heading") all read the same. Emphasis is read as Word resolves it:
the run, its character style, then the paragraph's style chain.

``read`` is a file's layout as facets and ``compare`` names every facet that
differs; ``prior.prove`` uses them to refuse a markdown base that prints the
words but loses the headings, the paper or the charts. The revision itself
is never re-rendered (``inplace.py``), so nothing here is carried anywhere.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from .. import briefpage, frontpage
from . import charts

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
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
    cap, chart_tables = charts.split(doc)
    facets: dict[str, Any] = {
        "paper": paper_of(blob),
        "page": _page(doc),
        "headings": [(heading_level(p), " ".join(p.text.split())) for p in heads],
        "roles": [("h%d" % heading_level(p)) if heading_level(p) else ("list" if is_list(p) else "p") for p in paras],
        "caption": charts.look(cap) if cap is not None else None,
        "tables": [charts.look(t) for t in chart_tables],
        "spacing:body": _spacing(body, False),
        "spacing:heading": _spacing(heads, True),
    }
    facets.update({f"front:{k}": v for k, v in front_options(doc).items()})
    return facets


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
            out.append(
                "tables: a chart's borders, rules, widths, margins, fills, alignment or text colour differ from "
                "the filed brief"
            )
        elif k == "caption":
            out.append("caption: the caption table's borders, widths or margins differ from the filed brief")
        else:
            out.append(f"{k}: filed {want!r}, rendered {ours.get(k)!r}")
    return out
