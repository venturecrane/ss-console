"""`map`: chunk a unit's extracted text and compose the map prompt per chunk.
Paid (composition tier).

A unit is one client's chronology. Resume is by content hash (chunk-XX.sha
beside chunk-XX.txt): an existing map file is kept only when the chunk it came
from is byte-identical, so a unit whose files changed recomposes exactly the
chunks that changed. Streaming is REQUIRED: a 128k-token completion exceeds
the non-streaming ceiling, and every chunk of one early run failed on exactly
that while the script still printed MAP DONE. A refusal is NOT final (two
chunks once refused and came back clean with identical input; accepting the
first refusal would have dropped fifteen files silently). An emptied result
(end_turn with almost nothing composed) halves the chunk once; the same bytes
are never sent a third time. A pass that produced nothing exits 1.

Outputs under runs/<unit>/: chunk-XX.txt, chunk-XX.sha, map-XX.md (map-XX-k.md
for halves), usage.jsonl (per call: tokens, stop, sha, window).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .. import llm, prompts
from .base import StageRun, append_jsonl, read_json
from .chunking import build_chunks, chunk_size, sha, split_chunk

REFUSAL_ATTEMPTS = 3


def outputs_for(d: Path, i: int) -> tuple[Path, list[Path]]:
    whole = d / f"map-{i:02d}.md"
    parts = sorted(p for p in d.iterdir() if re.match(rf"map-{i:02d}-\d+\.md$", p.name))
    return whole, parts


def produced(d: Path, i: int) -> bool:
    whole, parts = outputs_for(d, i)
    if whole.is_file() and whole.stat().st_size > 100:
        return True
    return bool(parts) and all(p.stat().st_size > 100 for p in parts)


def recorded_sha(d: Path, i: int) -> str | None:
    p = d / f"chunk-{i:02d}.sha"
    if p.is_file():
        return p.read_text().strip()
    txt = d / f"chunk-{i:02d}.txt"
    return sha(txt.read_text(encoding="utf-8")) if txt.is_file() else None


def clear_outputs(d: Path, i: int) -> None:
    whole, parts = outputs_for(d, i)
    for p in [whole, *parts]:
        p.unlink(missing_ok=True)


class Chunk:
    """One unit of composition work: a whole chunk, or a half of an emptied
    one (label "3.1"). The output file name follows the label."""

    def __init__(self, i: int, text: str, part: int | None = None) -> None:
        self.i, self.part, self.text, self.attempts = i, part, text, 0

    @property
    def label(self) -> str:
        return f"{self.i}.{self.part}" if self.part else str(self.i)

    @property
    def out_name(self) -> str:
        return f"map-{self.i:02d}-{self.part}.md" if self.part else f"map-{self.i:02d}.md"

    @property
    def chunk_name(self) -> str:
        return f"chunk-{self.i:02d}-{self.part}.txt" if self.part else f"chunk-{self.i:02d}.txt"


class _Composer:
    def __init__(self, sr: StageRun, d: Path, model: str, system: str, max_tokens: int) -> None:
        self.sr, self.d, self.model, self.system, self.max_tokens = sr, d, model, system, max_tokens
        self.truncated: list[str] = []
        self.refused: list[str] = []
        self.emptied: list[str] = []

    def log_usage(self, **row: Any) -> None:
        append_jsonl(self.d / "usage.jsonl", row)

    def handle(self, c: Chunk, r: llm.Result | None, err: str | None) -> str:
        """Record one result: 'ok', 'refused', 'empty', or 'error'."""
        c.attempts += 1
        if err is not None:
            if err == "refusal":
                self.log_usage(chunk=c.label, refused=True, attempt=c.attempts)
                self.sr.log(f"chunk {c.label} refused (attempt {c.attempts})")
                return "refused"
            self.sr.log(f"chunk {c.label} attempt {c.attempts} error: {str(err)[:150]}")
            return "error"
        if r is None:
            return self.handle(c, None, "no result")
        if r.stop_reason == "refusal":
            self.log_usage(chunk=c.label, refused=True, attempt=c.attempts)
            self.sr.log(f"chunk {c.label} refused (attempt {c.attempts})")
            return "refused"
        chunk_id: Any = int(c.label) if c.part is None else c.label
        row = {
            "chunk": chunk_id,
            "in": r.usage.input_tokens,
            "out": r.usage.output_tokens,
            "stop": r.stop_reason,
            "sha": sha(c.text),
            "max_tokens": self.max_tokens,
        }
        if r.empty:
            self.log_usage(empty=True, **row)
            self.sr.log(f"chunk {c.label} EMPTY: {len(r.text)}B from {len(c.text)}B of source")
            return "empty"
        (self.d / c.out_name).write_text(r.text, encoding="utf-8")
        self.log_usage(**row)
        if r.stop_reason == "max_tokens":
            self.truncated.append(c.label)
        self.sr.log(f"chunk {c.label} {r.stop_reason} ({r.usage.input_tokens}in/{r.usage.output_tokens}out)")
        return "ok"

    def compose_one(self, c: Chunk) -> str:
        try:
            r = self.sr.doorway.call(
                "compose",
                model=self.model,
                system=self.system,
                messages=[{"role": "user", "content": c.text}],
                max_tokens=self.max_tokens,
                effort="",
                stream=True,
                cache_blocks=("system",),
                custom_id=c.out_name[:-3],
            )
        except Exception as exc:  # noqa: BLE001 - classified by handle()
            return self.handle(c, None, str(exc))
        return self.handle(c, r, None)

    def after(self, c: Chunk, state: str) -> list[Chunk]:
        """What a result means for the queue: retry, give up, or split."""
        if state == "ok":
            return []
        if state in ("refused", "error"):
            if c.attempts < REFUSAL_ATTEMPTS:
                return [c]
            if state == "refused":
                (self.d / c.out_name).write_text("## REFUSED\n", encoding="utf-8")
                self.refused.append(c.label)
                self.sr.log(f"chunk {c.label} REFUSED after {REFUSAL_ATTEMPTS} attempts")
            return []
        if c.part is not None:  # empty after the split: give up
            self.emptied.append(c.label)
            self.sr.log(f"chunk {c.label} still empty after the split; not composed")
            return []
        parts = split_chunk(c.text, 2)
        self.sr.log(f"chunk {c.i}: splitting into {len(parts)} parts ({[len(p) for p in parts]} chars)")
        subs = []
        for k, p in enumerate(parts, 1):
            sub = Chunk(c.i, p, part=k)
            (self.d / sub.chunk_name).write_text(p, encoding="utf-8")
            subs.append(sub)
        return subs

    def run_serial(self, todo: list[Chunk]) -> None:
        queue = list(todo)
        while queue:
            c = queue.pop(0)
            state = self.compose_one(c)
            nxt = self.after(c, state)
            if state in ("refused", "error") and nxt:
                time.sleep(10 * c.attempts)
            queue[0:0] = nxt
            if state == "ok":
                time.sleep(1)

    def run_batched(self, todo: list[Chunk]) -> None:
        """One batch per round for the whole unit. The id carries the content
        hash and the attempt number: the doorway resumes a persisted batch by
        custom_id, so an id that outlived its input would hand back a stale
        result instead of composing again."""
        pending = list(todo)
        while pending:
            items = [
                llm.Item(
                    custom_id=f"{c.out_name[:-3]}-{sha(c.text)[:12]}-a{c.attempts + 1}",
                    messages=[{"role": "user", "content": c.text}],
                    meta={"chunk": c},
                )
                for c in pending
            ]
            nxt: list[Chunk] = []

            def on_result(item: llm.Item, r: llm.Result | None, err: str | None) -> None:
                c = item.meta["chunk"]
                nxt.extend(self.after(c, self.handle(c, r, err)))

            s = self.sr.doorway.batch_call(
                "compose",
                items,
                on_result,
                model=self.model,
                system=self.system,
                max_tokens=self.max_tokens,
                effort="",
                cache_blocks=("system",),
                batch_dir=self.d,
            )
            if s.timed_out:
                self.sr.log(f"compose: {len(s.timed_out)} chunk(s) in a batch still processing; rerun resumes it")
                return
            pending = nxt


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    d.mkdir(parents=True, exist_ok=True)
    files = read_json(sr.slug_dir / "units" / f"{sr.unit.unit}.json", [])
    skipped = [f for f in files if not f.get("compose", True)]
    files = [f for f in files if f.get("compose", True)]
    if skipped:
        sr.log(f"compose-skipped: {len(skipped)}")
    max_tokens = int(sr.cfg.get("levers", "compose_max_tokens", 128000))
    chunk = chunk_size(max_tokens)
    system = prompts.load("map-system", sr.cfg)
    chunks = build_chunks(files, chunk)
    sr.log(f"compose window: max_tokens={max_tokens}; CHUNK={chunk:,} chars")
    sr.log(f"{sr.unit.unit}: {len(files)} files -> {len(chunks)} chunks ({[len(c) for c in chunks]} chars)")

    todo: list[Chunk] = []
    for i, ch in enumerate(chunks, 1):
        h = sha(ch)
        if produced(d, i) and recorded_sha(d, i) == h:
            continue
        if produced(d, i):
            sr.log(f"chunk {i}: input changed since its map file; recomposing")
        clear_outputs(d, i)
        (d / f"chunk-{i:02d}.txt").write_text(ch, encoding="utf-8")
        (d / f"chunk-{i:02d}.sha").write_text(h + "\n", encoding="utf-8")
        todo.append(Chunk(i, ch))
    sr.log(f"{len(chunks) - len(todo)} chunk(s) already composed, {len(todo)} to run")

    comp = _Composer(sr, d, llm.model_for(sr.cfg, "composition"), system, max_tokens)
    if "compose" in sr.doorway.batch_stages:
        comp.run_batched(todo)
    else:
        comp.run_serial(todo)

    done = [i for i in range(1, len(chunks) + 1) if produced(d, i)]
    missing = [i for i in range(1, len(chunks) + 1) if i not in done]
    sr.log(f"MAP {sr.unit.unit}: {len(done)}/{len(chunks)} chunks produced output")
    sr.log(f"TRUNCATED chunks: {comp.truncated}")
    if comp.refused:
        sr.log(f"REFUSED chunks: {comp.refused}")
    if comp.emptied:
        sr.log(f"EMPTY after split: {comp.emptied}")
    if missing:
        sr.log(f"MAP INCOMPLETE, missing chunks: {missing}")
        return 1
    sr.log(f"MAP DONE {sr.unit.unit}")
    return 0


def read_usage(d: Path) -> list[dict[str, Any]]:
    p = d / "usage.jsonl"
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
