"""The model half of the merge: the clusters code routed, in batches, each
batch falsified before it is accepted and split on cluster boundaries when it
truncates or fails (the earlier version retried the identical batch, which
could only truncate again, then gave up, and 34 merged clusters were silently
missing from the document). Paid (mechanical tier).

Batches run MERGE_WORKERS at once: each is independent, each is falsified on
its own, and the results are joined in batch order, so the order replies
arrive in changes nothing. One at a time, a 3,821-page matter spent about
four hours here (20 batches, each truncating and splitting).

The answer is per cluster (``merge_blocks``): a part that cannot be merged
(it cannot split further) leaves only ITS clusters for the code salvage
(``merge.salvage_entry``); every other part and batch keeps its model
result. WHY (2026-11-19, the saved
3,821-page run): the first failed batch discarded the accepted model results
of 23 clusters and all 122 were merged in code. A cluster too long for one
reply to echo is never sent (``SINGLE_MAX_CHARS``).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .. import llm
from ..medchron import prompts
from ..medchron.audit.run import run_pool
from ..limits import LimitHold
from . import merge_falsify as mf
from .base import StageRun, append_jsonl

#: Batch size in characters of cluster text. The model echoes its input, so a
#: batch is sized on the reply it needs: at 120,000 every top-level batch of
#: the saved 3,821-page run hit max_tokens (32,000 out on 38k-48k in), and
#: most halves then ended normally. Halving on truncation stays the backstop.
BATCH_CHARS = 60_000
#: A single cluster longer than this is merged in code and never sent.
#: Basis (2026-11-19): one cluster of a two-cluster half of about 89,000
#: characters echoed 31,427 tokens, 98% of MAX_TOKENS, and dropped a citation
#: (the falsifier caught it; one cluster cannot split). That is at most about
#: 2.8 characters per output token, so one reply holds about 90,000 echoed
#: characters; this keeps a fifth of it for the merge's own wording.
SINGLE_MAX_CHARS = 72_000
MAX_TOKENS = 32_000
MAX_DEPTH = 4
#: Batches merged at once.
MERGE_WORKERS = 8
_USAGE_LOCK = threading.Lock()


def _run_batch(
    sr: StageRun, d: Path, model: str, system: str, text: str, label: str, hd: mf.Headings, depth: int = 0
) -> list[str | None]:
    """The merged entry of each cluster in `text`, in order; None for each
    cluster of a part that could not be merged (it cannot split further)."""
    parts = [p for p in mf.CLUSTER_SPLIT.split(text) if p.strip()]
    r = None
    try:
        r = sr.doorway.call(
            "merge",
            model=model,
            system=system,
            messages=[{"role": "user", "content": text}],
            max_tokens=MAX_TOKENS,
            stream=True,
            custom_id=f"merge{label}",
        )
    except LimitHold:
        raise  # a limit stops the stage and reaches the driver as a hold (N10)
    except Exception as exc:  # noqa: BLE001 - a doorway failure on one batch is logged; the stage falls back to splitting the batch below
        sr.log(f"  merge {label} failed: {str(exc)[:110]}")
    if r is not None:
        with _USAGE_LOCK:
            append_jsonl(
                d / "usage.jsonl",
                {
                    "chunk": f"merge{label}",
                    "in": r.usage.input_tokens,
                    "out": r.usage.output_tokens,
                    "stop": r.stop_reason,
                },
            )
        if r.stop_reason != "max_tokens":
            rc, rep = mf.check(text, r.text, hd)
            entries = mf.parse_entries(r.text) if rc == 0 else []
            if rc == 0 and len(entries) == len(parts):
                sr.log(f"  merge {label} ok ({r.usage.output_tokens} out)")
                return [e["body"].rstrip() + "\n" for e in entries]
            why = next((ln.strip() for ln in rep if "LOST" in ln), rep[{3: 0, 4: -3, 5: -2}.get(rc, -1)].strip())
            sr.log(f"  merge {label} FALSIFIED (exit {rc}): {why}")
    if len(parts) < 2 or depth > MAX_DEPTH:
        sr.log(f"  merge {label} CANNOT SPLIT FURTHER: its {len(parts)} cluster(s) are merged in code")
        return [None] * len(parts)
    mid = len(parts) // 2
    sr.log(f"  merge {label} truncated or falsified: splitting {len(parts)} clusters into {mid}+{len(parts) - mid}")
    got: list[str | None] = []
    for j, half in enumerate(("".join(parts[:mid]), "".join(parts[mid:])), 1):
        time.sleep(1)
        got += _run_batch(sr, d, model, system, half, f"{label}.{j}", hd, depth + 1)
    return got


def _batches(blocks: list[str]) -> tuple[list[list[int]], list[int]]:
    """(batches of block indices, in order; the indices too long to send)."""
    batches: list[list[int]] = []
    oversize: list[int] = []
    cur: list[int] = []
    size = 0
    for i, b in enumerate(blocks):
        if len(b) > SINGLE_MAX_CHARS:
            oversize.append(i)
            continue
        if cur and size + len(b) > BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(b)
    if cur:
        batches.append(cur)
    return batches, oversize


def merge_blocks(sr: StageRun, d: Path, blocks: list[str], hd: mf.Headings) -> list[str | None]:
    """The merged entry for each cluster block, in order, or None for each
    cluster the model did not merge (its batch failed, or it is too long to
    send): the caller salvages exactly those in code."""
    batches, oversize = _batches(blocks)
    if oversize:
        sr.log(f"{len(oversize)} cluster(s) over {SINGLE_MAX_CHARS:,} characters merged in code, never sent")
    sr.log(f"{len(blocks) - len(oversize)} routed clusters in {len(batches)} batch(es)")
    system = prompts.load("merge-system", sr.cfg)
    model = llm.model_for(sr.cfg, "mechanical")
    out: list[str | None] = [None] * len(blocks)

    def task(k: int) -> Callable[[], None]:
        def run() -> None:
            idx = batches[k]
            got = _run_batch(sr, d, model, system, "".join(blocks[i] for i in idx), str(k + 1), hd)
            if len(got) != len(idx):  # never: a cluster split is the batching's own boundary
                got = [None] * len(idx)
            for i, e in zip(idx, got):
                out[i] = e

        return run

    if batches:
        run_pool([task(k) for k in range(len(batches))], max(1, min(MERGE_WORKERS, len(batches))))
    return out
