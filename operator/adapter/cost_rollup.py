"""Per-customer cost attribution rollup (issue #884).

Aggregates rows from the per-customer `cost_telemetry` D1 table into a
monthly view broken down by cost driver. The §17.1 COGS/MRR margin gate
reads from the totals computed here.

Design notes
------------

* The rollup is per-customer because the `cost_telemetry` table is
  per-customer (ADR 0009: one D1 binding per customer; no cross-customer
  table). The caller passes an `Executor` already bound to the customer's
  database. There is no `customer_id` argument and no row-level customer
  column.

* Drivers are grouped into nine `DriverCategory` buckets that match the
  §15.1 cost-driver enumeration. Several raw drivers
  (`claude_api_input_tokens` + `claude_api_output_tokens`,
  `r2_storage_gb_hours` + `r2_class_a_ops` + `r2_class_b_ops`, etc.)
  roll up into a single category so the COGS line items match the
  pricing model.

* Anything emitted under a driver name that is not in the closed
  category map lands in `DriverCategory.OTHER`. New drivers are added by
  extending `_DRIVER_TO_CATEGORY` in the same PR that adds the emitter,
  not silently bucketed.

* The query computes monthly totals by `(year_month, driver)` and the
  module folds those rows into per-category sums + a grand total + a
  per-category percentage breakdown. Percentages are computed in integer
  basis points (1/100 of a percent) to avoid float drift in the
  COGS/MRR ratio downstream.

* Aggregation cadence: real-time per-event INSERT via the emitter
  (cost-telemetry-events.md). The rollup is computed on-demand by this
  module. A future nightly cron seam is documented but not implemented
  here — `compute_monthly_rollup` is callable from a Worker cron or from
  the Captain dashboard handler equally.

* No autonomous send. This module produces values; it does not trigger
  alerts or notifications. The §17.1 alert seam is the Captain
  dashboard's responsibility.

Failure modes
-------------

* `cost_telemetry` empty for the requested month: returns a
  `MonthlyRollup` with zero totals and an empty per-driver breakdown.
  This is not an error — a brand-new customer has no rows yet.

* Database error: surfaces as the underlying executor exception. The
  module does not swallow database failures.

* Driver value not in the closed enum: bucketed into
  `DriverCategory.OTHER` with the raw driver name preserved in the
  per-driver detail map. Captain dashboard can surface the unknown
  driver name for triage.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence

log = logging.getLogger("aie.cost_rollup")


# ---------------------------------------------------------------------------
# Driver categories
#
# Source of truth: §15.1 (nine cost drivers) +
# docs/specs/operator/cost-telemetry-events.md (raw driver enum that
# emitters write).
# ---------------------------------------------------------------------------


class DriverCategory(str, enum.Enum):
    """High-level COGS categories that the pricing model groups by.

    The pricing model has nine line items per profile per month; these
    are the same nine.
    """

    ANTHROPIC_LLM = "anthropic_llm"
    FLY_COMPUTE = "fly_compute"
    CLOUDFLARE_D1 = "cloudflare_d1"
    CLOUDFLARE_R2 = "cloudflare_r2"
    CLOUDFLARE_VECTORIZE = "cloudflare_vectorize"
    AGENTMAIL = "agentmail"
    CAPTAIN_TIME = "captain_time"
    OTHER = "other"


# Raw driver -> category. Drivers not present here fall through to OTHER.
# Sourced from cost-telemetry-events.md "Drivers + emission sources" table.
_DRIVER_TO_CATEGORY: Mapping[str, DriverCategory] = {
    # Anthropic LLM
    "claude_api_input_tokens": DriverCategory.ANTHROPIC_LLM,
    "claude_api_output_tokens": DriverCategory.ANTHROPIC_LLM,
    # Fly
    "fly_machine_minutes": DriverCategory.FLY_COMPUTE,
    # Cloudflare D1
    "d1_reads": DriverCategory.CLOUDFLARE_D1,
    "d1_writes": DriverCategory.CLOUDFLARE_D1,
    # Cloudflare R2
    "r2_storage_gb_hours": DriverCategory.CLOUDFLARE_R2,
    "r2_class_a_ops": DriverCategory.CLOUDFLARE_R2,
    "r2_class_b_ops": DriverCategory.CLOUDFLARE_R2,
    # Cloudflare Vectorize
    "vectorize_queries": DriverCategory.CLOUDFLARE_VECTORIZE,
    "vectorize_dimensions_stored": DriverCategory.CLOUDFLARE_VECTORIZE,
    # AgentMail
    "agentmail_messages": DriverCategory.AGENTMAIL,
    "agentmail_mailbox_days": DriverCategory.AGENTMAIL,
    # Captain time
    "captain_time": DriverCategory.CAPTAIN_TIME,
}


def category_for_driver(driver: str) -> DriverCategory:
    """Map a raw `cost_telemetry.driver` value to a `DriverCategory`.

    Unknown drivers bucket into OTHER. The raw driver name is preserved
    by the caller in the `per_driver_detail_cents` map so the dashboard
    can surface the unmapped driver for triage.
    """
    return _DRIVER_TO_CATEGORY.get(driver, DriverCategory.OTHER)


# ---------------------------------------------------------------------------
# Year-month validation
# ---------------------------------------------------------------------------


def _validate_year_month(year_month: str) -> tuple[int, int]:
    """Parse a `YYYY-MM` string into `(year, month)`. Raises `ValueError`.

    The rollup is monthly-grained; the input is the calendar month in
    UTC. The format matches the `cost_telemetry.date` column's leading
    7 chars so the SQL filter is a simple prefix match.
    """
    if not isinstance(year_month, str):
        raise ValueError(f"year_month must be str, got {type(year_month).__name__}")
    if len(year_month) != 7 or year_month[4] != "-":
        raise ValueError(f"year_month must be 'YYYY-MM' (got {year_month!r})")
    try:
        year = int(year_month[:4])
        month = int(year_month[5:7])
    except ValueError as e:
        raise ValueError(f"year_month must be 'YYYY-MM' (got {year_month!r})") from e
    if not (1 <= month <= 12):
        raise ValueError(f"month must be 1..12 (got {month} in {year_month!r})")
    if year < 2026 or year > 2100:
        # Defensive bound; cost_telemetry only exists post-launch.
        raise ValueError(f"year must be 2026..2100 (got {year} in {year_month!r})")
    return year, month


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlyRollup:
    """Per-customer monthly cost rollup.

    Fields
    ------
    year_month : str
        'YYYY-MM' of the rollup month (UTC).
    total_cents : int
        Sum of `amount_cents` across every row in the month.
    by_category_cents : Mapping[DriverCategory, int]
        Per-category sum. Every `DriverCategory` is present even when
        zero so consumers can render a stable column set.
    by_category_basis_points : Mapping[DriverCategory, int]
        Per-category share as basis points (1/100 of a percent). The
        sum of the values is 10000 when `total_cents > 0`; the dict is
        all zeros when `total_cents == 0`. Integer math avoids float
        drift in the §17.1 COGS/MRR ratio computation.
    per_driver_detail_cents : Mapping[str, int]
        Raw `cost_telemetry.driver` -> sum-of-cents. Preserves the
        underlying driver names so a dashboard can drill in and so
        unmapped drivers in the OTHER bucket can be surfaced by name.
    row_count : int
        Number of `cost_telemetry` rows that fed the rollup. Useful for
        sanity-checking that the query actually returned data.
    """

    year_month: str
    total_cents: int
    by_category_cents: Mapping[DriverCategory, int]
    by_category_basis_points: Mapping[DriverCategory, int]
    per_driver_detail_cents: Mapping[str, int]
    row_count: int = 0

    def category_dollars(self, category: DriverCategory) -> float:
        """Return the category total in dollars (float). Convenience for
        rendering; the source of truth remains `by_category_cents`."""
        return self.by_category_cents.get(category, 0) / 100.0

    def total_dollars(self) -> float:
        """Return total in dollars (float). Convenience for rendering."""
        return self.total_cents / 100.0

    def cogs_mrr_basis_points(self, mrr_cents: int) -> Optional[int]:
        """Compute `total_cents / mrr_cents` as basis points.

        Returns None when `mrr_cents <= 0` (an unpriced customer; the
        ratio is undefined). The §17.1 kill criterion is
        `ratio > 0.40` for two consecutive months, which is 4000 basis
        points; the dashboard reads this value and applies the threshold.
        """
        if mrr_cents <= 0:
            return None
        return (self.total_cents * 10_000) // mrr_cents


# ---------------------------------------------------------------------------
# Executor protocol
#
# The reader uses a Protocol so tests can swap a sqlite executor for the
# production D1 HTTP executor. Same shape as audit_log.py's `Executor`.
# ---------------------------------------------------------------------------


class RowReader(Protocol):
    """Async row reader. Returns a list of (driver, total_cents) tuples."""

    async def fetch_rows(self, sql: str, params: list) -> Sequence[tuple]: ...


# ---------------------------------------------------------------------------
# SQL
#
# The query is a straight aggregate on the `(date, driver)` PK. SQLite
# and D1 both plan this as an index scan with on-the-fly aggregation.
# ---------------------------------------------------------------------------


# Use a leading-substring match on `date` so the (date, driver) PK index
# can serve the scan. cost_telemetry.date is 'YYYY-MM-DD'; the year_month
# prefix is 'YYYY-MM' followed by '-'.
_AGGREGATE_SQL = (
    "SELECT driver, COALESCE(SUM(amount_cents), 0) AS cents, "
    "COUNT(*) AS rowcount "
    "FROM cost_telemetry "
    "WHERE date >= ? AND date < ? AND amount_cents >= 0 "
    "GROUP BY driver "
    "ORDER BY driver"
)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """Return ('YYYY-MM-01', 'YYYY-MM+1-01') half-open range.

    Using a half-open range over the lexicographically-ordered date
    string lets SQLite/D1 plan an index range scan on the (date, driver)
    PK without a function call per row.
    """
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


# ---------------------------------------------------------------------------
# Rollup entry point
# ---------------------------------------------------------------------------


async def compute_monthly_rollup(
    reader: RowReader,
    year_month: str,
) -> MonthlyRollup:
    """Compute the monthly cost rollup for the bound customer.

    Parameters
    ----------
    reader : RowReader
        An executor already bound to the per-customer D1 database. The
        caller is responsible for picking the right binding per ADR 0009.
    year_month : str
        Calendar month in 'YYYY-MM' (UTC).

    Returns
    -------
    MonthlyRollup
        Total cents, per-category cents, per-category basis-points share,
        and a per-driver detail map of the raw `driver` values seen.
    """
    year, month = _validate_year_month(year_month)
    start, end = _month_bounds(year, month)

    rows = await reader.fetch_rows(_AGGREGATE_SQL, [start, end])

    per_driver: dict[str, int] = {}
    by_category: dict[DriverCategory, int] = {cat: 0 for cat in DriverCategory}
    total = 0
    row_count = 0

    for row in rows:
        # rows are (driver, cents, rowcount) tuples
        driver = row[0]
        cents = int(row[1]) if row[1] is not None else 0
        rowcount = int(row[2]) if len(row) > 2 and row[2] is not None else 0

        if cents < 0:
            # Per cost-telemetry-events.md, amounts are always integer cents
            # >= 0. A negative value indicates a corrupted row; log and skip
            # rather than poison the rollup.
            log.warning(
                "cost_rollup: negative amount_cents for driver=%r in %s; skipping",
                driver,
                year_month,
            )
            continue

        per_driver[driver] = per_driver.get(driver, 0) + cents
        category = category_for_driver(driver)
        by_category[category] = by_category.get(category, 0) + cents
        total += cents
        row_count += rowcount

    by_category_bps: dict[DriverCategory, int] = {cat: 0 for cat in DriverCategory}
    if total > 0:
        for cat, cents in by_category.items():
            # Integer basis points; floor division. Sum may be off by 1-2 bps
            # from rounding; the dashboard reconciles by reading total_cents
            # directly rather than summing per-category bps.
            by_category_bps[cat] = (cents * 10_000) // total

    return MonthlyRollup(
        year_month=year_month,
        total_cents=total,
        by_category_cents=by_category,
        by_category_basis_points=by_category_bps,
        per_driver_detail_cents=per_driver,
        row_count=row_count,
    )


# ---------------------------------------------------------------------------
# Sqlite reader (tests + local dev)
#
# Backs the rollup with a `sqlite3.Connection` whose schema matches the
# `cost_telemetry` table from migration 0001. Production wiring would
# use a `HttpD1Reader` that calls the Cloudflare D1 HTTP API; that
# wrapper lives outside this module and is configured by bootstrap.sh.
# ---------------------------------------------------------------------------


class SqliteRowReader:
    """Sqlite3-backed RowReader for tests and local dev.

    The caller supplies a `sqlite3.Connection` with the `cost_telemetry`
    schema applied (via migration 0001 or a hand-built CREATE TABLE in
    test setup). The reader runs the aggregate query synchronously
    inside the async method; sqlite calls are non-blocking enough for
    test scope.
    """

    def __init__(self, connection) -> None:
        self._conn = connection

    async def fetch_rows(self, sql: str, params: list) -> Sequence[tuple]:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


__all__ = [
    "DriverCategory",
    "MonthlyRollup",
    "RowReader",
    "SqliteRowReader",
    "category_for_driver",
    "compute_monthly_rollup",
]
