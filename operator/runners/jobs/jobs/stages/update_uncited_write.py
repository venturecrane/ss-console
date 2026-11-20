"""The uncited-page stage's extra writing pass (``stages/update_uncited``):
the pages the reader called a dated record, written again.

WHY ITS OWN PROMPT (``prompts/uncited-write-system.md``). The measured
failure is that the writer, with its own prompt, declines these pages again.
This pass is told what it is shown (pages the first pass wrote nothing from,
on each of which a reader found dated items), the only kinds to write (a
vaccine or injection given; a test placed, read or resulted; a visit listed
by date on a log or statement with no entry for that date and provider), and
what never to write (a prescription or medication-list row, a problem-list
row, a print, fax or as-of date, a billing amount, an appointment not kept, a
history line with no date). Each request opens with the dates and kinds the
reader found, page by page, and the date and provider lines of the entries
the chronology already holds for that document, so a charted visit is not
written twice. Its reply is the writer's own five-block shape.

The routed pages of each document are cut into requests exactly as ``map``
cuts a document (``chunking.build_chunks`` with ``windows.cut``; a routed
page whose neighbour is not routed carries that neighbour as a context block,
``select.selected_text``), and each request is a writer ``Chunk`` numbered
after the writer's own chunks, composed by the writer's own ``_Composer``.
Its map file is then one more ``map-XX.md``, so repair, assemble and
everything after it read its entries like any other.

The numbers and text shas written are recorded (``extras.json``) BEFORE any
call, so a resume keeps a chunk whose text and map are unchanged and pays for
it once, and a number whose text changed is cleared and composed again.

REPLAY ($0). Under ``ENTRY_AUDIT_REPLAY=1`` no writer call is made. Each
chunk without a map has its request written to
``runs/<unit>/uncited_read/writer/map-XX.request.json`` ({chunk, sha,
answer, model, max_tokens, system, content}); the reply is written by hand
(or by an agent) to the file its ``answer`` field names,
``map-XX.<sha12>.answer.md``, in the five-block shape the prompt asks for. The next run
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
from .chunking import build_chunks, chunk_size, pieces_pages, sha
from .update_uncited_plan import Page, Sweep

PROMPT = "uncited-write-system"

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


def texts(sr: StageRun, d: Path, sw: Sweep, found: dict[Page, Any]) -> list[str]:
    """The extra requests: each routed document's routed pages, cut as ``map``
    cuts, each opening with what the reader found on its pages (`found`:
    page -> its ``uncited_read.Answer``) and the entries already charted."""
    wd = d / DIR / "routed"
    wd.mkdir(parents=True, exist_ok=True)
    routed, docs = sorted(found), sw.docs
    files = []
    for x in sorted({x for x, _ in routed}):
        doc = docs[x]
        tp = sr.slug_dir / "text" / f"{doc.doc_id}.txt"
        pages = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
        out = wd / f"{doc.doc_id}.txt"
        out.write_text(S.selected_text(pages, sorted(p for y, p in routed if y == x)), encoding="utf-8")
        files.append({**doc.row, "text_path": str(out)})
    chunks = build_chunks(files, chunk_size(_max_tokens(sr)), cut=WN.cut) if files else []
    return [_preamble(c, sw, found) + c for c in chunks]


def _preamble(chunk: str, sw: Sweep, found: dict[Page, Any]) -> str:
    """What the reader found on this request's pages, and the entries already
    charted for its documents. Pages are named "page N", never ``[p.N]``: a
    page mark here would read as a page the request holds."""
    by_name = {doc.name: x for x, doc in sw.docs.items()}
    lines = ["WHAT THE READER FOUND ON THESE PAGES:"]
    charted = ["ENTRIES ALREADY IN THE CHRONOLOGY FOR THIS DOCUMENT (date | provider):"]
    for piece in pieces_pages(chunk):
        x = by_name.get(str(piece["file"]).strip())
        if x is None:
            continue
        for p in piece["pages"] or []:
            a = found.get((x, p))
            if a is not None:
                lines.append(f"- page {p}: dates {', '.join(a.dates)}; kinds {', '.join(a.kinds)}")
        charted += [f"- {c}" for c in sw.context.get(x, []) if f"- {c}" not in charted]
    if len(charted) == 1:
        charted.append("- none")
    return "\n".join(lines + [""] + charted) + "\n\n"


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
    system = prompts.load(PROMPT, sr.cfg)
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
