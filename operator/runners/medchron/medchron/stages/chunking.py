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
        for k, p in enumerate(parts, 1):
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
