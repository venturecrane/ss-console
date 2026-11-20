"""The uncited-page stage's extra writing pass (``stages/update_uncited``):
the pages the reader called a dated record, written by the EXISTING writer.

The routed pages of each document are cut into requests exactly as ``map``
cuts a document (``chunking.build_chunks`` with ``windows.cut``; a routed
page whose neighbour is not routed carries that neighbour as a context block,
``select.selected_text``), and each request is a writer ``Chunk`` numbered
after the writer's own chunks, composed by the writer's own ``_Composer``
with the writer's prompt (``map-system``). Its map file is then one more
``map-XX.md``, so repair, assemble and everything after it read its entries
like any other. The reader's dates are never shown to the writer.

The numbers and text shas written are recorded (``extras.json``) BEFORE any
call, so a resume keeps a chunk whose text and map are unchanged and pays for
it once, and a number whose text changed is cleared and composed again.

REPLAY ($0). Under ``ENTRY_AUDIT_REPLAY=1`` no writer call is made. Each
chunk without a map has its request written to
``runs/<unit>/uncited_read/writer/map-XX.request.json`` ({chunk, sha,
answer, model, max_tokens, system, content}); the reply is written by hand
(or by an agent) to the file its ``answer`` field names,
``map-XX.<sha12>.answer.md``, in the writer's five-block shape. The next run
takes that file as the chunk's map, records a usage row with that sha, and
goes on; a chunk whose text changed has a new sha and so a new answer name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import llm
from ..medchron import prompts, windows as WN
from ..medchron.update import select as S
from . import compose as C, compose_pool
from .base import StageRun, append_jsonl, read_json
from .chunking import build_chunks, chunk_size, sha
from .update_uncited_plan import Doc, Page

DIR = "uncited_read"
EXTRAS = "extras.json"
WRITER = "writer"
OK, INFRA, DEFECT, PENDING = "ok", "infra", "defect", "pending"


def read_extras(d: Path) -> dict[int, str]:
    """{chunk number: its text's sha} for every extra chunk this stage wrote."""
    raw = read_json(d / DIR / EXTRAS, {}) or {}
    rows = raw.get("chunks") if isinstance(raw, dict) else None
    return {int(r["i"]): str(r["sha"]) for r in rows or [] if isinstance(r, dict) and "i" in r and "sha" in r}


def _write_extras(d: Path, rows: dict[int, str]) -> None:
    p = d / DIR / EXTRAS
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"chunks": [{"i": i, "sha": h} for i, h in sorted(rows.items())]}
    p.write_text(json.dumps(body, indent=1), encoding="utf-8")


def texts(sr: StageRun, d: Path, docs: dict[int, Doc], routed: list[Page]) -> list[str]:
    """The extra requests: each routed document's routed pages, cut as ``map`` cuts."""
    wd = d / DIR / "routed"
    wd.mkdir(parents=True, exist_ok=True)
    files = []
    for x in sorted({x for x, _ in routed}):
        doc = docs[x]
        tp = sr.slug_dir / "text" / f"{doc.doc_id}.txt"
        pages = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
        out = wd / f"{doc.doc_id}.txt"
        out.write_text(S.selected_text(pages, sorted(p for y, p in routed if y == x)), encoding="utf-8")
        files.append({**doc.row, "text_path": str(out)})
    return build_chunks(files, chunk_size(_max_tokens(sr)), cut=WN.cut) if files else []


def _max_tokens(sr: StageRun) -> int:
    return int(sr.cfg.get("levers", "compose_max_tokens", 128000))


def clear(d: Path, i: int) -> None:
    """Everything extra chunk `i` left: its maps, parts, text, sha and unread rows."""
    C.clear_outputs(d, i)
    C.clear_unread(d, i)
    for p in (d / f"chunk-{i:02d}.txt", d / f"chunk-{i:02d}.sha", C.failed_marker(d, i)):
        p.unlink(missing_ok=True)


def _todo(d: Path, numbered: dict[int, str], old: dict[int, str]) -> list[C.Chunk]:
    """The extra pieces still to compose; stale extras cleared first."""
    for i in sorted(set(old) - set(numbered)):
        if C.recorded_sha(d, i) == old[i]:  # still this stage's: compose never rewrote the number
            clear(d, i)
    todo: list[C.Chunk] = []
    for i, text in numbered.items():
        h = sha(text)
        same = C.recorded_sha(d, i) == h
        if same and C.produced(d, i):
            continue
        resumed = C.queued_chunks(d, i) if same and not C.failed_marker(d, i).exists() else []
        if resumed:
            todo.extend(resumed)
            continue
        clear(d, i)
        (d / f"chunk-{i:02d}.txt").write_text(text, encoding="utf-8")
        (d / f"chunk-{i:02d}.sha").write_text(h + "\n", encoding="utf-8")
        todo.append(C.Chunk(i, text))
    return todo


def write(sr: StageRun, d: Path, extra: list[str], first: int, replay: bool) -> tuple[str, list[int]]:
    """(OK | INFRA | DEFECT | PENDING, the extra chunk numbers). Nothing to
    write clears every extra an earlier run left."""
    old = read_extras(d)
    numbered = {first + k: t for k, t in enumerate(extra)}
    # Recorded before any chunk file is written or cleared, so a run killed in
    # between never leaves an extra chunk that reads as one of the writer's.
    _write_extras(d, {**old, **{i: sha(t) for i, t in numbered.items()}})
    todo = _todo(d, numbered, old)
    _write_extras(d, {i: sha(t) for i, t in numbered.items()})
    nums = sorted(numbered)
    if not todo:
        return OK, nums
    system = prompts.load("map-system", sr.cfg)
    if replay:
        return _replay(sr, d, todo, system), nums
    models = {t: llm.model_for(sr.cfg, t) for t in ("composition", "judgment")}
    comp = C._Composer(sr, d, models, system, _max_tokens(sr))
    sr.log(f"uncited pages: writing {len(todo)} extra chunk(s) {[c.label for c in todo]} with the writer")
    if "compose" in sr.doorway.batch_stages:
        comp.run_batched(todo)
    else:
        compose_pool.run(comp, todo)
    missing = [i for i in nums if not C.produced(d, i)]
    if missing:
        sr.log(f"uncited pages: extra chunk(s) {missing} have no map" + (" (infrastructure)" if comp.infra else ""))
        return (INFRA if comp.infra else DEFECT), nums
    return OK, nums


def _replay(sr: StageRun, d: Path, todo: list[C.Chunk], system: str) -> str:
    """Take each answer on file as its chunk's map; write a request for the rest."""
    wd = d / DIR / WRITER
    wd.mkdir(parents=True, exist_ok=True)
    pending = 0
    for c in todo:
        h, stem = sha(c.text), c.out_name[:-3]
        answer = wd / f"{stem}.{h[:12]}.answer.md"
        if answer.is_file():
            (d / c.out_name).write_text(answer.read_text(encoding="utf-8"), encoding="utf-8")
            row: dict[str, Any] = {"chunk": c.i if c.part is None else c.label, "in": 0, "out": 0}
            append_jsonl(d / "usage.jsonl", {**row, "stop": "end_turn", "sha": h, "replayed": True})
            continue
        req = {
            "chunk": c.label,
            "sha": h,
            "answer": answer.name,
            "model": llm.model_for(sr.cfg, "composition"),
            "max_tokens": _max_tokens(sr),
            "system": system,
            "content": c.text,
        }
        (wd / f"{stem}.request.json").write_text(json.dumps(req, indent=1), encoding="utf-8")
        pending += 1
    if pending:
        sr.log(f"uncited pages REPLAY: {pending} writer answer(s) pending under runs/{sr.unit.unit}/{DIR}/{WRITER}/")
        return PENDING
    return OK
