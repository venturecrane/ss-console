"""The batch round's on-disk bookkeeping: how fresh requests are grouped into
batches under the API's size and count ceilings, and which batch files a stage
has already claimed.

Split from ``llm.py`` at the module-size ceiling (2026-11-22, when the doorway
gained its size stop, ``request_size.py``). Pure moves; ``llm`` re-exports the
names its callers and tests use (``llm._split_requests``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BATCH_BYTE_BUDGET = 200_000_000  # API ceiling is 256 MB; leave headroom
BATCH_COUNT_BUDGET = 10_000  # API ceiling is 100k; keep batches restartable


def split_requests(
    reqs: list[dict[str, Any]], byte_budget: int = BATCH_BYTE_BUDGET, count_budget: int = BATCH_COUNT_BUDGET
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for r in reqs:
        n = len(json.dumps(r).encode("utf-8"))
        if cur and (size + n > byte_budget or len(cur) >= count_budget):
            batches.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        batches.append(cur)
    return batches


def batch_files(batch_dir: Path, stage: str) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    if not batch_dir.is_dir():
        return files
    for p in batch_dir.iterdir():
        if p.name.startswith(f"batch-{stage}-") and p.suffix == ".json":
            try:
                files[p.name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - a corrupt batch file is skipped so the batch index still lists the readable ones
                continue
    return files


def next_index(existing: dict[str, Any], stage: str) -> int:
    n = 0
    for fn in existing:
        core = fn[len(f"batch-{stage}-") : -len(".json")].split("-")[0]
        if core.isdigit():
            n = max(n, int(core) + 1)
    return n
