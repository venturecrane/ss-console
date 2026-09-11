"""Cost telemetry ingestion — pulls per-day cost data from external billing
APIs and UPSERTs it into the per-customer `cost_telemetry` D1 table.

This is the source side of the §15.1 cost-driver pipeline. The rollup side
(`cost_rollup.py`) reads what this module writes. The cron seam is the
Cloudflare Worker at `infra/workers/cost-telemetry/`, which iterates every
active customer once per day at 02:00 UTC and invokes the per-customer
ingest run.

Design notes
------------

* The ingest is per-customer because cost_telemetry is per-customer
  (ADR 0009: one D1 binding per customer; no cross-customer table). The
  caller passes a `CostIngestExecutor` already bound to the customer's
  database. There is no `customer_id` argument and no row-level
  customer column.

* Sources covered in this v1:

    1. Anthropic billing API — input_tokens and output_tokens, costed
       via `cost_telemetry/anthropic_pricing.json`. Daily aggregate
       per model.

  Cloudflare D1/R2/Vectorize and Fly compute remain in the spec but are
  deferred per the validation-spike result documented in this module's
  `validation_spike` section. Token cost dominates the COGS surface; the
  spec explicitly permits a phase-2 defer when the metering source is
  unworkable.

* Each source ingest is independent. A failure in one source does NOT
  block the others — the spec calls for "log to Captain alerting; do
  NOT block other sources" and the dashboard surfaces stale-source
  warnings. This module returns an `IngestRunResult` that names every
  source's outcome so the cron loop can surface failures without
  crashing the whole pass.

* UPSERT semantics match the rest of cost_telemetry: multiple events
  for the same `(date, driver)` accumulate via
  `ON CONFLICT (date, driver) DO UPDATE SET amount_cents =
  amount_cents + excluded.amount_cents, units = units + excluded.units`.
  Re-running the ingest for the same day after a fixed billing-API
  outage does NOT double-count *within the same run* — each source's
  ingest replaces, not accumulates, by computing a single UPSERT row
  per `(date, driver)`. Cross-run double-counting is the caller's
  responsibility (the cron only runs once per day per customer).

* No autonomous send. This module writes to D1. It does not send
  alerts. The §17.1 alert seam is the Captain dashboard's
  responsibility.

Validation spike (per cost-telemetry-events.md "Resolved decisions" #824)
-----------------------------------------------------------------------

This module SKIPS Cloudflare D1/R2/Vectorize metering for v1. The spec
prescribes Cloudflare GraphQL Analytics; the validation step here is
the live-API check. Outcome of the v1 spike (2026-05-23):

  - The Cloudflare GraphQL Analytics endpoint exposes account-level
    aggregates but does not, at the time of the spike, partition by
    individual D1 database / R2 bucket / Vectorize index in a shape
    that lets us attribute cost to a single customer-namespaced
    resource. The account-aggregate signal would conflate every
    customer's usage into one number, which violates the §15.1
    "per-customer per-day cost driver attribution" contract.
  - Per the cost-telemetry-events.md fallback clause ("if GraphQL
    Analytics access turns out to be unworkable for our auth model
    or rate limits, defer D1 cost-driver instrumentation to phase 2
    — Anthropic API tokens dominate the COGS surface"), the D1, R2,
    and Vectorize ingests are deferred to phase 2. The PR body
    documents this gap.
  - Anthropic API tokens cover the dominant COGS surface for v1,
    sufficient to compute the §17.1 COGS/MRR kill criterion within
    the modeling margin already accepted.

When the gap is closed (phase 2), add an `ingest_cloudflare_metrics()`
function with the same shape as `ingest_anthropic_billing()` and wire
it into `run_ingest_for_customer()`. No other module needs to change.

Failure modes
-------------

* Anthropic billing API returns 4xx/5xx: source result `ok=False`,
  reason logged. Other sources continue. Cron logs the summary; Captain
  sees a stale-source warning on the dashboard.
* Anthropic returns rows for a model not in `anthropic_pricing.json`:
  log warning, write the row with `amount_cents=0` and `units=` the
  raw token count. The Captain dashboard surfaces unknown-model rows
  as triage candidates. This is preferable to silently dropping
  usage — a missing price is a pricing-file gap, not a usage gap.
* D1 UPSERT fails: surfaces as the underlying executor exception. The
  module does not swallow database failures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol, Sequence

log = logging.getLogger("aie.cost_ingest")


# ---------------------------------------------------------------------------
# Pricing file loaders
# ---------------------------------------------------------------------------

_PRICING_DIR = Path(__file__).parent / "cost_telemetry"


def _load_pricing(filename: str) -> dict:
    """Load a pricing JSON. Raises FileNotFoundError if missing."""
    path = _PRICING_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"cost telemetry pricing file not found: {path}; must be checked in alongside this module"
        )
    return json.loads(path.read_text())


def load_anthropic_pricing(filename: str = "anthropic_pricing.json") -> dict:
    """Load Anthropic per-model pricing. Returns the parsed JSON."""
    return _load_pricing(filename)


# ---------------------------------------------------------------------------
# Executor / source protocols
# ---------------------------------------------------------------------------


class CostIngestExecutor(Protocol):
    """D1 executor bound to the per-customer cost_telemetry table."""

    async def execute(self, sql: str, params: list) -> None: ...


class AnthropicBillingSource(Protocol):
    """Pull token usage for a single day, grouped by model.

    The production implementation calls the Anthropic billing API. Tests
    pass in a stub. The shape returned must be a sequence of
    (model, input_tokens, output_tokens) tuples — one row per model the
    customer's API key used that day.
    """

    async def fetch_daily_usage(
        self,
        api_key: str,
        day: date,
    ) -> Sequence[tuple[str, int, int]]: ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceIngestResult:
    """Outcome of a single source's ingest."""

    source: str
    ok: bool
    rows_written: int = 0
    cents_written: int = 0
    reason: Optional[str] = None


@dataclass(frozen=True)
class IngestRunResult:
    """Aggregate outcome of one customer's daily ingest run."""

    customer_slug: str
    day: str  # YYYY-MM-DD
    sources: tuple[SourceIngestResult, ...]

    @property
    def any_failures(self) -> bool:
        return any(not s.ok for s in self.sources)

    @property
    def total_cents(self) -> int:
        return sum(s.cents_written for s in self.sources)


# ---------------------------------------------------------------------------
# UPSERT helper
# ---------------------------------------------------------------------------


_UPSERT_SQL = (
    "INSERT INTO cost_telemetry (date, driver, amount_cents, units, unit_type) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT (date, driver) DO UPDATE SET "
    "  amount_cents = amount_cents + excluded.amount_cents, "
    "  units = units + excluded.units"
)


async def _upsert_row(
    executor: CostIngestExecutor,
    day_str: str,
    driver: str,
    amount_cents: int,
    units: float,
    unit_type: str,
) -> None:
    """One UPSERT into cost_telemetry. Raises if D1 errors."""
    await executor.execute(
        _UPSERT_SQL,
        [day_str, driver, amount_cents, units, unit_type],
    )


# ---------------------------------------------------------------------------
# Anthropic ingest
# ---------------------------------------------------------------------------


def _compute_anthropic_cents(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict,
) -> tuple[int, int, Optional[str]]:
    """Return (input_cents, output_cents, warning).

    `warning` is a human-readable string when the model is not in the
    pricing file; in that case both cents values are 0 (units are still
    written so the dashboard can surface unknown-model usage).
    """
    models = pricing.get("models", {})
    entry = models.get(model)
    if entry is None:
        return 0, 0, (f"model {model!r} not in anthropic_pricing.json; wrote tokens with amount_cents=0")
    in_per_m = int(entry.get("input_per_million_cents", 0))
    out_per_m = int(entry.get("output_per_million_cents", 0))
    in_cents = (input_tokens * in_per_m) // 1_000_000
    out_cents = (output_tokens * out_per_m) // 1_000_000
    return in_cents, out_cents, None


async def ingest_anthropic_billing(
    executor: CostIngestExecutor,
    source: AnthropicBillingSource,
    api_key: str,
    day: date,
    pricing: Optional[dict] = None,
) -> SourceIngestResult:
    """Pull yesterday's Anthropic token usage and UPSERT into cost_telemetry.

    Emits two rows per model:
      driver=claude_api_input_tokens,  unit_type=input_tokens
      driver=claude_api_output_tokens, unit_type=output_tokens

    Per-model rows are SUMMED into the per-driver row, mirroring how the
    per-call buffer flush in `cost_event_buffer.py` (future work)
    accumulates into the same `(date, driver)` PK.
    """
    if pricing is None:
        pricing = load_anthropic_pricing()
    day_str = day.isoformat()

    try:
        rows = await source.fetch_daily_usage(api_key, day)
    except Exception as e:  # noqa: BLE001 - the vendor fetch failing must not abort the day's ingest; it is logged and the day is marked unfetched
        log.warning(
            "anthropic_billing fetch failed for %s: %s",
            day_str,
            e,
        )
        return SourceIngestResult(
            source="anthropic_billing",
            ok=False,
            reason=f"fetch failed: {e}",
        )

    total_in_tokens = 0
    total_out_tokens = 0
    total_in_cents = 0
    total_out_cents = 0
    warnings: list[str] = []

    for model, in_tokens, out_tokens in rows:
        in_cents, out_cents, warn = _compute_anthropic_cents(model, in_tokens, out_tokens, pricing)
        if warn:
            warnings.append(warn)
            log.warning(warn)
        total_in_tokens += int(in_tokens)
        total_out_tokens += int(out_tokens)
        total_in_cents += in_cents
        total_out_cents += out_cents

    rows_written = 0
    if total_in_tokens > 0:
        await _upsert_row(
            executor,
            day_str,
            "claude_api_input_tokens",
            total_in_cents,
            float(total_in_tokens),
            "input_tokens",
        )
        rows_written += 1
    if total_out_tokens > 0:
        await _upsert_row(
            executor,
            day_str,
            "claude_api_output_tokens",
            total_out_cents,
            float(total_out_tokens),
            "output_tokens",
        )
        rows_written += 1

    return SourceIngestResult(
        source="anthropic_billing",
        ok=True,
        rows_written=rows_written,
        cents_written=total_in_cents + total_out_cents,
        reason="; ".join(warnings) if warnings else None,
    )


# ---------------------------------------------------------------------------
# Per-customer run entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CustomerIngestContext:
    """Per-customer wiring passed to the daily run.

    The cron loop builds one of these per customer from the customer's
    Hermes Machine bindings (API keys are per-customer secrets) and the
    central customer_configs row (slug).
    """

    customer_slug: str
    anthropic_api_key: str
    executor: Optional[CostIngestExecutor] = field(default=None)


async def run_ingest_for_customer(
    ctx: CustomerIngestContext,
    anthropic_source: AnthropicBillingSource,
    day: Optional[date] = None,
) -> IngestRunResult:
    """Run every enabled source for one customer for one day.

    Default day is yesterday-UTC (the standard nightly cron target).

    Failures in one source do NOT block other sources. The
    `IngestRunResult` aggregates per-source outcomes; the caller decides
    whether to alert Captain.
    """
    if ctx.executor is None:
        raise ValueError(
            "CustomerIngestContext.executor is required; the cron worker must bind a CostIngestExecutor before calling"
        )

    if day is None:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    day_str = day.isoformat()

    source_results: list[SourceIngestResult] = []

    # Anthropic
    try:
        anthropic_result = await ingest_anthropic_billing(
            ctx.executor,
            anthropic_source,
            ctx.anthropic_api_key,
            day,
        )
    except Exception as e:  # noqa: BLE001 - one source raising must not abort the other sources' ingest; it is logged as that source's failure
        log.error(
            "ingest_anthropic_billing raised for %s on %s: %s",
            ctx.customer_slug,
            day_str,
            e,
        )
        anthropic_result = SourceIngestResult(
            source="anthropic_billing",
            ok=False,
            reason=f"unhandled exception: {e}",
        )
    source_results.append(anthropic_result)

    return IngestRunResult(
        customer_slug=ctx.customer_slug,
        day=day_str,
        sources=tuple(source_results),
    )


__all__ = [
    "AnthropicBillingSource",
    "CostIngestExecutor",
    "CustomerIngestContext",
    "IngestRunResult",
    "SourceIngestResult",
    "ingest_anthropic_billing",
    "load_anthropic_pricing",
    "run_ingest_for_customer",
]
