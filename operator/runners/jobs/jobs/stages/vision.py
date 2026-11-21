"""`vision`: per-page transcription of the scan queue, with structural
provenance. Paid (transcription tier).

Every page is one independent request, checkpointed to `partial/<id>.jsonl`
as it completes so a kill costs only the pages not yet done, and gathered
across every queued file so one matter is one batch (or a few, bounded by
rendered bytes) when the levers name the stage. Page markers are real page
numbers, illegible content is marked never guessed, and a page that cannot be
transcribed is recorded as such rather than silently dropped.

Every page's reply carries a REQUIRED field, ``HANDWRITING: yes|no``; a page
with any handwritten value is stored opening with ``[handwritten]``
(``update/handwritten.MARK``), and a reply missing the field is treated as
handwritten. A date or vital read off handwriting is a reading: the typed
statements of the same provider settle a date, and the audit re-reads the
image for every value.

An update writes ``vision_skip.json`` ({document id: [page, ...]}) before
this runs (``stages/update_pages``): pages whose picture is a page already
read. Each is recorded as SKIPPED without a request: a re-filed scan is never
paid for twice.

One change from the frozen script, the 2026-08-29 review's item 12: a file
left incomplete (a batch still processing, a page that never returned) is an
exit 1, not a printed line under "VISION DONE". The driver records the stage
as failed with that reason and the next run resumes the same batch.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

from .. import llm, stop_request, unopened
from ..matter import paid as matter_paid, policy as matter_policy, store as page_store_mod
from ..medchron.audit.run import run_pool
from ..medchron.update.pageid import SKIPPED
from . import vision_seed
from .base import StageRun, append_jsonl, read_jsonl
from .transcript import UNREAD, merge_pages, merged, recorded_ids, transcript_state

PAGE_TIMEOUT = 180.0
MAX_TOKENS = 8000
GROUP_B64_BUDGET = 400_000_000  # rendered pages held in memory per submission
IMAGE_B64_LIMIT = 4_500_000  # the API judges the encoded size
#: Pages read at once when the stage is not batched (2026-11-19: one at a
#: time, page reading spent 144 minutes of a 3,821-page matter).
VISION_WORKERS = 8
#: The chronology's transcription prompt (its row in ``matter/policy``).
SYSTEM = matter_policy.RECORD_SYSTEM
USER_TEXT = matter_policy.RECORD_USER_TEXT
FAILED = "[page transcription failed]"
REFUSED = "[page transcription refused]"
HANDWRITING = re.compile(r"^\s*HANDWRITING:\s*(yes|no)\s*$", re.I | re.M)


def split_handwriting_field(text: str) -> tuple[str | None, str]:
    """(``yes``/``no`` or None when the reply carries no field, the reply with
    the field line removed). The one reading of the field: the chronology
    turns it into its page mark (:func:`handwriting_field`), and the demand
    and drafting transcription only removes it (``demand/transcribe.py``), so
    no drafter reads a system line as page text."""
    m = HANDWRITING.search(text[:200]) if text else None
    if m is None:
        return None, text
    return m.group(1).lower(), (text[: m.start()] + text[m.end() :]).strip("\n")


def handwriting_field(text: str) -> str:
    """The transcription with its required HANDWRITING field turned into the
    page mark downstream reads (``update/handwritten.MARK`` first, or
    nothing). A reply WITHOUT the field is marked handwritten: the field is
    required, a model that drops it was not asked an optional question, and
    an unmarked handwritten page is the defect this exists for (a flowsheet
    whose blood pressure read 102/102 for 142/102, 2026-10-13)."""
    if text in (FAILED, REFUSED):
        return text
    answer, body = split_handwriting_field(text)
    return body if answer == "no" else "[handwritten]\n" + body


def load_partial(path: Path) -> dict[int, str]:
    done: dict[int, str] = {}
    for r in read_jsonl(path):
        try:
            done[int(r["page"])] = r["text"]
        except (KeyError, TypeError, ValueError):
            continue
    return done


def load_skips(slug_dir: Path) -> dict[str, set[int]]:
    """{document id: pages} the update lane found already read by their
    picture (``stages/update_pages``); empty when no update wrote one."""
    p = slug_dir / "vision_skip.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[int]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = {int(x) for x in v if isinstance(x, int) and not isinstance(x, bool)}
    return out


def render_page(page: Any, log, name: str, pno: int) -> str:
    """Base64 PNG of one page, stepped down until it fits the API limit.

    The predictor is rendered pixel area, not source bytes: a phone photo
    opened as a document gets upscaled by its DPI metadata (1448x2573 became
    2263x4021, 17 MB of base64 against a 5 MB limit), and a photograph's PNG
    entropy can encode a 2000px render past the limit anyway. So cap the
    dimensions, then check the ENCODED size and step down until it fits."""
    rect = page.rect
    base = 150 * max(rect.width, rect.height) / 72.0
    b64 = ""
    for cap in (2000.0, 1500.0, 1100.0, 800.0):
        dpi = int(150 * min(1.0, cap / base)) if base > 0 else 150
        pix = page.get_pixmap(dpi=max(dpi, 30))
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        if len(b64) <= IMAGE_B64_LIMIT:
            break
        log(f"  {name[:30]} p{pno}: render at cap {cap:.0f} encodes to {len(b64) / 1e6:.1f} MB, stepping down")
    return b64


def page_item(rec: dict[str, Any], pno: int, b64: str) -> llm.Item:
    return llm.Item(
        custom_id=f"{rec['id']}-p{pno}",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": USER_TEXT},
                ],
            }
        ],
        meta={"id": rec["id"], "page": pno, "name": rec["name"]},
    )


def _write_transcript(out: Path, body: str) -> None:
    """Atomic, the way state.py writes. A kill during a bare ``write_text``
    leaves a truncated file, and a truncated file reads as a completed one to
    anything that only asks whether it exists."""
    tmp = out.with_suffix(".txt.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out)


class PageStore:
    """The store of paid page text (``matter/store``) for this stage's pages:
    a page this chronology's method already read for a file with the same
    sha256 (on this matter or another) is served from it, never paid again;
    a fresh reading is kept. The raw reply is stored, and the page's
    handwriting mark is made from it exactly as from a fresh one."""

    def __init__(self, sr: StageRun, model: str, shas: dict[str, str], src: dict[str, str]) -> None:
        job_file = getattr(sr.job, "path", None)
        record = Path(job_file).parent / page_store_mod.RECORD if job_file else None
        self.store = page_store_mod.from_env("medchron", log=sr.log, record_to=record)
        self.method = matter_paid.method(matter_policy.CHRONOLOGY, model).version
        self.matter = str(getattr(sr.job, "matter_id", "") or "")
        self._shas, self._src = dict(shas), src

    def sha(self, rid: str) -> str:
        if not self._shas.get(rid) and self.store.on and self._src.get(rid):
            try:
                self._shas[rid] = matter_paid.sha_file(Path(self._src[rid]))
            except OSError:
                self._shas[rid] = ""
        return self._shas.get(rid) or ""

    def get(self, rid: str, pno: int) -> str | None:
        return self.store.get(self.sha(rid), pno, self.method, self.matter)

    def put(self, rid: str, pno: int, text: str, stop_reason: str | None) -> None:
        self.store.put(self.sha(rid), pno, self.method, text, self.matter, stop_reason=stop_reason)


def _gather(
    sr: StageRun,
    queue: list[dict[str, Any]],
    src: dict[str, str],
    log_path: Path,
    store: PageStore | None = None,
) -> tuple[dict[str, dict[str, Any]], list[tuple[dict[str, Any], int, str]]]:
    import pymupdf

    d = sr.slug_dir
    files: dict[str, dict[str, Any]] = {}
    pending: list[tuple[dict[str, Any], int, str]] = []
    recorded = recorded_ids(d)
    skips = load_skips(d)
    for rec in queue:
        only = [int(p) for p in rec.get("vision_pages") or []]
        if only:
            if merged(d / "text" / f"{rec['id']}.txt", only, retry_failed=True):
                continue
        # The SAME reader build_units uses. These two asked the same question
        # with two copies of the same literal and drew opposite conclusions:
        # a short transcription was "not read" there and "already read" here,
        # so four screenshots halted a chronology AND were re-transcribed, and
        # re-paid for, on every resume.
        elif transcript_state(d, rec["id"], recorded)[0] not in UNREAD:
            continue
        path = src.get(rec["id"])
        if not path:
            continue
        part_path = d / "partial" / f"{rec['id']}.jsonl"
        done = load_partial(part_path)
        for pno in skips.get(rec["id"], ()):
            done.setdefault(pno, SKIPPED)
        for pno in only:
            if done.get(pno) == FAILED:
                del done[pno]  # a failed image page is asked again on a resume, never kept as read
        try:
            doc = pymupdf.open(path)
            if unopened.locked(doc):
                # pymupdf counts a locked file's pages and raises only at the
                # render below, outside this guard, which crashed the stage.
                doc.close()
                raise unopened.Locked()
            npages = len(doc)
        except Exception as exc:  # noqa: BLE001 - an unopenable file is a results row
            append_jsonl(log_path, {"id": rec["id"], "name": rec["name"], "error": str(exc)[:200]})
            continue
        if done:
            sr.log(f"{rec['name'][:45]}: resuming, {len(done)}/{npages} done")
        want = [p for p in only if 1 <= p <= npages] if only else list(range(1, npages + 1))
        files[rec["id"]] = {"rec": rec, "npages": npages, "done": done, "part": part_path, "only": only}
        for pno in want:
            if pno in done:
                continue
            hit = store.get(rec["id"], pno) if store is not None else None
            if hit is not None:
                done[pno] = handwriting_field(hit)
                append_jsonl(part_path, {"page": pno, "text": done[pno]})
                continue
            pending.append((rec, pno, render_page(doc[pno - 1], sr.log, rec["name"], pno)))
        doc.close()
    return files, pending


def _finish(sr: StageRun, f: dict[str, Any], log_path: Path) -> bool:
    """Write one file's transcription; False when pages never returned. The
    image pages of a text-layer PDF are merged into its text instead."""
    rec, npages, done = f["rec"], f["npages"], f["done"]
    want = [p for p in f["only"] if 1 <= p <= npages] if f["only"] else list(range(1, npages + 1))
    missing = [n for n in want if n not in done]
    if missing:
        sr.log(f"  {rec['name'][:40]}: {len(missing)} page(s) never returned; file left incomplete for a resume")
        return False
    tp = sr.slug_dir / "text" / f"{rec['id']}.txt"
    if f["only"]:
        _write_transcript(tp, merge_pages(tp.read_text(encoding="utf-8") if tp.is_file() else "", done, want))
        sr.log(f"image pages of {rec['name'][:45]}: {len(want)} transcribed into its text")
    else:
        body = "\n".join(f"[p.{n}] (machine transcription)\n{done.get(n, '')}" for n in range(1, npages + 1))
        _write_transcript(tp, body)
        sr.log(f"scanned {rec['name'][:50]}: {npages}pp")
    # Failure markers are counted like illegible marks, so pages_out ==
    # pages cannot report CLEAN over a page that transcribed to nothing. The
    # image pages of a text-layer PDF get the row too: before 2026-10-17 that
    # path returned first, and a page vision failed or refused on was
    # disclosed in Limitations nowhere.
    read = {n: done[n] for n in want}
    append_jsonl(
        log_path,
        {
            "id": rec["id"],
            "name": rec["name"],
            "pages": npages,
            "pages_out": len(read) if f["only"] else len(done),
            "failed_pages": sum(1 for t in read.values() if FAILED in t or REFUSED in t),
            "illegible_marks": sum(t.count("[illegible]") for t in read.values()),
        },
    )
    return True


def _transcribe(sr: StageRun, groups: list[list[llm.Item]], on_result: Callable[..., None], model: str, d: Path) -> int:
    """Every group's pages; the pages still in a processing batch. Batched,
    one submission per group as before; live, VISION_WORKERS pages at once,
    each its own task with the stop check first, and every result recorded
    under one lock (the part files, the page store, the file's done map)."""
    kw: dict[str, Any] = dict(
        model=model, system=SYSTEM, max_tokens=MAX_TOKENS, effort="", cache_blocks=("system",), batch_dir=d / "batch"
    )
    if "vision" in sr.doorway.batch_stages:
        timed_out = 0
        for items in groups:
            stop_request.check("vision")  # every finished page is already appended to its part file
            timed_out += len(sr.doorway.batch_call("vision", items, on_result, **kw).timed_out)
        return timed_out
    lock = threading.Lock()
    # Anything raised in a worker (a failed save, a stop, a defect) halts the
    # pool: a queued page returns before its call, so nothing more is paid
    # for. run_pool cancels queued tasks only on a limit.
    halt = threading.Event()

    def locked(item: llm.Item, r: llm.Result | None, err: str | None) -> None:
        with lock:
            on_result(item, r, err)

    def task(item: llm.Item) -> Callable[[], None]:
        def one() -> None:
            if halt.is_set():
                return
            try:
                # No new page starts once a stop is requested; a call already
                # in flight is the residual exposure (paid, unrecorded if killed).
                stop_request.check("vision")
                sr.doorway.batch_call("vision", [item], locked, **kw)
            except BaseException:
                halt.set()
                raise

        return one

    items = [it for g in groups for it in g]
    if items:
        run_pool([task(it) for it in items], max(1, min(VISION_WORKERS, len(items))))
    return 0


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    (d / "partial").mkdir(parents=True, exist_ok=True)
    (d / "text").mkdir(parents=True, exist_ok=True)
    queue = json.loads((d / "scan_queue.json").read_text(encoding="utf-8"))
    pulled = [r for r in read_jsonl(d / "raw_manifest.jsonl") if r.get("ok") and r.get("path")]
    src = {r["id"]: r["path"] for r in pulled}
    log_path = d / "ocr_results.jsonl"
    vision_seed.seed(sr, queue)
    model = llm.model_for(sr.cfg, "transcription")
    store = PageStore(sr, model, {r["id"]: str(r.get("sha256") or "") for r in pulled}, src)
    files, pending = _gather(sr, queue, src, log_path, store)
    sr.log(f"{len(files)} file(s), {len(pending)} page(s) to transcribe")

    def on_result(item: llm.Item, r: llm.Result | None, err: str | None) -> None:
        f = files[item.meta["id"]]
        pno = item.meta["page"]
        if err is not None:
            # A 4xx is not a transient and repeats identically on every retry;
            # the doorway raised it at once with the full message.
            text = REFUSED if err == "refusal" else FAILED
            sr.log(f"  {item.meta['name'][:30]} p{pno}: {str(err)[:400]}")
        elif r is not None and r.stop_reason == "refusal":
            text = REFUSED
        else:
            text = handwriting_field(r.text) if r is not None else FAILED
            if r is not None:
                store.put(item.meta["id"], pno, r.text, r.stop_reason)
        append_jsonl(f["part"], {"page": pno, "text": text})
        f["done"][pno] = text
        n = len(f["done"])
        if n % 20 == 0 or n == f["npages"]:
            sr.log(f"  {item.meta['name'][:40]}: {n}/{f['npages']}")

    groups: list[list[llm.Item]] = []
    group: list[llm.Item] = []
    size = 0
    for rec, pno, b64 in pending:
        if group and size + len(b64) > GROUP_B64_BUDGET:
            groups.append(group)
            group, size = [], 0
        group.append(page_item(rec, pno, b64))
        size += len(b64)
    if group:
        groups.append(group)
    timed_out = _transcribe(sr, groups, on_result, model, d)

    store.store.flush()
    incomplete = sum(0 if _finish(sr, f, log_path) else 1 for f in files.values())
    if incomplete:
        sr.log(
            f"VISION INCOMPLETE: {incomplete} file(s) not finished"
            + (f" ({timed_out} page(s) in a batch still processing; rerun resumes it)" if timed_out else "")
        )
        return 1
    sr.log("VISION DONE")
    return 0
