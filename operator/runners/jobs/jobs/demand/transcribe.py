"""`transcribe`: the pages preflight could not read, by a machine, live and in
parallel, before the summary exists (2026-09-24: each unreadable bill found
after a summary re-ran it). Paid (transcription tier).

Per page, not per file: a bill whose second page is a control-character layer
costs one page, not the file. Each page is checkpointed to
``partial/<id>.jsonl`` as it returns, so a kill or a cap stop costs only the
pages not yet done, and the resume never pays for a page twice. The rendering
and the prompt are the chronology's (``stages/vision.py``): one reader, one
set of rules for an illegible token.

Two pages whose image is byte for byte the same are read once and the reading
is written to both (``repeats.same_images``). A doorway whose ``batch_stages``
names ``vision`` sends the pages through the provider's batch queue at half
the price; a page still in the queue when the wait ends stops the job
resumably, and the resume collects it from the queue unpaid.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ..limits import LimitHold
from ..stages.base import append_jsonl, read_jsonl
from ..stages.pleading_margin import strip_page
from ..stages.vision import FAILED, MAX_TOKENS, REFUSED, SYSTEM, page_item, render_page, split_handwriting_field
from . import repeats


def _todo(extracted: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[int]]]:
    out = []
    for r in extracted:
        want = r.get("transcribe")
        if not want or not r.get("source_path"):
            continue
        pages = list(range(1, int(r.get("pages") or 1) + 1)) if want == "all" else [int(p) for p in want]
        out.append((r, pages))
    return out


def _done(data: Path, rid: str) -> dict[int, str]:
    return {int(x["page"]): str(x["text"]) for x in read_jsonl(data / "partial" / f"{rid}.jsonl")}


def _merge(text: str, pages: dict[int, str], total: int) -> str:
    """Put each transcription under its own ``[p.N]`` marker, marked as a
    machine's reading. An image (no markers yet) becomes page 1. A scanned
    pleading's margin line numbers are removed here, as ``extract`` removes a
    text layer's (``stages/pleading_margin``): 2026-11-05 a served set quoted a
    scanned answer with them inside its sentences. The page prompt's
    ``HANDWRITING:`` field line is removed too (``vision.split_handwriting_field``):
    it is the chronology's page mark, never page text a drafter reads."""
    pages = {n: strip_page(split_handwriting_field(t)[1]) for n, t in pages.items()}
    if "[p." not in text:
        return "\n".join(f"[p.{n}] (machine transcription)\n{pages.get(n, FAILED)}" for n in range(1, total + 1))
    parts = re.split(r"(\[p\.\d+\]\n)", text)
    out = []
    for i, part in enumerate(parts):
        m = re.fullmatch(r"\[p\.(\d+)\]\n", part)
        if m and int(m.group(1)) in pages:
            n = int(m.group(1))
            out.append(f"[p.{n}] (machine transcription)\n{pages[n]}\n")
            if i + 1 < len(parts):
                parts[i + 1] = ""  # the unreadable layer it replaces
        else:
            out.append(part)
    return "".join(out)


def _queued(
    data: Path,
    doorway: Any,
    model: str,
    jobs: list[tuple[dict[str, Any], int, str]],
    keep: Callable[[dict[str, Any], int, str], None],
    log: Callable[[str], None],
) -> None:
    """Every page through the batch queue. A page that failed or was refused
    is marked as the live path marks it; a page still in the queue is not
    marked at all, so the stage stops and a resume collects it."""
    by_id = {}
    items = []
    for r, n, b64 in jobs:
        item = page_item(r, n, b64)
        by_id[item.custom_id] = (r, n)
        items.append(item)

    def landed(item: Any, res: Any, err: str | None) -> None:
        r, n = by_id[item.custom_id]
        if res is None:
            log(f"  {str(r['name'])[:30]} p{n}: {str(err)[:200]}")
        keep(r, n, res.text if res is not None else (REFUSED if err == "refusal" else FAILED))

    summary = doorway.batch_call(
        "vision",
        items,
        landed,
        model=model,
        system=SYSTEM,
        max_tokens=MAX_TOKENS,
        effort="",
        batch_dir=data / "batch-vision",
    )
    if summary.timed_out:
        raise RuntimeError(
            f"{len(summary.timed_out)} page(s) are still in the batch queue; a resume collects them, nothing is paid twice"
        )


def run(
    data: Path,
    doorway: Any,
    model: str,
    log: Callable[[str], None],
    concurrency: int,
) -> list[str]:
    """Transcribe every queued page. Returns the names of the documents whose
    text is now (partly) a machine transcription; raises ``LimitHold`` through
    when a limit stops it, leaving every finished page checkpointed."""
    import pymupdf

    rows = [json.loads(line) for line in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if line]
    (data / "partial").mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[dict[str, Any], int, str]] = []
    for r, pages in _todo(rows):
        have = _done(data, r["id"])
        with pymupdf.open(r["source_path"]) as doc:
            for n in pages:
                if n not in have and n <= len(doc):
                    jobs.append((r, n, render_page(doc[n - 1], log, str(r["name"]), n)))
    jobs, twins = repeats.same_images(jobs)
    repeats.save(data, "scanned", repeats.scanned_record(jobs, twins), add=True)
    same = sum(len(v) for v in twins.values())
    log(
        f"transcribe: {len(jobs)} page(s) to read"
        + (f"; {same} more carry the same image as one of them" if same else "")
    )

    def keep(r: dict[str, Any], n: int, text: str) -> None:
        """The reading, written to the page and to every page with its image."""
        for row, page in [(r, n), *twins.get((str(r["id"]), n), [])]:
            append_jsonl(data / "partial" / f"{row['id']}.jsonl", {"page": page, "text": text})

    def one(job: tuple[dict[str, Any], int, str]) -> None:
        r, n, b64 = job
        item = page_item(r, n, b64)
        try:
            res = doorway.call(
                "vision",
                model=model,
                system=SYSTEM,
                messages=item.messages,
                max_tokens=MAX_TOKENS,
                effort="",
                custom_id=item.custom_id,
            )
            text = REFUSED if res.stop_reason == "refusal" else res.text
        except LimitHold:
            raise
        except Exception as exc:  # noqa: BLE001 - a page that will not transcribe is marked, never dropped
            log(f"  {str(r['name'])[:30]} p{n}: {str(exc)[:200]}")
            text = FAILED
        keep(r, n, text)

    if "vision" in getattr(doorway, "batch_stages", ()):
        _queued(data, doorway, model, jobs, keep, log)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(one, jobs))
    machine = []
    for r, pages in _todo(rows):
        have = _done(data, r["id"])
        tp = Path(r["text_path"])
        tp.write_text(_merge(tp.read_text(encoding="utf-8"), have, int(r.get("pages") or 1)), encoding="utf-8")
        machine.append(str(r["name"]))
    (data / "transcribed.json").write_text(json.dumps(machine, indent=1), encoding="utf-8")
    return machine
