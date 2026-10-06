"""`summarize`: the record into a cited digest, in chunks of about 120K
characters, all chunks at once, every call streamed live. Never Batch.

Ported from the laptop's ``draft_run.py`` digest/splitfix/condense, with the
three 2026-09-24 fixes built in rather than remembered:

* The live path STREAMS. ``batch_call``'s live path did not, the SDK refused a
  64K-output non-streaming request, and every summary that day fell back to the
  Batch queue (4 to 43 minutes a round). Here there is no batch path at all.
* Chunks are ~120K characters (``levers.chunk_chars``), not 240K: dense records
  summarise to near their own length and the 240K chunks truncated three times.
  A truncated digest (no ``## FILES-SEEN`` block) is split in halves and redone
  at once, up to twice, instead of stopping for a hand-run ``splitfix``.
* Resume keys on the chunk's CONTENT: a digest is reused only when its chunk
  file's sha matches the chunk being asked for, so changed input can never
  collect the previous run's answer (the stale ``digest-07`` of that day).
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

TRUNCATION_MARK = "## FILES-SEEN"
MAX_SPLITS = 2


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def corpus_files(data: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return [
        r for r in rows if r.get("text_path") and Path(r["text_path"]).is_file() and Path(r["text_path"]).stat().st_size
    ]


def _pieces(f: dict[str, Any], chunk: int) -> list[tuple[str, str]]:
    txt = Path(f["text_path"]).read_text(encoding="utf-8", errors="replace")
    name, folder = f["name"], f.get("folder") or "/"
    if len(txt) <= chunk:
        return [(f"=== FILE: {name} (folder {folder}) ===", txt)]
    marks = [m.start() for m in re.finditer(r"\[p\.\d+\]", txt)]
    parts, start = [], 0
    while start < len(txt):
        end = start + chunk
        cut = max((m for m in marks if start < m <= end), default=None)
        cut = min(end, len(txt)) if cut is None or cut <= start else cut
        parts.append(txt[start:cut])
        start = cut
    return [(f"=== FILE: {name} [part {k}/{len(parts)}] (folder {folder}) ===", p) for k, p in enumerate(parts, 1)]


def build_chunks(files: list[dict[str, Any]], chunk: int) -> list[str]:
    chunks, cur, size = [], [], 0
    for f in files:
        for hdr, txt in _pieces(f, chunk):
            item = hdr + "\n" + txt + "\n\n"
            if size + len(item) > chunk and cur:
                chunks.append("".join(cur))
                cur, size = [], 0
            cur.append(item)
            size += len(item)
    if cur:
        chunks.append("".join(cur))
    return chunks


def split_halves(text: str) -> list[str]:
    """Two pieces at the [p.N] marker or FILE header nearest the middle."""
    cuts = [m.start() for m in re.finditer(r"\[p\.\d+\]|=== FILE:", text)]
    mid = len(text) // 2
    cut = min(cuts, key=lambda c: abs(c - mid)) if cuts else mid
    cut = cut if 0 < cut < len(text) else mid
    return [p for p in (text[:cut], text[cut:]) if p.strip()]


class Summarizer:
    def __init__(
        self,
        data: Path,
        doorway: Any,
        model: str,
        system: str,
        max_tokens: int,
        workers: int,
        log: Callable[[str], None],
    ) -> None:
        self.dd = data / "digest"
        self.dd.mkdir(parents=True, exist_ok=True)
        self.doorway, self.model, self.system = doorway, model, system
        self.max_tokens, self.workers, self.log = max_tokens, workers, log

    def _one(self, stem: str, text: str) -> tuple[str, str]:
        cp, op = self.dd / f"chunk-{stem}.txt", self.dd / f"digest-{stem}.md"
        same = cp.is_file() and sha(cp.read_text(encoding="utf-8")) == sha(text)
        if same and op.is_file():
            return stem, op.read_text(encoding="utf-8")
        tp = self.dd / f"truncated-{stem}.md"
        if same and tp.is_file():  # a resume: known truncated, so the caller splits it again for free
            tp.rename(op)
            return stem, op.read_text(encoding="utf-8")
        cp.write_text(text, encoding="utf-8")
        r = self.doorway.call(
            "digest",
            model=self.model,
            system=self.system,
            messages=[{"role": "user", "content": text}],
            max_tokens=self.max_tokens,
            stream=True,
            custom_id=f"digest-{stem}-{sha(text)[:8]}",
        )
        op.write_text(r.text, encoding="utf-8")
        self.log(f"  digest {stem}: {len(text):,} in, {len(r.text):,} out, stop {r.stop_reason}")
        return stem, r.text

    def run_round(self, todo: dict[str, str]) -> dict[str, str]:
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return dict(ex.map(lambda kv: self._one(*kv), todo.items()))

    def run(self, chunks: list[str]) -> list[str]:
        """Every chunk digested and complete, in order. Truncations split and
        redo; a chunk still truncated after MAX_SPLITS raises."""
        todo = {f"{i:02d}": c for i, c in enumerate(chunks)}
        final: dict[str, str] = {}
        for depth in range(MAX_SPLITS + 1):
            got = self.run_round(todo)
            todo = {}
            for stem, text in got.items():
                if TRUNCATION_MARK in text:
                    final[stem] = text
                    continue
                if depth == MAX_SPLITS:
                    raise RuntimeError(f"digest chunk {stem} still truncated after {MAX_SPLITS} splits")
                (self.dd / f"digest-{stem}.md").rename(self.dd / f"truncated-{stem}.md")
                src = (self.dd / f"chunk-{stem}.txt").read_text(encoding="utf-8")
                for k, part in enumerate(split_halves(src), 1):
                    todo[f"{stem}s{k}"] = part
                self.log(f"  digest {stem} truncated; split in two")
            if not todo:
                break
        return [final[k] for k in sorted(final)]


def condense(
    digests: list[str], budget: int, doorway: Any, model: str, prompt: str, workers: int, log: Callable[[str], None]
) -> str:
    """Bring the digests under ``budget`` characters; concatenation when they fit."""
    joined = "\n\n".join(digests)
    if len(joined) <= budget:
        return joined
    ratio = budget / len(joined)
    groups, cur, size = [], [], 0
    for t in digests:
        if size + len(t) > 300_000 and cur:
            groups.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t)
    if cur:
        groups.append(cur)

    def one(g: list[str]) -> str:
        body = "\n\n".join(g)
        user = f"TARGET LENGTH: about {int(len(body) * ratio):,} characters.\n\n{body}"
        r = doorway.call(
            "condense",
            model=model,
            system=prompt.replace("{{WINDOW}}", "none stated"),
            messages=[{"role": "user", "content": user}],
            max_tokens=96_000,
            stream=True,
            custom_id=f"condense-{sha(body)[:8]}",
        )
        return r.text

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(one, groups))
    log(f"  condensed {len(joined):,} to {sum(len(o) for o in out):,} characters")
    return "\n\n".join(out)
