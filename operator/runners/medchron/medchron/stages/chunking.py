"""Chunk arithmetic shared by composition and repair (the frozen tree had
these split across map_run.py and repair_truncated.py, each importing the
other lazily; here the shared piece stands alone).

Output window. Thirteen ledgers put 18% of a clean run's spend on map-repair,
every dollar caused by a 32k output cap: a 240k-char chunk composes to more
than 32k tokens often enough that one matter truncated 18 times and then paid
to compose the same text again in halves. The cap is the firm's
`levers.compose_max_tokens` (default 128000, streamed), and the chunk ceiling
is derived from it rather than guessed:

    chunk_cap = max_tokens * 4 * YIELD_MARGIN / YIELD_CEILING

YIELD_CEILING is the largest output/input byte ratio a healthy part has
produced (0.85, measured on a repair tail), YIELD_MARGIN keeps 20% of the
window in hand, and 4 chars per token is the conservative English rate.

Page context. A record does not stop at a page break: a negative serology
was composed POSITIVE because its "No" sat at the foot of one page and the
result at the head of the next, and the next page was all compose saw. Where
a page's neighbour is NOT beside it in the compose input (a file split into
parts here, a page the update lane does not read), the neighbour's last or
first CONTEXT_LINES lines ride along as a delimited ``context_block``,
outside every page's own segment, so a citation still names the page the
fact is printed on (prompts/map-system.md "PAGE CONTEXT").
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

YIELD_CEILING = 0.85
YIELD_MARGIN = 0.8
CHARS_PER_TOKEN = 4
CHUNK_DEFAULT = 240_000
FILE_HDR = re.compile(r"(?m)^=== FILE: .*? ===$")
PAGE_MARK = re.compile(r"\[p\.\d+\]")
# Composed output is a fraction of source text, but not a vanishing one:
# healthy parts yield 0.28-0.85 of their source bytes; an emptied part yielded
# 0.0009. 0.02 sits an order of magnitude below every real part.
MIN_YIELD = 0.02


#: Lines of a neighbouring page carried as context, and their character cap
#: (a page with no line breaks is one line).
CONTEXT_LINES = 15
CONTEXT_CHARS = 2000


def context_block(text: str, page: int | None, before: bool) -> str:
    """The last (``before``) or first CONTEXT_LINES lines of a neighbouring
    page, delimited and labelled with that page, or '' when it has none."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    body = "\n".join(lines[-CONTEXT_LINES:] if before else lines[:CONTEXT_LINES])
    body = body[-CONTEXT_CHARS:] if before else body[:CONTEXT_CHARS]
    where = "the foot of the page before" if before else "the head of the page after"
    if page is not None:
        where += f", p.{page}"
    return f"[context, {where}: for reading only; never cite it or compose from it]\n{body}\n[end context]\n"


def _first_page(text: str) -> tuple[int | None, str]:
    """(number, text) of the first page-marked page in `text`."""
    marks = list(PAGE_MARK.finditer(text))
    if not marks:
        return None, text
    end = marks[1].start() if len(marks) > 1 else len(text)
    return int(marks[0].group()[3:-1]), text[marks[0].end() : end]


def _last_page(text: str) -> tuple[int | None, str]:
    """(number, text) of the last page-marked page in `text`."""
    marks = list(PAGE_MARK.finditer(text))
    if not marks:
        return None, text
    return int(marks[-1].group()[3:-1]), text[marks[-1].end() :]


def _with_part_context(parts: list[str]) -> list[str]:
    """Each part of a file split at page marks, with the foot of the page
    before it and the head of the page after it as context."""
    out = []
    for k, p in enumerate(parts):
        pre = post = ""
        if k > 0:
            n, text = _last_page(parts[k - 1])
            pre = context_block(text, n, before=True)
        if k + 1 < len(parts):
            n, text = _first_page(parts[k + 1])
            post = context_block(text, n, before=False)
        if post and not p.endswith("\n"):
            p += "\n"
        out.append(pre + p + post)
    return out


def chunk_size(max_tokens: int) -> int:
    cap = int(max_tokens * CHARS_PER_TOKEN * YIELD_MARGIN / YIELD_CEILING)
    return min(CHUNK_DEFAULT, cap)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunks(files: list[dict[str, Any]], chunk: int) -> list[str]:
    """~chunk chars each, split BETWEEN files where possible, else within a
    large file at [p.N] markers (parts labelled [part k/n])."""
    pieces: list[tuple[str, str]] = []
    for f in files:
        txt = Path(f["text_path"]).read_text(encoding="utf-8", errors="replace")
        name = f"{f['name']}{f.get('ext', '')}"
        if len(txt) <= chunk:
            pieces.append((f"=== FILE: {name} (fileId {f['id']}) ===", txt))
            continue
        marks = [m.start() for m in PAGE_MARK.finditer(txt)]
        parts: list[str] = []
        start = 0
        while start < len(txt):
            end = start + chunk
            cut = max((m for m in marks if start < m <= end), default=None)
            if cut is None or cut <= start:
                cut = min(end, len(txt))
            parts.append(txt[start:cut])
            start = cut
        for k, p in enumerate(_with_part_context(parts), 1):
            pieces.append((f"=== FILE: {name} [part {k}/{len(parts)}] ===", p))
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for hdr, txt in pieces:
        item = hdr + "\n" + txt + "\n\n"
        if size + len(item) > chunk and cur:
            chunks.append("".join(cur))
            cur, size = [], 0
        cur.append(item)
        size += len(item)
    if cur:
        chunks.append("".join(cur))
    return chunks


def split_chunk(text: str, parts: int = 2) -> list[str]:
    """Split on FILE headers where possible so no record is cut mid-file; a
    part that begins mid-file gets the governing header carried in, marked
    [continued], so its citations still name their file."""
    heads = [m.start() for m in FILE_HDR.finditer(text)]
    if len(heads) >= parts:
        step = len(heads) // parts
        cuts = [heads[i * step] for i in range(1, parts)]
    else:
        marks = [m.start() for m in PAGE_MARK.finditer(text)]
        if not marks:
            step = len(text) // parts
            cuts = [step * i for i in range(1, parts)]
        else:
            step = len(marks) // parts
            cuts = [marks[i * step] for i in range(1, parts)]
    out: list[str] = []
    prev = 0
    for c in cuts + [len(text)]:
        out.append(text[prev:c])
        prev = c
    out = [p for p in out if p.strip()]
    fixed: list[str] = []
    current: str | None = None
    for part in out:
        found = FILE_HDR.findall(part)
        if not part.lstrip().startswith("=== FILE:") and current:
            base = re.sub(r"\s*\[part \d+/\d+\]\s*===$", " ===", current)
            part = base.replace(" ===", " [continued] ===", 1) + "\n" + part
        if found:
            current = found[-1]
        fixed.append(part)
    return fixed
