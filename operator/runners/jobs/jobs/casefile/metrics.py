"""Where the money went: tokens and cents per stage and per matter.

Read from the job's usage ledger (every paid call writes one row, ``ledger.py``)
and priced by the shared table (``budget.Pricing``), so the figure is the same
one the cap meters. A row's matter is its ``custom_id``'s (``<stage>-<matter
id>[-final]``). This is how a price is measured: a proof run's verdict says
what each matter cost and in which stage, at standard (non-batch) rates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..ledger import read_rows

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
FIELDS = ("in", "out", "cache_read", "cache_write")


def _blank() -> dict[str, Any]:
    return {"calls": 0, "cents": 0.0, **{k: 0 for k in FIELDS}}


def _add(acc: dict[str, Any], row: dict[str, Any], dollars: float) -> None:
    acc["calls"] += 1
    acc["cents"] += dollars * 100
    for k in FIELDS:
        acc[k] += int(row.get(k) or 0)


def summarize(ledger: Path, pricing: Any) -> dict[str, Any]:
    stages: dict[str, dict[str, Any]] = {}
    matters: dict[str, dict[str, Any]] = {}
    for row in read_rows(ledger):
        try:
            dollars = pricing.price_row(row)
        except Exception:  # noqa: BLE001 - an unpriceable row is counted at zero here; the cap prices it itself
            dollars = 0.0
        _add(stages.setdefault(str(row.get("stage") or "?"), _blank()), row, dollars)
        hit = _UUID.search(str(row.get("custom_id") or ""))
        if hit:
            _add(matters.setdefault(hit.group(0).lower(), _blank()), row, dollars)
    for bucket in (*stages.values(), *matters.values()):
        bucket["cents"] = int(round(bucket["cents"]))
    read = [m["cents"] for m in matters.values()]
    return {
        "stages": stages,
        "matters": matters,
        "matters_paid": len(read),
        "cents_per_matter_avg": int(round(sum(read) / len(read))) if read else 0,
    }


__all__ = ["summarize"]
