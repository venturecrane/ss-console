"""One usage ledger for every paid call, and no price table of its own.

Every paid call writes one row here (the frozen tree learned this the
expensive way: per-stage ledgers once captured 42% of real spend, because two
stages wrote no usage at all). Rows are the shape `budget.py` reads live for
the cap, and they now carry two fields the delivered ledgers lacked: `pages`,
the number of page images in the call, so a per-page unit cost is a sum and
not an after-the-fact join against `extracted.jsonl`; and `custom_id`, so a
batch row can be tied back to the item it paid for.

Dollars come from `budget.Pricing` (the console's `anthropic_pricing.json`
with its multipliers), never from a table in this file: the stage that once
printed a figure at a rate card two generations stale is why.

Best-effort by design: a ledger failure must never kill a paid call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .budget import BudgetError, Pricing


def usage_dict(usage: Any) -> dict[str, int]:
    return {
        "in": int(getattr(usage, "input_tokens", 0) or 0),
        "out": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }


def count_pages(messages: list[dict[str, Any]] | None) -> int:
    """Image blocks in the request: the pages a transcription or verdict read."""
    n = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):
            n += sum(1 for b in c if isinstance(b, dict) and b.get("type") == "image")
    return n


@dataclass
class Ledger:
    path: Path

    def record(
        self,
        stage: str,
        model: str,
        usage: Any,
        *,
        effort: str | None,
        cache: bool,
        batch: bool,
        pages: int = 0,
        custom_id: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            rec: dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stage": stage,
                "model": model,
                **usage_dict(usage),
                "effort": effort,
                "cache": cache,
                "batch": batch,
                "pages": pages,
            }
            if custom_id is not None:
                rec["custom_id"] = custom_id
            if error is not None:
                rec["error"] = error
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:  # noqa: BLE001 - never kill a paid call over its receipt
            pass


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def report(path: Path, pricing: Pricing) -> str:
    """The per-stage table and JSON blob the frozen `ledger.py report` printed,
    priced by the shared table; an unknown model is counted, not zeroed."""
    rows = read_rows(path)
    by_stage: dict[str, dict[str, Any]] = {}
    unknown = 0
    for row in rows:
        s = by_stage.setdefault(
            str(row.get("stage") or "?"),
            {
                "models": set(),
                "calls": 0,
                "in": 0,
                "out": 0,
                "cache_read": 0,
                "cache_write": 0,
                "batch": 0,
                "pages": 0,
                "dollars": 0.0,
            },
        )
        s["models"].add(str(row.get("model") or "?"))
        s["calls"] += 1
        for k in ("in", "out", "cache_read", "cache_write", "pages"):
            s[k] += int(row.get(k) or 0)
        s["batch"] += 1 if row.get("batch") else 0
        try:
            s["dollars"] += pricing.price_row(row)
        except BudgetError:
            unknown += 1
    hdr = (
        f"{'stage':12s} {'calls':>6s} {'in':>12s} {'out':>10s} {'cache_rd':>10s} {'cache_wr':>10s} "
        f"{'batch':>6s} {'pages':>6s} {'dollars':>9s}"
    )
    lines = [f"ledger: {path}", hdr]
    tot = {"calls": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "batch": 0, "pages": 0, "dollars": 0.0}
    tokens_by_stage: dict[str, Any] = {}
    dollars_by_stage: dict[str, float] = {}
    for stage in sorted(by_stage):
        s = by_stage[stage]
        lines.append(
            f"{stage:12s} {s['calls']:6d} {s['in']:12,d} {s['out']:10,d} {s['cache_read']:10,d} "
            f"{s['cache_write']:10,d} {s['batch']:6d} {s['pages']:6d} {s['dollars']:9.2f}"
        )
        for k in tot:
            tot[k] += s[k]
        tokens_by_stage[stage] = {
            "model": ",".join(sorted(s["models"])),
            "calls": s["calls"],
            "in": s["in"],
            "out": s["out"],
            "cache_read": s["cache_read"],
            "cache_write": s["cache_write"],
            "pages": s["pages"],
        }
        dollars_by_stage[stage] = round(s["dollars"], 4)
    lines.append(
        f"{'TOTAL':12s} {tot['calls']:6d} {tot['in']:12,d} {tot['out']:10,d} {tot['cache_read']:10,d} "
        f"{tot['cache_write']:10,d} {tot['batch']:6d} {tot['pages']:6d} {tot['dollars']:9.2f}"
    )
    lines.append(f"unknown-model rows (unpriced): {unknown}")
    blob = {
        "rate_card": pricing.source,
        "tokens_by_stage": tokens_by_stage,
        "dollars_by_stage": dollars_by_stage,
        "dollars_total": round(tot["dollars"], 4),
    }
    lines.append(json.dumps(blob, sort_keys=True))
    return "\n".join(lines)
