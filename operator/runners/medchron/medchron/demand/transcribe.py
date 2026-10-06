"""`transcribe`: the pages preflight could not read, by a machine, live and in
parallel, before the summary exists (2026-09-24: each unreadable bill found
after a summary re-ran it). Paid (transcription tier).

Per page, not per file: a bill whose second page is a control-character layer
costs one page, not the file. Each page is checkpointed to
``partial/<id>.jsonl`` as it returns, so a kill or a cap stop costs only the
pages not yet done, and the resume never pays for a page twice. The rendering
and the prompt are the chronology's (``stages/vision.py``): one reader, one
set of rules for an illegible token.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ..limits import LimitHold
from ..stages.base import append_jsonl, read_jsonl
from ..stages.vision import FAILED, MAX_TOKENS, REFUSED, SYSTEM, page_item, render_page


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
    machine's reading. An image (no markers yet) becomes page 1."""
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
    log(f"transcribe: {len(jobs)} page(s) to read")

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
        append_jsonl(data / "partial" / f"{r['id']}.jsonl", {"page": n, "text": text})

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
