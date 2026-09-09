"""The cents ledger, read live: running spend, projection, cap, and pages.

The pipeline's ledger is an append-only JSONL of token rows (`ts, stage, model,
in, out, cache_read, cache_write, batch`) written by the one paid doorway; the
price table it used lived in the private tree. This module prices those rows
from the console's `anthropic_pricing.json` (one table for the seat's cap and
the venture's telemetry) plus the batch and cache multipliers, and it REFUSES an
unknown model id rather than pricing it at zero, because a cap that prices
unknown models at zero cannot trip.

Reading is incremental: the ledger only grows, so the reader remembers its byte
offset and running totals per file and re-reads from there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The console's table: operator/adapter/cost_telemetry/anthropic_pricing.json,
# shipped in the image beside the runner (ss#2614). Path resolution mirrors the
# connector's config-as-data pattern: env override, then the fixed default.
PRICING_ENV = "MEDCHRON_PRICING_JSON"
PRICING_DEFAULT = "/opt/smd/cost_telemetry/anthropic_pricing.json"


class BudgetError(RuntimeError):
    """A pricing or cap condition the run must not proceed past."""


@dataclass(frozen=True)
class Rate:
    input_per_million_cents: int
    output_per_million_cents: int


@dataclass(frozen=True)
class Pricing:
    rates: dict[str, Rate]
    batch_multiplier: float
    cache_write_multiplier: float
    cache_read_multiplier: float
    source: str

    @classmethod
    def load(cls, path: Path) -> "Pricing":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        meta = data.get("_meta") or {}
        mult = meta.get("multipliers") or {}
        missing = [k for k in ("batch", "cache_write_5m", "cache_read") if k not in mult]
        if missing:
            raise BudgetError(
                f"{path}: _meta.multipliers is missing {missing}; the cap cannot price batch or "
                "cache rows without them (ss#2613)"
            )
        rates = {
            model: Rate(int(row["input_per_million_cents"]), int(row["output_per_million_cents"]))
            for model, row in (data.get("models") or {}).items()
        }
        return cls(
            rates=rates,
            batch_multiplier=float(mult["batch"]),
            cache_write_multiplier=float(mult["cache_write_5m"]),
            cache_read_multiplier=float(mult["cache_read"]),
            source=str(path),
        )

    def rate_for(self, model: str) -> Rate:
        if model in self.rates:
            return self.rates[model]
        # Longest-prefix match covers dated ids ("claude-haiku-4-5-20251001").
        best = max((m for m in self.rates if model.startswith(m)), key=len, default=None)
        if best is None:
            raise BudgetError(
                f"model {model!r} has no row in {self.source}; refusing to price it at zero"
            )
        return self.rates[best]

    def price_row(self, row: dict[str, Any]) -> float:
        """Dollars for one ledger row."""
        r = self.rate_for(str(row.get("model") or ""))
        inp = int(row.get("in") or 0)
        out = int(row.get("out") or 0)
        cw = int(row.get("cache_write") or 0)
        cr = int(row.get("cache_read") or 0)
        cents_per_m = (
            inp * r.input_per_million_cents
            + cw * r.input_per_million_cents * self.cache_write_multiplier
            + cr * r.input_per_million_cents * self.cache_read_multiplier
            + out * r.output_per_million_cents
        )
        dollars = cents_per_m / 1e8
        if row.get("batch"):
            dollars *= self.batch_multiplier
        return dollars


@dataclass
class _Cursor:
    offset: int = 0
    dollars: float = 0.0
    rows: int = 0
    by_stage: dict[str, float] = field(default_factory=dict)


@dataclass
class Budget:
    """Live spend for one job (all units) against one cap."""
    pricing: Pricing
    cap_usd: float
    ledgers: list[Path]
    usd_per_million_chars: float
    _cursors: dict[Path, _Cursor] = field(default_factory=dict)

    def refresh(self) -> float:
        for path in self.ledgers:
            cur = self._cursors.setdefault(path, _Cursor())
            if not path.is_file():
                continue
            with path.open("rb") as fh:
                fh.seek(cur.offset)
                chunk = fh.read()
            if not chunk:
                continue
            # Only whole lines are consumed; a partial trailing line waits.
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                continue
            for line in chunk[: last_nl + 1].splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                d = self.pricing.price_row(row)
                cur.dollars += d
                cur.rows += 1
                stage = str(row.get("stage") or "?")
                cur.by_stage[stage] = cur.by_stage.get(stage, 0.0) + d
            cur.offset += last_nl + 1
        return self.spent()

    def spent(self) -> float:
        return sum(c.dollars for c in self._cursors.values())

    def by_stage(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self._cursors.values():
            for k, v in c.by_stage.items():
                out[k] = out.get(k, 0.0) + v
        return out

    def projection(self, extracted_chars: int) -> float:
        return extracted_chars / 1e6 * self.usd_per_million_chars

    def check(self, *, stage: str, extracted_chars: int | None = None) -> None:
        """Refuse BEFORE a paid stage when spend already crossed the cap, or when
        the projection from extracted characters says the run will."""
        spent = self.refresh()
        if spent >= self.cap_usd:
            raise BudgetError(
                f"cap {self.cap_usd:.2f} USD reached before {stage}: {spent:.2f} USD spent"
            )
        if extracted_chars is not None:
            projected = self.projection(extracted_chars)
            if projected > self.cap_usd:
                raise BudgetError(
                    f"projection {projected:.2f} USD for {extracted_chars:,} extracted chars exceeds "
                    f"the cap {self.cap_usd:.2f} USD; not starting {stage}"
                )


def pages_read(extracted_jsonl: Path) -> int:
    """Pages the run has read, from the extract stage's per-file rows."""
    if not extracted_jsonl.is_file():
        return 0
    total = 0
    for line in extracted_jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        pages = row.get("pages")
        if isinstance(pages, int):
            total += pages
    return total


def scanned_pages(extracted_jsonl: Path) -> int:
    """Pages the extract stage routed to vision (no usable text layer). These
    are the expensive pages: transcription is per page and it is what made the
    measured cost of a scanned package what it is, so the pre-vision projection
    counts these, not every page."""
    if not extracted_jsonl.is_file():
        return 0
    total = 0
    for line in extracted_jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("scan") and isinstance(row.get("pages"), int):
            total += row["pages"]
    return total


def extracted_chars(extracted_jsonl: Path) -> int:
    if not extracted_jsonl.is_file():
        return 0
    total = 0
    for line in extracted_jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        chars = row.get("chars")
        if isinstance(chars, int):
            total += chars
    return total
