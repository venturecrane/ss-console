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
are never sent a third time.

A record the reader declines is decided by rule, never a stop (2026-10-24: a
SANE exam or a self-harm note is a legitimate record, and a refusal on it
failed the whole job). The ladder: REFUSAL_ATTEMPTS on the composition tier,
then JUDGMENT_ATTEMPTS on the judgment tier, then each file of the piece
alone (the same ladder again), then a single file in page halves, down to
one page (PAGE_HALF_ATTEMPTS on the composition tier, then the judgment
tier), so one declined page never costs its neighbours their entries. A
single page still declined, or still empty, is a page nothing read: it is
recorded in ``unread_pages.json`` and named to the firm in Records Reviewed
and Limitations, and the piece's map carries that account in its FILES-SEEN
line, so the coverage gate reads the file as explained. A record the reader
did read is never put there.

An ERROR is different: a persistent API error is infrastructure, not the
record, so it is never disclosed as unread and never ships a hole. The chunk
is marked failed (``chunk-XX.failed``) and the map exits INFRA_EXIT (75,
stable: the daemon tells "come back later" from a defect by it), as does a
batch still processing; a rerun composes the chunk again rather than taking a
surviving sibling part for the whole. Exit 1 is left for a chunk with no
output and no recorded reason, which only a defect produces.

Outputs under runs/<unit>/: chunk-XX.txt, chunk-XX.sha, map-XX.md (map-XX-k.md
for parts), usage.jsonl (per call: tokens, stop, sha, window), and when a page
went unread, unread_pages.json.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .. import llm, prompts
from ..limits import LimitHold
from .base import StageRun, append_jsonl, read_json
from .chunking import build_chunks, chunk_size, pieces_pages, sha, split_chunk, split_files

# The five blocks the prompt demands (prompts/map-system.md "OUTPUT SHAPE").
BLOCKS = ("## ENTRIES", "## INDEX", "## BILLING-DATES", "## CONFLICTS / REFERENCED-BUT-ABSENT", "## FILES-SEEN")


def well_formed(text: str) -> bool:
    """A map in the house shape, however short: every block heading present."""
    return all(b in text for b in BLOCKS)


REFUSAL_ATTEMPTS = 3
#: Attempts on the judgment tier after the composition tier declined.
JUDGMENT_ATTEMPTS = 1
#: Composition-tier attempts on a page half: its whole file was already
#: refused REFUSAL_ATTEMPTS times, so a half gets one try before the judgment
#: tier, which bounds the cost of finding the declined pages.
PAGE_HALF_ATTEMPTS = 1
#: Exit code for infrastructure (a persistent API error, a batch still
#: processing): nothing was disclosed, and a later rerun is the remedy. Stable;
#: dag.py maps it and the daemon reads it (sysexits EX_TEMPFAIL).
INFRA_EXIT = 75
#: Pages nothing read, per unit: [{chunk, file, file_id, pages, reason}].
UNREAD = "unread_pages.json"
#: The reason codes, and the account each writes into the map's FILES-SEEN.
UNREAD_REASONS = {
    "refused": "declined by the reader on every attempt",
    "empty": "the reader returned no account of these pages",
    "truncated": "too dense to read within the output limit even one page at a time",
}


def page_count(text: str) -> int:
    """Pages a single-file piece carries (its context blocks excluded)."""
    return sum(len(p["pages"] or []) for p in pieces_pages(text))


def narrower(text: str) -> list[str]:
    """The next smaller pieces of a piece no reader would take: its files one
    at a time, else (a single file) its page halves, else nothing (one page,
    or text without page marks, is the floor)."""
    files = split_files(text)
    if len(files) > 1:
        return files
    if page_count(text) > 1:
        halves = split_chunk(text, 2)
        if len(halves) > 1:
            return halves
    return []


def outputs_for(d: Path, i: int) -> tuple[Path, list[Path]]:
    whole = d / f"map-{i:02d}.md"
    parts = sorted(p for p in d.iterdir() if re.match(rf"map-{i:02d}-\d+\.md$", p.name))
    return whole, parts


def failed_marker(d: Path, i: int) -> Path:
    return d / f"chunk-{i:02d}.failed"


def produced(d: Path, i: int) -> bool:
    if failed_marker(d, i).exists():
        return False  # a part errored: the parts that composed are not the whole
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


def clear_unread(d: Path, i: int) -> None:
    """Drop chunk `i`'s unread rows: it is about to be composed again."""
    p = d / UNREAD
    rows = read_json(p, [])
    keep = [r for r in rows if str(r.get("chunk")).split(".", 1)[0] != str(i)]
    if len(keep) == len(rows):
        return
    if keep:
        p.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    else:
        p.unlink()


def record_unread(d: Path, label: str, text: str, reason: str, out_name: str) -> list[dict[str, Any]]:
    """Name the pages of `text` as unread and write the map file that carries
    their account: every block in the house shape, no entry, and one
    FILES-SEEN line per file reading `nothing extractable: not read (...)`,
    so assemble parses it and the coverage gate finds the file explained."""
    why = UNREAD_REASONS[reason]
    pieces = pieces_pages(text)
    rows = [{"chunk": label, **{k: v for k, v in p.items() if k != "header"}, "reason": reason} for p in pieces]
    seen = [
        f"{p['header']} | nothing extractable: not read ({why}); named as unread in the limitations" for p in pieces
    ]
    none = "none in this chunk"
    (d / out_name).write_text(
        f"## ENTRIES\n{none}\n\n## INDEX\n{none}\n\n## BILLING-DATES\n{none}\n\n"
        f"## CONFLICTS / REFERENCED-BUT-ABSENT\n{none}\n\n## FILES-SEEN\n" + "\n".join(seen) + "\n",
        encoding="utf-8",
    )
    p = d / UNREAD
    kept = [r for r in read_json(p, []) if str(r.get("chunk")) != label]
    p.write_text(json.dumps(kept + rows, indent=1), encoding="utf-8")
    return rows


class Chunk:
    """One unit of composition work: a whole chunk, a half of an emptied one,
    or one file or page half of a declined one (label "3.1"). The output file
    name follows the label; `tier` is the model tier it is on, `attempts`
    counts the calls on that tier, and `budget` is its composition attempts."""

    def __init__(self, i: int, text: str, part: int | None = None, budget: int = REFUSAL_ATTEMPTS) -> None:
        self.i, self.part, self.text, self.attempts = i, part, text, 0
        self.tier = "composition"
        self.budget = budget

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
    def __init__(self, sr: StageRun, d: Path, models: dict[str, str], system: str, max_tokens: int) -> None:
        self.sr, self.d, self.models, self.system, self.max_tokens = sr, d, models, system, max_tokens
        self.truncated: list[str] = []
        self.refused: list[str] = []
        self.emptied: list[str] = []
        self.next_part: dict[int, int] = {}
        #: An API error outlived its retries, or a batch is still processing.
        self.infra = False

    def log_usage(self, **row: Any) -> None:
        append_jsonl(self.d / "usage.jsonl", row)

    def handle(self, c: Chunk, r: llm.Result | None, err: str | None) -> str:
        """Record one result: 'ok', 'refused', 'empty', or 'error'."""
        c.attempts += 1
        if err is not None:
            if err == "refusal":
                self.log_usage(chunk=c.label, refused=True, attempt=c.attempts, tier=c.tier)
                self.sr.log(f"chunk {c.label} refused (attempt {c.attempts}, {c.tier} tier)")
                return "refused"
            self.sr.log(f"chunk {c.label} attempt {c.attempts} error: {str(err)[:150]}")
            return "error"
        if r is None:
            return self.handle(c, None, "no result")
        if r.stop_reason == "refusal":
            self.log_usage(chunk=c.label, refused=True, attempt=c.attempts, tier=c.tier)
            self.sr.log(f"chunk {c.label} refused (attempt {c.attempts}, {c.tier} tier)")
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
        if r.empty and not (c.part is not None and well_formed(r.text)):
            self.log_usage(empty=True, **row)
            self.sr.log(f"chunk {c.label} EMPTY: {len(r.text)}B from {len(c.text)}B of source")
            return "empty"
        if r.empty:
            # A half that is short because its source holds nothing to extract
            # (a 78-page property inspection report, live 2026-09-16) still
            # answered in the house shape, and that answer is the only account
            # downstream will ever have of the file. Dropping it left the file
            # unexplained at the coverage gate. Short and complete is kept.
            self.sr.log(f"chunk {c.label} short ({len(r.text)}B from {len(c.text)}B) but complete; kept")
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
                model=self.models[c.tier],
                system=self.system,
                messages=[{"role": "user", "content": c.text}],
                max_tokens=self.max_tokens,
                effort="high",
                stream=True,
                cache_blocks=("system",),
                custom_id=c.out_name[:-3],
            )
        except LimitHold:
            raise  # a limit stops the stage and reaches the driver as a hold (N10)
        except Exception as exc:  # noqa: BLE001 - classified by handle()
            return self.handle(c, None, str(exc))
        return self.handle(c, r, None)

    def _subs(self, c: Chunk, parts: list[str], budget: int = REFUSAL_ATTEMPTS) -> list[Chunk]:
        subs = []
        for p in parts:
            self.next_part[c.i] = self.next_part.get(c.i, 0) + 1
            sub = Chunk(c.i, p, part=self.next_part[c.i], budget=budget)
            (self.d / sub.chunk_name).write_text(p, encoding="utf-8")
            subs.append(sub)
        return subs

    def _give_up(self, c: Chunk, reason: str) -> list[Chunk]:
        """A piece holding several files is sent one file at a time; a single
        file in page halves; one page is the floor, and it is named unread."""
        files = split_files(c.text)
        if len(files) > 1:
            self.sr.log(f"chunk {c.label}: sending its {len(files)} files one at a time")
            return self._subs(c, files)
        halves = narrower(c.text)
        if halves:
            self.sr.log(f"chunk {c.label}: {reason} as one file; sending it in page halves")
            return self._subs(c, halves, budget=PAGE_HALF_ATTEMPTS)
        rows = record_unread(self.d, c.label, c.text, reason, c.out_name)
        (self.refused if reason == "refused" else self.emptied).append(c.label)
        named = "; ".join(f"{r['file']} pp. {r['pages'] or 'all'}" for r in rows)
        self.sr.log(f"chunk {c.label} {reason} on every attempt; named as unread: {named}")
        return []

    def after(self, c: Chunk, state: str) -> list[Chunk]:
        """What a result means for the queue: retry, move to the judgment
        tier, split, or (a single file still declined) name its pages unread."""
        if state == "ok":
            return []
        if state == "error":
            if c.attempts < REFUSAL_ATTEMPTS:
                return [c]
            failed_marker(self.d, c.i).write_text(c.label + "\n", encoding="utf-8")
            self.infra = True
            self.sr.log(f"chunk {c.label} errored on {REFUSAL_ATTEMPTS} attempts; chunk {c.i} is incomplete")
            return []
        if state == "refused":
            if c.tier == "composition" and c.attempts < c.budget:
                return [c]
            if c.tier == "composition":
                c.tier, c.attempts = "judgment", 0
                self.sr.log(f"chunk {c.label} refused on the composition tier; asking the judgment tier")
                return [c]
            if c.attempts < JUDGMENT_ATTEMPTS:
                return [c]
            return self._give_up(c, "refused")
        if c.part is not None:  # empty after a split
            return self._give_up(c, "empty")
        parts = split_chunk(c.text, 2)
        self.sr.log(f"chunk {c.i}: splitting into {len(parts)} parts ({[len(p) for p in parts]} chars)")
        return self._subs(c, parts)

    def run_serial(self, todo: list[Chunk]) -> None:
        queue = list(todo)
        while queue:
            c = queue.pop(0)
            state = self.compose_one(c)
            nxt = self.after(c, state)
            if state in ("refused", "error") and nxt and nxt[0] is c:
                time.sleep(10 * max(c.attempts, 1))
            queue[0:0] = nxt
            if state == "ok":
                time.sleep(1)

    def run_batched(self, todo: list[Chunk]) -> None:
        """One batch per tier per round for the whole unit. The id carries the
        content hash, the tier and the attempt number: the doorway resumes a
        persisted batch by custom_id, so an id that outlived its input would
        hand back a stale result instead of composing again."""
        pending = list(todo)
        while pending:
            nxt: list[Chunk] = []

            def on_result(item: llm.Item, r: llm.Result | None, err: str | None) -> None:
                c = item.meta["chunk"]
                nxt.extend(self.after(c, self.handle(c, r, err)))

            for tier in ("composition", "judgment"):
                batch = [c for c in pending if c.tier == tier]
                if not batch:
                    continue
                tag = "" if tier == "composition" else "-j"
                items = [
                    llm.Item(
                        custom_id=f"{c.out_name[:-3]}-{sha(c.text)[:12]}{tag}-a{c.attempts + 1}",
                        messages=[{"role": "user", "content": c.text}],
                        meta={"chunk": c},
                    )
                    for c in batch
                ]
                s = self.sr.doorway.batch_call(
                    "compose",
                    items,
                    on_result,
                    model=self.models[tier],
                    system=self.system,
                    max_tokens=self.max_tokens,
                    effort="high",
                    cache_blocks=("system",),
                    batch_dir=self.d,
                )
                if s.timed_out:
                    self.infra = True
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
        clear_unread(d, i)
        failed_marker(d, i).unlink(missing_ok=True)
        (d / f"chunk-{i:02d}.txt").write_text(ch, encoding="utf-8")
        (d / f"chunk-{i:02d}.sha").write_text(h + "\n", encoding="utf-8")
        todo.append(Chunk(i, ch))
    sr.log(f"{len(chunks) - len(todo)} chunk(s) already composed, {len(todo)} to run")

    models = {t: llm.model_for(sr.cfg, t) for t in ("composition", "judgment")}
    comp = _Composer(sr, d, models, system, max_tokens)
    if "compose" in sr.doorway.batch_stages:
        comp.run_batched(todo)
    else:
        comp.run_serial(todo)

    done = [i for i in range(1, len(chunks) + 1) if produced(d, i)]
    missing = [i for i in range(1, len(chunks) + 1) if i not in done]
    sr.log(f"MAP {sr.unit.unit}: {len(done)}/{len(chunks)} chunks produced output")
    sr.log(f"TRUNCATED chunks: {comp.truncated}")
    if comp.refused:
        sr.log(f"REFUSED on every attempt, pages named as unread: {comp.refused}")
    if comp.emptied:
        sr.log(f"EMPTY after every split, pages named as unread: {comp.emptied}")
    if missing and comp.infra:
        sr.log(f"MAP INCOMPLETE (infrastructure, nothing disclosed; a rerun composes them): {missing}")
        return INFRA_EXIT
    if missing:
        sr.log(f"MAP INCOMPLETE, missing chunks with no recorded reason: {missing}")
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
