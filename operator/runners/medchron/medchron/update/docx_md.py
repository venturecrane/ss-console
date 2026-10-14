"""The delivered chronology as markdown, and the proof that it IS the
delivered chronology.

An update carries every delivered entry byte-identical, so it needs the
markdown that renders to the document the firm holds. No working file of the
delivery survives on a seat (each job has its own data root), so the markdown
is rebuilt from the delivered .docx itself (`docx_to_md`), in build_doc's
section shape.

It is not trusted. `prove` renders it with THE SEAT'S renderer
(`stages/render.render_text`) and compares the result with the delivered
document paragraph by paragraph, table cells included: style, bold runs and
text. Only zero differences is a base. The one section left out of the
comparison is the Treatment Timeline: its pictures and gap bars are computed
from the entries on every render, and an update recomputes them by design.

A chronology some other renderer produced (the laptop pipeline's) will
usually fail that exact proof on style or bold runs alone: the words are the
firm's document, the formatting is another program's. Then `prove_text` is
the proof: the markdown is rebuilt WITHOUT bold markup (`docx_to_md(...,
plain=True)`; a foreign bold run is not the seat's emphasis) and the seat's
rendering of it must read the same as the delivered document paragraph by
paragraph: section and whitespace-collapsed text, table cells included, empty
paragraphs and the Treatment Timeline left out. Entries are then carried as
TEXT (the update records ``renderer: "foreign"``). A document whose WORDS the
markdown does not reproduce (a hand edit by the firm, a paragraph added) fails
both proofs and the update holds: silently reshaping a filed litigation
document is the failure this module exists to prevent.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

SECTION_HEAD = re.compile(r"^(?:\d+\.\s+)?(.+?)\s*$")
SUMMARY = "Medical Summary"
TIMELINE = "Treatment Timeline"
LIMITS = "Records Reviewed and Limitations"

Row = tuple[str, str, tuple[bool, ...], str]


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _blocks(document: Any) -> Iterator[tuple[str, Any]]:
    """Body children in order: ('p', Paragraph) or ('t', Table)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for ch in document.element.body.iterchildren():
        tag = ch.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(ch, document)
        elif tag == "tbl":
            yield "t", Table(ch, document)


def _section_of(p: Any) -> str | None:
    if p.style is None or p.style.name != "Heading 1":
        return None
    m = SECTION_HEAD.match(_flat(p.text))
    return m.group(1) if m else None


def _row(sec: str, p: Any) -> Row:
    return (
        sec,
        p.style.name if p.style is not None else "",
        tuple(bool(r.bold) for r in p.runs if r.text),
        _flat(p.text),
    )


def structure(docx_path: Path) -> list[Row]:
    """(section, style, bold pattern, text) for every paragraph a reader
    sees, table cells included, in document order; the Treatment Timeline
    section (pictures and gap bars, recomputed on every render) left out."""
    import docx

    d = docx.Document(str(docx_path))
    out: list[Row] = []
    sec = "Title"
    for kind, x in _blocks(d):
        if kind == "p":
            head = _section_of(x)
            if head is not None:
                sec = head
            if sec != TIMELINE or head is not None:
                out.append(_row(sec, x))
            continue
        for row in x.rows:
            for cell in row.cells:
                out.extend(_row(sec, p) for p in cell.paragraphs)
    return out


def summary_lines(docx_path: Path) -> list[str]:
    """The Medical Summary's non-empty lines, whitespace collapsed: what a
    reader of the entries sees, and what the update's proofs compare."""
    return [t for sec, _st, _b, t in structure(docx_path) if sec == SUMMARY and t and t != "4. Medical Summary"]


def text_rows(docx_path: Path) -> list[tuple[str, str]]:
    """(section, text) for every NON-EMPTY paragraph a reader sees, table
    cells included, Treatment Timeline left out: what `prove_text` compares.
    Style and bold runs are a renderer's; the words are the document."""
    return [(sec, t) for sec, _st, _b, t in structure(docx_path) if t]


def _runs_md(p: Any, plain: bool = False) -> str:
    """Paragraph text with bold runs as **...**, adjacent same-weight runs
    joined first (the renderer emits one run per span). `plain`: the text
    alone, for a document another renderer bolded its own way."""
    if plain:
        return "".join(r.text for r in p.runs if r.text)
    spans: list[list[Any]] = []
    for r in p.runs:
        if not r.text:
            continue
        b = bool(r.bold)
        if spans and spans[-1][0] == b:
            spans[-1][1] += r.text
        else:
            spans.append([b, r.text])
    return "".join(f"**{t}**" if b else t for b, t in spans)


def _table_md(t: Any) -> list[str]:
    return ["| " + " | ".join(c.text.strip() for c in row.cells) + " |" for row in t.rows]


def _entries_md(t: Any, subsections: set[str]) -> list[str]:
    """The entry table back to entries. The renderer prints only the left
    side of the provider line ("<provider> | <first subsection>"); the right
    side is restored from the entry's own first heading, so the lane reads the
    same and the entry renders the same."""
    out = []
    for row in t.rows:
        left, right = row.cells[0], row.cells[1]
        lp = [p.text for p in left.paragraphs]
        body = [p.text.strip() for p in right.paragraphs]
        head = next((b for b in body if b in subsections), "")
        who = lp[1].strip() if len(lp) > 1 else ""
        lines = [lp[0].strip(), f"{who} | {head}" if head else who, "", *body]
        while lines and lines[-1] == "":
            lines.pop()
        out.append("\n".join(lines))
    return out


def _preamble_md(paras: list[tuple[str, str]]) -> list[str]:
    """Preamble paragraphs as markdown blocks. A Heading 2 and the paragraph
    under it are ONE block (the renderer makes a short unpunctuated first line
    a heading and the rest its paragraph), so a paragraph holding a line break
    after a heading is not mistaken for a second heading."""
    blocks: list[str] = []
    for style, txt in paras:
        if blocks and blocks[-1].endswith("\x00") and style != "Heading 2":
            blocks[-1] = blocks[-1][:-1] + "\n" + txt
            continue
        blocks.append(txt + ("\x00" if style == "Heading 2" else ""))
    return [b.rstrip("\x00") for b in blocks]


def docx_to_md(docx_path: Path, subsections: set[str], plain: bool = False) -> str:
    """Rebuild build_doc's markdown from a chronology the seat renderer made
    (or, `plain`, from one another renderer made: no bold markup)."""
    import docx

    d = docx.Document(str(docx_path))
    title, limits, prov, icd, pre, entries, exhibits = "", [], [], [], [], [], []
    sec = None
    for kind, x in _blocks(d):
        if kind == "p":
            st = x.style.name if x.style is not None else ""
            if st == "Title":
                title = x.text.strip()
            elif _section_of(x) is not None:
                sec = _section_of(x)
            elif sec == LIMITS and x.text.strip():
                limits.append(("* " if st == "List Bullet" else "") + _runs_md(x, plain))
            elif sec == SUMMARY and x.text.strip():
                pre.append((st, _runs_md(x, plain)))
            continue
        if sec == "Past Treatment History":
            prov = _table_md(x)
        elif sec == "Diagnostic Highlights":
            icd = _table_md(x)
        elif sec == "Exhibit List":
            exhibits = _table_md(x)
        elif sec == SUMMARY:
            entries = _entries_md(x, subsections)
    o = [f"{title}\n", "## Treatment Timeline\n", *prov, "", "## Diagnostic Highlights\n", *icd, ""]
    o.append("## Medical Chronology\n")
    o.extend(b + "\n" for b in _preamble_md(pre))
    o.extend(e + "\n" for e in entries)
    o.extend(["## Exhibit List\n", *exhibits])
    if limits:
        o.extend(["", "## Records Reviewed and Limitations\n", *limits])
    return "\n".join(o)


def diff(a: list[Any], b: list[Any]) -> list[tuple[int, Any, Any]]:
    """Positional diff: [(index, delivered, rendered)]."""
    out = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            out.append((i, x, y))
    return out


def prove(md_text: str, delivered: Path, render: Callable[[str, Path], int], workdir: Path | None = None) -> dict:
    """{'rows', 'diffs', 'rendered'} for `md_text` rendered now against the
    delivered document. `render(text, dst)` is the seat renderer bound to the
    job (returns its exit code)."""
    wd = Path(workdir or tempfile.mkdtemp(prefix="update-prove-"))
    wd.mkdir(parents=True, exist_ok=True)
    out = wd / "rendered.docx"
    if render(md_text, out) != 0 or not out.is_file():
        return {"rows": 0, "diffs": [(0, "render", "the renderer refused the rebuilt markdown")], "rendered": None}
    a, b = structure(delivered), structure(out)
    return {"rows": len(a), "diffs": diff(a, b), "rendered": out}


def prove_text(md_text: str, delivered: Path, render: Callable[[str, Path], int], workdir: Path | None = None) -> dict:
    """`prove` on the words only: {'rows', 'diffs', 'rendered'}, a diff being
    a (section, collapsed text) paragraph the delivered document and the
    seat's rendering of `md_text` do not share in the same position."""
    wd = Path(workdir or tempfile.mkdtemp(prefix="update-prove-text-"))
    wd.mkdir(parents=True, exist_ok=True)
    out = wd / "rendered.docx"
    if render(md_text, out) != 0 or not out.is_file():
        return {"rows": 0, "diffs": [(0, "render", "the renderer refused the rebuilt markdown")], "rendered": None}
    a, b = text_rows(delivered), text_rows(out)
    return {"rows": len(a), "diffs": diff(a, b), "rendered": out}
