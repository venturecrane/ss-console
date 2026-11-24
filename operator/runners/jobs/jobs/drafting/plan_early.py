"""A propounded set's caption and party plan before the file is read (design B, B0).

The party plan needs the court's caption, and on main the caption is read
after the transcription, so a request naming a party the matter does not have
stopped only after the whole file was paid for (2026-11-19: $9.43 spent before
``party_unknown``).

The caption cannot simply move earlier. Before the transcription only the
papers that already carry text are visible, so the caption stage can choose a
lesser paper than the one it would choose after (a court notice in place of a
scanned complaint), freeze that paper's caption and write corrections from it
(the 11-19 final review's falsifier). So it moves earlier only when the paper
it would read now is the paper it will read after: no document that could
still gain text outranks it (``caption.source_document``'s own order: the
complaint, then the answer, then the court notice, the latest of each).
Otherwise the job runs as on main.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import caption as caption_mod


def _key(row: dict[str, Any], kind: str) -> tuple[int, str, str]:
    return (caption_mod.RANKS[kind], caption_mod._dated(row), str(row.get("name") or ""))


def settled(data: Path) -> bool:
    """The caption paper chosen now is the one chosen after the transcription:
    every document that names a kind of court paper and ranks above it already
    carries text that is not a caption (the transcription adds text only to
    the pages that had none). False with no paper chosen now."""
    now = caption_mod.source_document(data)
    p = data / "extracted.jsonl"
    if now is None or not p.is_file():
        return False
    top = _key(now, str(now["kind"]))
    for ln in p.read_text(encoding="utf-8").splitlines():
        row = json.loads(ln) if ln else {}
        kind = caption_mod._kind(str(row.get("name") or ""))
        if not kind or _key(row, kind) <= top:
            continue
        text = row.get("text_path")
        if not text or not Path(text).is_file() or len(Path(text).read_text(errors="replace").split()) < 20:
            return False  # it may carry the caption once read
    return True
