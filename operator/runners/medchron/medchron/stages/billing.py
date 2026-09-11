"""`billing_extract`: read every billing document visually and transcribe what
is PRINTED on it. Paid (transcription tier).

Text extraction cannot do this job: itemized statements yield zero dollar
amounts to a text layer, and a text layer that reads "$1,$00.00" where the
image reads "$1,800.00" loses precisely the figures a settlement demand is
built on. So every page is read as an image.

The division of labour is the point of the design: the model TRANSCRIBES
printed figures and never computes. Sums, comparisons and reconciliation are
done in Python afterwards, so every number can be traced to a page and every
total recomputed. Three pages per call (an eight-page chunk of itemised
charges generates output until it hits max_tokens); a chunk that will not
parse is halved and retried, a single dense page gets a larger budget and
then a totals-only read, and a page that still fails is a hard gap: the run
exits 1 rather than report a complete total over missing money.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from .. import llm
from .base import StageRun, append_jsonl, read_json, read_jsonl

PAGES_PER_CALL = 3
WORKERS = 6
FIRST_CAP = 8000
GROUP_B64_BUDGET = 400_000_000
RENDER_DPI = 115

SCHEMA_NOTE = """Return ONE json object, no prose, no code fence:

{
 "doc_type": "MEDICAL_BILL|LEDGER|LIEN_SUBROGATION|VENDOR_INVOICE|CERTIFICATE|RECORDS_ONLY|OTHER",
 "provider": "<the treating provider or clinic exactly as printed>",
 "patient": "<patient name exactly as printed, or null>",
 "date_first": "<earliest date of SERVICE on these pages, MM/DD/YYYY, or null>",
 "date_last":  "<latest date of SERVICE on these pages, MM/DD/YYYY, or null>",
 "printed_totals": [ {"label":"<the words next to it>", "amount":"<as printed>", "page":<n>} ],
 "line_items":     [ {"date":"<MM/DD/YYYY>", "description":"<short>", "charge":"<as printed>", "page":<n>} ],
 "notes": "<anything ambiguous, illegible, or that looks like a duplicate>"
}

RULES:
- Transcribe only. NEVER add, total, sum, or compute any figure. If a total is
  printed, copy it. If none is printed, printed_totals is [].
- Copy amounts exactly as shown including $ and commas. If a digit is
  illegible write it as "?" inside the number, e.g. "$1,?00.00".
- date_first/date_last are DATES OF SERVICE, not statement or print dates.
- doc_type VENDOR_INVOICE means a bill to the LAW FIRM from a service vendor
  (record retrieval, chronology services, imaging couriers) rather than
  treatment of the patient.
- doc_type LIEN_SUBROGATION means a health plan or its recovery vendor
  asserting reimbursement for benefits it paid, not a provider's charge.
- If these pages are clinical records with no charges, doc_type RECORDS_ONLY
  and printed_totals []."""

TOTALS_ONLY = """
OVERRIDE: this page carries too many line items to list. Set "line_items" to
[] and transcribe ONLY the printed totals, the provider, the patient and the
date range. Add "line_items_omitted": true."""


def render_doc(path: str) -> tuple[dict[int, str], int]:
    """Every page rendered SERIALLY before any call fans out: rendering under
    concurrency once returned None for 166 cited pages and the caller dropped
    them. Workers then do network I/O only."""
    import base64

    import pymupdf

    doc = pymupdf.open(path)
    n = len(doc)
    imgs = {
        p: base64.standard_b64encode(doc[p - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")).decode()
        for p in range(1, n + 1)
    }
    doc.close()
    return imgs, n


def range_messages(imgs: dict[int, str], name: str, start: int, end: int, totals_only: bool = False) -> list[dict]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"Document: {name}\nPages {start}-{end} follow. "
            + SCHEMA_NOTE
            + (TOTALS_ONLY if totals_only else ""),
        }
    ]
    for p in range(start, end + 1):
        content.append({"type": "text", "text": f"--- page {p} ---"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": imgs[p]}})
    return [{"role": "user", "content": content}]


def parse(result: llm.Result) -> dict[str, Any] | None:
    txt = re.sub(r"^```(?:json)?|```$", "", (result.text or "").strip(), flags=re.M).strip()
    if not txt:
        return None
    try:
        got = json.loads(txt)
    except json.JSONDecodeError:
        return None
    return got if isinstance(got, dict) else None


def usage_of(result: llm.Result) -> list[int]:
    return [int(getattr(result.usage, "input_tokens", 0) or 0), int(getattr(result.usage, "output_tokens", 0) or 0)]


def range_id(idx: int, name: str, start: int, end: int) -> str:
    """A custom_id the Batch API accepts, unique per document AND position:
    two documents can share a name, and the API refuses a duplicate id."""
    return f"b{idx}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}-{start}-{end}"


def first_ranges(n: int) -> list[tuple[int, int]]:
    return [(s, min(s + PAGES_PER_CALL - 1, n)) for s in range(1, n + 1, PAGES_PER_CALL)]


class _Reader:
    def __init__(self, sr: StageRun, model: str) -> None:
        self.sr, self.model = sr, model

    def one_call(
        self, imgs: dict[int, str], name: str, start: int, end: int, cap: int = FIRST_CAP, totals_only: bool = False
    ) -> tuple[dict[str, Any] | None, list[int]]:
        r = self.sr.doorway.call(
            "billing",
            model=self.model,
            max_tokens=cap,
            effort="",
            messages=range_messages(imgs, name, start, end, totals_only),
            cache_blocks=(),
            timeout=600.0,
        )
        return parse(r), usage_of(r)

    def read_doc(
        self,
        name: str,
        imgs: dict[int, str],
        n: int,
        first: dict[tuple[int, int], tuple[dict | None, list | None]] | None = None,
    ) -> dict[str, Any]:
        first = first or {}
        out: dict[str, Any] = {"file": name, "pages": n, "chunks": [], "usage": [], "failures": []}
        lock = threading.Lock()

        def work(rng: tuple[int, int]) -> list[dict[str, Any]]:
            start, end = rng
            got, usage = first[rng] if rng in first else self.one_call(imgs, name, start, end)
            with lock:
                if usage:
                    out["usage"].append(usage)
            if got is not None:
                return [got]
            if start == end:
                # A single page that overflowed 8k is dense, not broken: a much
                # larger budget, then totals-only, before giving up.
                for cap, only in ((32000, False), (8000, True)):
                    got, usage = self.one_call(imgs, name, start, end, cap=cap, totals_only=only)
                    with lock:
                        out["usage"].append(usage)
                    if got is not None:
                        if only:
                            got["line_items_omitted"] = True
                            with lock:
                                out.setdefault("totals_only_pages", []).append(start)
                        return [got]
                with lock:
                    out["failures"].append(start)
                return [{"FAILED_PAGE": start}]
            mid = (start + end) // 2
            self.sr.log(f"        retrying {start}-{end} as {start}-{mid} / {mid + 1}-{end}")
            return work((start, mid)) + work((mid + 1, end))

        ranges = first_ranges(n)
        if first:
            for rng in ranges:  # the batch answered the first level; failures recurse serially
                out["chunks"].extend(work(rng))
        else:
            with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for res in ex.map(work, ranges):
                    out["chunks"].extend(res)
        return out

    def batch_group(self, group: list[tuple[int, dict, dict[int, str], int]], batch_dir: Path) -> dict[int, dict]:
        items: list[llm.Item] = []
        first: dict[int, dict] = {}
        for i, t, imgs, n in group:
            first[i] = {}
            for s, e in first_ranges(n):
                items.append(
                    llm.Item(
                        custom_id=range_id(i, t["name"], s, e),
                        messages=range_messages(imgs, t["name"], s, e),
                        meta={"idx": i, "name": t["name"], "range": (s, e)},
                    )
                )

        def on_result(item: llm.Item, r: llm.Result | None, err: str | None) -> None:
            i, name, rng = item.meta["idx"], item.meta["name"], item.meta["range"]
            if err is not None or r is None:
                self.sr.log(f"        {name[:40]} p{rng[0]}-{rng[1]}: {str(err)[:200]}")
                first[i][rng] = (None, None)
                return
            first[i][rng] = (parse(r), usage_of(r))

        s = self.sr.doorway.batch_call(
            "billing",
            items,
            on_result,
            model=self.model,
            max_tokens=FIRST_CAP,
            effort="",
            cache_blocks=(),
            batch_dir=batch_dir,
        )
        for cid in s.timed_out:
            it = next(x for x in items if x.custom_id == cid)
            first[it.meta["idx"]][it.meta["range"]] = (None, None)
        return first


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    spec = read_json(d / "billing_docs.json", [])
    todo = spec.get("docs") if isinstance(spec, dict) else spec
    outp = d / "billing_extract.jsonl"
    # The evidence file exists from the moment the stage runs, even with zero
    # billing documents: build_units refuses on an authored billing set with
    # no extraction, and "nothing to extract" is an extraction result.
    outp.touch()
    done = {r.get("file") for r in read_jsonl(outp)}
    reader = _Reader(sr, llm.model_for(sr.cfg, "transcription"))
    tin = tout = hard = 0

    def finish(i: int, t: dict[str, Any], rec: dict[str, Any]) -> None:
        nonlocal tin, tout, hard
        append_jsonl(outp, rec)
        a, b = sum(u[0] for u in rec["usage"]), sum(u[1] for u in rec["usage"])
        tin, tout, hard = tin + a, tout + b, hard + len(rec["failures"])
        sr.log(
            f"        {a:,} in / {b:,} out" + (f"   !! {len(rec['failures'])} PAGE(S) LOST" if rec["failures"] else "")
        )

    pending = []
    for i, t in enumerate(todo or [], 1):
        if t["name"] in done:
            sr.log(f"[{i}/{len(todo)}] skip (done) {t['name'][:56]}")
            continue
        pending.append((i, t))
    # The page count is what the render measured, never a field off the spec:
    # billing_docs.json carries {id, name, path} and nothing else.
    if "billing" not in sr.doorway.batch_stages:
        for i, t in pending:
            imgs, n = render_doc(t["path"])
            sr.log(f"[{i}/{len(todo)}] {n:>3}pp  {t['name'][:56]}")
            finish(i, t, reader.read_doc(t["name"], imgs, n))
    else:
        groups: list[list] = []
        group: list = []
        size = 0
        for i, t in pending:
            imgs, n = render_doc(t["path"])
            b = sum(len(v) for v in imgs.values())
            if group and size + b > GROUP_B64_BUDGET:
                groups.append(group)
                group, size = [], 0
            group.append((i, t, imgs, n))
            size += b
        if group:
            groups.append(group)
        for group in groups:
            sr.log(
                f"batch: {len(group)} document(s), {sum(len(first_ranges(n)) for _, _, _, n in group)} first-level range(s)"
            )
            first = reader.batch_group(group, d / "batch")
            for i, t, imgs, n in group:
                sr.log(f"[{i}/{len(todo)}] {n:>3}pp  {t['name'][:56]}")
                finish(i, t, reader.read_doc(t["name"], imgs, n, first=first[i]))
    sr.log(f"{tin:,} input / {tout:,} output tokens")
    if hard:
        sr.log(f"!! {hard} page(s) could not be transcribed; the totals from this run are INCOMPLETE")
        return 1
    sr.log("every page transcribed")
    return 0
