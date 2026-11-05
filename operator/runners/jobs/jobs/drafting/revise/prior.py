"""The brief as it is filed now, as markdown the job can edit, proven.

THE FILED VERSION WINS. The files are downloaded from the matter by id (the
broker named the ids from the delivery proof, never the model), and their
sha256 decides the base: a brief edited and versioned in place since the job
delivered it (the 2026-11-04 hand revision of 200045 at 04:53) is a newer
document than any working file the job kept.

The markdown is never trusted on its say-so. A candidate is a base only when
the seat's own renderer, given it, prints the filed document back: the same
paragraphs in the same order, table cells included, with the same bold and
italic as Word resolves it (a heading's bold is usually its style's), AND the
same layout: the same headings at the same levels, paper, page, charts and
first page (``layout.compare``). A base that prints the words but loses the
headings or the pleading paper is not a proof (``prove``). Candidates, in
order:

1. the prior job's own ``draft-final.md`` with its render-time settlements
   applied (``render.reserve_judgment``), when the job dir still holds it;
2. the filed Word file read back into markdown (``docx_to_md``).

Neither proving is ``revise_base_unproven``: the job stops for SMD (failed,
no client message), because reshaping a filed litigation document from text
that does not print it is the failure this module exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

from ..outcome import DraftingFailed
from . import layout

Render = Callable[[str, Path], Path]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _blocks(document: Any) -> Iterator[tuple[str, Any]]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for ch in document.element.body.iterchildren():
        tag = ch.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(ch, document)
        elif tag == "tbl":
            yield "t", Table(ch, document)


def _segments(p: Any) -> list[tuple[str, bool, bool]]:
    """Runs merged where emphasis does not change: (text, bold, italic)."""
    out: list[tuple[str, bool, bool]] = []
    for r in p.runs:
        if not r.text:
            continue
        key = (layout.flag(r, p, "bold"), layout.flag(r, p, "italic"))
        if out and (out[-1][1], out[-1][2]) == key:
            out[-1] = (out[-1][0] + r.text, *key)
        else:
            out.append((r.text, *key))
    return out


def _runs_md(p: Any) -> str:
    parts = []
    for text, bold, italic in _segments(p):
        core, lead, trail = text.strip(), text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
        if not core:
            parts.append(text)
            continue
        mark = "***" if bold and italic else "**" if bold else "*" if italic else ""
        parts.append(f"{lead}{mark}{core}{mark}{trail}")
    return "".join(parts)


def docx_to_md(path: Path) -> str:
    """The drafting renderer's markdown subset, read back off a Word file."""
    import docx

    d, out = docx.Document(str(path)), []
    for kind, b in _blocks(d):
        if kind == "t":
            rows = [[" ".join(c.text.split()) for c in row.cells] for row in b.rows]
            if not rows:
                continue
            lines = ["| " + " | ".join(r) + " |" for r in rows]
            head_bold = all(s[1] for c in b.rows[0].cells for p in c.paragraphs for s in _segments(p))
            if head_bold and len(lines) > 1:
                lines.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
            out.append("\n".join(lines))
            continue
        if not b.text.strip():
            continue
        style = b.style.name if b.style is not None else ""
        level = layout.heading_level(b)
        if level:
            out.append("#" * min(level, 6) + " " + " ".join(b.text.split()))
        elif style == "List Bullet":
            out.append("- " + _runs_md(b).strip())
        else:
            out.append(_runs_md(b).strip())
    return "\n\n".join(out) + "\n"


def printed(path: Path) -> list[tuple[str, tuple[tuple[str, bool, bool], ...]]]:
    """What a reader sees, in order: each paragraph's words and emphasis, and
    each table row's cells as words."""
    import docx

    d, out = docx.Document(str(path)), []
    for kind, b in _blocks(d):
        if kind == "t":
            for row in b.rows:
                out.append(("| " + " | ".join(" ".join(c.text.split()) for c in row.cells) + " |", ()))
        elif b.text.strip():
            segs = tuple((" ".join(t.split()), bo, it) for t, bo, it in _segments(b) if t.strip())
            out.append((" ".join(b.text.split()), segs))
    return out


def prove(md: str, filed: Path, render: Render, workdir: Path | None = None) -> list[str]:
    """Where the rendering of ``md`` differs from the filed document, in words,
    emphasis or layout; empty is a proof."""
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        cand = render(md, Path(tmp) / "candidate.docx")
        ours, looks = printed(cand), layout.read(cand)
    theirs = printed(filed)
    diffs: list[str] = layout.compare(layout.read(filed), looks)[:5]
    for i, (a, b) in enumerate(zip(ours, theirs)):
        if a[0] != b[0]:
            diffs.append(f"paragraph {i + 1} reads differently")
        elif a[1] != b[1] and b[1]:
            diffs.append(f"paragraph {i + 1} carries different bold or italic runs")
        if len(diffs) >= 8:
            break
    if len(ours) != len(theirs):
        diffs.append(f"{len(ours)} paragraphs rendered, {len(theirs)} filed")
    return diffs


def resolve(
    filed_draft: Path,
    candidates: list[tuple[str, str]],
    render: Render,
    out: Path,
) -> dict[str, Any]:
    """The first candidate (name, markdown) that prints the filed draft, plus
    the filed docx read back as the last candidate. Writes ``base.md`` and
    ``base.json`` under ``out``; raises ``revise_base_unproven``."""
    tried: list[str] = []
    for name, md in [*candidates, ("filed", docx_to_md(filed_draft))]:
        diffs = prove(md, filed_draft, render, out)
        if not diffs:
            (out / "base.md").write_text(md, encoding="utf-8")
            rec = {"source": name, "filed_sha256": sha(filed_draft), "tried": tried}
            (out / "base.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
            return rec
        tried.append(f"{name}: " + "; ".join(diffs[:3]))
    raise DraftingFailed(
        "revise_base_unproven: no markdown the seat's renderer prints back as the filed brief was found ("
        + " | ".join(tried)[:360]
        + "); nothing was edited or filed"
    )
