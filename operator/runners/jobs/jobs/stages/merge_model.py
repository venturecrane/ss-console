"""The model half of the merge: the clusters code routed, in batches, each
batch falsified before it is accepted and split on cluster boundaries when it
truncates or fails (the earlier version retried the identical batch, which
could only truncate again, then gave up, and 34 merged clusters were silently
missing from the document). Paid (mechanical tier).

Batches run MERGE_WORKERS at once: each is independent, each is falsified on
its own, and the results are joined in batch order and falsified again as a
whole, so the order replies arrive in changes nothing. One at a time, a
3,821-page matter spent about four hours here (20 batches, each truncating
and splitting).
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

BATCH_CHARS = 120_000
MAX_TOKENS = 32_000
MAX_DEPTH = 4
#: Batches merged at once.
MERGE_WORKERS = 8
_USAGE_LOCK = threading.Lock()


def _run_batch(
    sr: StageRun, d: Path, model: str, system: str, text: str, label: str, hd: mf.Headings, depth: int = 0
) -> list[str] | None:
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
            if rc == 0:
                sr.log(f"  merge {label} ok ({r.usage.output_tokens} out)")
                return [r.text]
            why = next((ln.strip() for ln in rep if "LOST" in ln), rep[{3: 0, 4: -3, 5: -2}.get(rc, -1)].strip())
            sr.log(f"  merge {label} FALSIFIED (exit {rc}): {why}")
    parts = [p for p in mf.CLUSTER_SPLIT.split(text) if p.strip()]
    if len(parts) < 2 or depth > MAX_DEPTH:
        sr.log(f"  merge {label} CANNOT SPLIT FURTHER")
        return None
    mid = len(parts) // 2
    sr.log(f"  merge {label} truncated or falsified: splitting {len(parts)} clusters into {mid}+{len(parts) - mid}")
    got: list[str] = []
    for j, half in enumerate(("".join(parts[:mid]), "".join(parts[mid:])), 1):
        time.sleep(1)
        sub = _run_batch(sr, d, model, system, half, f"{label}.{j}", hd, depth + 1)
        if sub is None:
            return None
        got += sub
    return got


def merge_blocks(sr: StageRun, d: Path, blocks: list[str], hd: mf.Headings) -> str | None:
    """Merged text for the given cluster blocks (in order), or None on failure."""
    batches: list[str] = []
    cur = ""
    for b in blocks:
        if cur and len(cur) + len(b) > BATCH_CHARS:
            batches.append(cur)
            cur = ""
        cur += b
    if cur:
        batches.append(cur)
    sr.log(f"{len(blocks)} routed clusters in {len(batches)} batch(es)")
    system = prompts.load("merge-system", sr.cfg)
    model = llm.model_for(sr.cfg, "mechanical")
    results: list[list[str] | None] = [None] * len(batches)

    def task(i: int) -> Callable[[], None]:
        def run() -> None:
            results[i] = _run_batch(sr, d, model, system, batches[i], str(i + 1), hd)

        return run

    run_pool([task(i) for i in range(len(batches))], max(1, min(MERGE_WORKERS, len(batches))))
    out: list[str] = []
    for i, res in enumerate(results, 1):
        if res is None:
            sr.log(f"  merge batch {i} FAILED")
            return None
        out += res
    merged = "\n\n".join(x.strip() for x in out)
    rc, rep = mf.check("".join(blocks), merged, hd)
    for line in rep:
        sr.log(line)
    return merged if rc == 0 else None
