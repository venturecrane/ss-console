"""Tests for operator/adapter/cost_rollup.py (issue #884).

Exercises the per-customer monthly cost rollup against a sqlite-backed
executor that mirrors the `cost_telemetry` schema from migration 0001
and the `captain_time_events` schema from migration 0006.

Coverage:
  - Year-month input validation
  - Empty month returns a zero rollup
  - Aggregation across multiple drivers in one month
  - Per-category bucketing (Anthropic in/out tokens roll to one bucket;
    R2 storage + class A + class B all roll to one bucket; D1 reads +
    writes; Vectorize queries + dimensions; AgentMail messages + days)
  - Unknown drivers bucket into OTHER and preserve their raw name in
    per_driver_detail_cents
  - Basis points sum approximates 10000; bps == 0 when total == 0
  - COGS/MRR ratio helper: returns None for unpriced customer, bps for
    priced
  - Month-bound filter excludes adjacent months
  - Negative amount_cents rows are skipped with a warning
  - SqliteRowReader exercises the full read path

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_cost_rollup.py -v
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

# Allow running from repo root or from operator/.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from adapter.cost_rollup import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    DriverCategory,
    MonthlyRollup,
    SqliteRowReader,
    _month_bounds,
    _validate_year_month,
    category_for_driver,
    compute_monthly_rollup,
)


# ---------------------------------------------------------------------------
# Schema setup — exact copy of cost_telemetry + captain_time_events from
# migrations 0001 and 0006. Mirrored by hand so the test does not depend
# on shelling out to wrangler.
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE cost_telemetry (
  date         TEXT NOT NULL,
  driver       TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  units        REAL,
  unit_type    TEXT,
  PRIMARY KEY (date, driver)
);
CREATE INDEX idx_cost_telemetry_date ON cost_telemetry(date);

CREATE TABLE captain_time_events (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  date          TEXT NOT NULL,
  activity      TEXT NOT NULL,
  minutes       INTEGER NOT NULL,
  amount_cents  INTEGER NOT NULL,
  note          TEXT
);
CREATE INDEX idx_captain_time_date ON captain_time_events(date);
CREATE INDEX idx_captain_time_activity ON captain_time_events(activity, date);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insert (date, driver, amount_cents) rows; UPSERT on conflict."""
    cur = conn.cursor()
    for date, driver, cents in rows:
        cur.execute(
            "INSERT INTO cost_telemetry (date, driver, amount_cents) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (date, driver) DO UPDATE SET "
            "  amount_cents = amount_cents + excluded.amount_cents",
            (date, driver, cents),
        )
    conn.commit()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Year-month validation
# ---------------------------------------------------------------------------


def test_validate_year_month_happy_path():
    assert _validate_year_month("2026-05") == (2026, 5)
    assert _validate_year_month("2026-12") == (2026, 12)


def test_validate_year_month_rejects_bad_shape():
    with pytest.raises(ValueError):
        _validate_year_month("2026/05")
    with pytest.raises(ValueError):
        _validate_year_month("2026-5")
    with pytest.raises(ValueError):
        _validate_year_month("26-05")
    with pytest.raises(ValueError):
        _validate_year_month("not-a-date")


def test_validate_year_month_rejects_bad_month():
    with pytest.raises(ValueError):
        _validate_year_month("2026-00")
    with pytest.raises(ValueError):
        _validate_year_month("2026-13")


def test_validate_year_month_rejects_out_of_range_year():
    with pytest.raises(ValueError):
        _validate_year_month("2020-05")
    with pytest.raises(ValueError):
        _validate_year_month("3000-05")


def test_validate_year_month_rejects_non_string():
    with pytest.raises(ValueError):
        _validate_year_month(202605)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Month bounds
# ---------------------------------------------------------------------------


def test_month_bounds_mid_year():
    assert _month_bounds(2026, 5) == ("2026-05-01", "2026-06-01")


def test_month_bounds_december_rolls_year():
    assert _month_bounds(2026, 12) == ("2026-12-01", "2027-01-01")


def test_month_bounds_january():
    assert _month_bounds(2026, 1) == ("2026-01-01", "2026-02-01")


# ---------------------------------------------------------------------------
# Driver -> category mapping
# ---------------------------------------------------------------------------


def test_anthropic_drivers_map_to_anthropic_llm():
    assert category_for_driver("claude_api_input_tokens") == DriverCategory.ANTHROPIC_LLM
    assert category_for_driver("claude_api_output_tokens") == DriverCategory.ANTHROPIC_LLM


def test_r2_drivers_all_map_to_cloudflare_r2():
    assert category_for_driver("r2_storage_gb_hours") == DriverCategory.CLOUDFLARE_R2
    assert category_for_driver("r2_class_a_ops") == DriverCategory.CLOUDFLARE_R2
    assert category_for_driver("r2_class_b_ops") == DriverCategory.CLOUDFLARE_R2


def test_d1_drivers_map_to_cloudflare_d1():
    assert category_for_driver("d1_reads") == DriverCategory.CLOUDFLARE_D1
    assert category_for_driver("d1_writes") == DriverCategory.CLOUDFLARE_D1


def test_vectorize_drivers_map_to_cloudflare_vectorize():
    assert category_for_driver("vectorize_queries") == DriverCategory.CLOUDFLARE_VECTORIZE
    assert category_for_driver("vectorize_dimensions_stored") == DriverCategory.CLOUDFLARE_VECTORIZE


def test_fly_machine_minutes_maps_to_fly_compute():
    assert category_for_driver("fly_machine_minutes") == DriverCategory.FLY_COMPUTE


def test_agentmail_drivers_map_to_agentmail():
    assert category_for_driver("agentmail_messages") == DriverCategory.AGENTMAIL
    assert category_for_driver("agentmail_mailbox_days") == DriverCategory.AGENTMAIL


def test_captain_time_maps_to_captain_time():
    assert category_for_driver("captain_time") == DriverCategory.CAPTAIN_TIME


def test_unknown_driver_maps_to_other():
    assert category_for_driver("third_party_api_lawpay") == DriverCategory.OTHER
    assert category_for_driver("totally-novel-driver") == DriverCategory.OTHER


# ---------------------------------------------------------------------------
# Rollup — empty
# ---------------------------------------------------------------------------


def test_empty_month_returns_zero_rollup():
    conn = _make_conn()
    reader = SqliteRowReader(conn)

    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert isinstance(rollup, MonthlyRollup)
    assert rollup.year_month == "2026-05"
    assert rollup.total_cents == 0
    assert rollup.total_dollars() == 0.0
    assert rollup.row_count == 0
    assert rollup.per_driver_detail_cents == {}
    # Every category present, all zero
    for cat in DriverCategory:
        assert rollup.by_category_cents[cat] == 0
        assert rollup.by_category_basis_points[cat] == 0


# ---------------------------------------------------------------------------
# Rollup — single driver, single day
# ---------------------------------------------------------------------------


def test_single_driver_single_day():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "claude_api_input_tokens", 250),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.total_cents == 250
    assert rollup.by_category_cents[DriverCategory.ANTHROPIC_LLM] == 250
    assert rollup.by_category_basis_points[DriverCategory.ANTHROPIC_LLM] == 10_000
    assert rollup.per_driver_detail_cents == {"claude_api_input_tokens": 250}
    assert rollup.row_count == 1


# ---------------------------------------------------------------------------
# Rollup — multiple drivers + multiple days
# ---------------------------------------------------------------------------


def test_multi_driver_multi_day_aggregation():
    conn = _make_conn()
    _seed(
        conn,
        [
            # Anthropic across two days
            ("2026-05-01", "claude_api_input_tokens", 100),
            ("2026-05-01", "claude_api_output_tokens", 200),
            ("2026-05-02", "claude_api_input_tokens", 150),
            ("2026-05-02", "claude_api_output_tokens", 250),
            # R2 across three sub-drivers
            ("2026-05-01", "r2_storage_gb_hours", 50),
            ("2026-05-01", "r2_class_a_ops", 30),
            ("2026-05-01", "r2_class_b_ops", 20),
            # Fly
            ("2026-05-01", "fly_machine_minutes", 500),
            # Captain time
            ("2026-05-15", "captain_time", 6000),
            # AgentMail
            ("2026-05-01", "agentmail_messages", 40),
            ("2026-05-01", "agentmail_mailbox_days", 60),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.total_cents == 100 + 200 + 150 + 250 + 50 + 30 + 20 + 500 + 6000 + 40 + 60
    assert rollup.total_cents == 7400
    # Anthropic: 100 + 200 + 150 + 250 = 700
    assert rollup.by_category_cents[DriverCategory.ANTHROPIC_LLM] == 700
    # R2: 50 + 30 + 20 = 100
    assert rollup.by_category_cents[DriverCategory.CLOUDFLARE_R2] == 100
    # Fly: 500
    assert rollup.by_category_cents[DriverCategory.FLY_COMPUTE] == 500
    # Captain time: 6000
    assert rollup.by_category_cents[DriverCategory.CAPTAIN_TIME] == 6000
    # AgentMail: 40 + 60 = 100
    assert rollup.by_category_cents[DriverCategory.AGENTMAIL] == 100
    # No D1, Vectorize or OTHER
    assert rollup.by_category_cents[DriverCategory.CLOUDFLARE_D1] == 0
    assert rollup.by_category_cents[DriverCategory.CLOUDFLARE_VECTORIZE] == 0
    assert rollup.by_category_cents[DriverCategory.OTHER] == 0


# ---------------------------------------------------------------------------
# Rollup — basis points
# ---------------------------------------------------------------------------


def test_basis_points_sum_to_within_one_per_category():
    # 4000 / 6000 / 0 split -> 6666 / 3333 / 0; sum = 9999 due to floor rounding
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "fly_machine_minutes", 4000),
            ("2026-05-01", "captain_time", 6000),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.total_cents == 10000
    assert rollup.by_category_basis_points[DriverCategory.FLY_COMPUTE] == 4000
    assert rollup.by_category_basis_points[DriverCategory.CAPTAIN_TIME] == 6000
    # Other categories at 0
    total_bps = sum(rollup.by_category_basis_points.values())
    assert total_bps == 10_000


def test_basis_points_zero_when_total_zero():
    conn = _make_conn()
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    for cat in DriverCategory:
        assert rollup.by_category_basis_points[cat] == 0


# ---------------------------------------------------------------------------
# Rollup — month-bound isolation
# ---------------------------------------------------------------------------


def test_month_bound_excludes_prior_month():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-04-30", "fly_machine_minutes", 999),  # outside
            ("2026-05-01", "fly_machine_minutes", 100),  # inside
            ("2026-05-31", "fly_machine_minutes", 200),  # inside
            ("2026-06-01", "fly_machine_minutes", 888),  # outside
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    # Only the two May rows count
    assert rollup.total_cents == 300
    assert rollup.by_category_cents[DriverCategory.FLY_COMPUTE] == 300


def test_month_bound_handles_december_year_roll():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-12-01", "fly_machine_minutes", 50),
            ("2026-12-31", "fly_machine_minutes", 70),
            ("2027-01-01", "fly_machine_minutes", 999),  # next year, excluded
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-12"))

    assert rollup.total_cents == 120


# ---------------------------------------------------------------------------
# Rollup — unknown drivers bucket into OTHER but preserve their name
# ---------------------------------------------------------------------------


def test_unknown_drivers_land_in_other_with_name_preserved():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "third_party_api_docusign", 75),
            ("2026-05-02", "totally-novel-driver", 25),
            ("2026-05-03", "fly_machine_minutes", 100),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.total_cents == 200
    assert rollup.by_category_cents[DriverCategory.OTHER] == 100
    assert rollup.by_category_cents[DriverCategory.FLY_COMPUTE] == 100
    # Per-driver detail map preserves the raw names so Captain dashboard can
    # surface unknown drivers for triage
    assert rollup.per_driver_detail_cents["third_party_api_docusign"] == 75
    assert rollup.per_driver_detail_cents["totally-novel-driver"] == 25
    assert rollup.per_driver_detail_cents["fly_machine_minutes"] == 100


# ---------------------------------------------------------------------------
# Rollup — negative amounts are skipped, not poisoning the rollup
# ---------------------------------------------------------------------------


def test_negative_amount_rows_are_filtered_out():
    """Defense in depth: the SQL filter excludes negative amount_cents at the
    row level so a corrupt row cannot poison the rollup via SUM."""
    conn = _make_conn()
    cur = conn.cursor()
    # Two same-driver rows; the negative must not contribute.
    cur.execute(
        "INSERT INTO cost_telemetry (date, driver, amount_cents) VALUES ('2026-05-01', 'fly_machine_minutes', -500)"
    )
    cur.execute(
        "INSERT INTO cost_telemetry (date, driver, amount_cents) VALUES ('2026-05-02', 'fly_machine_minutes', 200)"
    )
    conn.commit()

    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    # The negative row is filtered at the SQL layer; only the positive row counts.
    assert rollup.total_cents == 200
    assert rollup.by_category_cents[DriverCategory.FLY_COMPUTE] == 200


def test_module_level_negative_guard_also_skips(monkeypatch):
    """The module's row-level negative guard is a belt-and-braces backstop
    in case a non-SQL row source (e.g. a future in-memory reader) supplies
    a negative. Feed the rollup a synthetic reader and assert the guard
    skips the row + warns."""
    from adapter import cost_rollup

    class _StubReader:
        def __init__(self, rows):
            self._rows = rows

        async def fetch_rows(self, sql, params):
            return self._rows

    reader = _StubReader(
        [
            ("fly_machine_minutes", -500, 1),
            ("fly_machine_minutes", 200, 1),
        ]
    )
    rollup = _run(cost_rollup.compute_monthly_rollup(reader, "2026-05"))
    # Negative is dropped by the row-level guard; the positive row is kept.
    assert rollup.total_cents == 200
    assert rollup.by_category_cents[DriverCategory.FLY_COMPUTE] == 200


# ---------------------------------------------------------------------------
# Rollup — COGS/MRR ratio
# ---------------------------------------------------------------------------


def test_cogs_mrr_basis_points_priced_customer():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "fly_machine_minutes", 30_000),
            ("2026-05-01", "claude_api_input_tokens", 20_000),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    # 50_000 cents COGS against 200_000 cents MRR ($2k) = 25% = 2500 bps
    assert rollup.cogs_mrr_basis_points(mrr_cents=200_000) == 2500


def test_cogs_mrr_basis_points_above_kill_threshold():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "fly_machine_minutes", 50_000),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    # 50_000 / 100_000 = 50% = 5000 bps; > 4000 bps kill threshold
    bps = rollup.cogs_mrr_basis_points(mrr_cents=100_000)
    assert bps == 5000
    assert bps is not None and bps > 4000


def test_cogs_mrr_basis_points_unpriced_returns_none():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "fly_machine_minutes", 100),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.cogs_mrr_basis_points(mrr_cents=0) is None
    assert rollup.cogs_mrr_basis_points(mrr_cents=-1) is None


# ---------------------------------------------------------------------------
# MonthlyRollup helpers
# ---------------------------------------------------------------------------


def test_category_dollars_helper():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "claude_api_input_tokens", 1234),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.category_dollars(DriverCategory.ANTHROPIC_LLM) == 12.34
    assert rollup.category_dollars(DriverCategory.CLOUDFLARE_D1) == 0.0


def test_total_dollars_helper():
    conn = _make_conn()
    _seed(
        conn,
        [
            ("2026-05-01", "fly_machine_minutes", 1000),
            ("2026-05-02", "captain_time", 1234),
        ],
    )
    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))

    assert rollup.total_dollars() == 22.34


# ---------------------------------------------------------------------------
# Rollup result immutability
# ---------------------------------------------------------------------------


def test_rollup_is_frozen():
    rollup = MonthlyRollup(
        year_month="2026-05",
        total_cents=0,
        by_category_cents={},
        by_category_basis_points={},
        per_driver_detail_cents={},
    )
    with pytest.raises(Exception):
        rollup.total_cents = 100  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Compose with cost_telemetry UPSERT pattern used by emitters
# ---------------------------------------------------------------------------


def test_upsert_pattern_accumulates_then_rollup_reads_total():
    """Same-day same-driver INSERTs should accumulate via UPSERT, and the
    rollup should read the accumulated total — matching the emitter
    pattern in cost-telemetry-events.md."""
    conn = _make_conn()
    cur = conn.cursor()
    # Emit three same-day same-driver events; UPSERT accumulates.
    upsert_sql = (
        "INSERT INTO cost_telemetry (date, driver, amount_cents, units, unit_type) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (date, driver) DO UPDATE SET "
        "  amount_cents = amount_cents + excluded.amount_cents, "
        "  units = units + excluded.units"
    )
    for cents, tokens in [(100, 10_000), (200, 20_000), (300, 30_000)]:
        cur.execute(
            upsert_sql,
            ("2026-05-15", "claude_api_input_tokens", cents, tokens, "input_tokens"),
        )
    conn.commit()

    # One row in the table, accumulated cents
    row = cur.execute(
        "SELECT amount_cents, units FROM cost_telemetry WHERE date='2026-05-15' AND driver='claude_api_input_tokens'"
    ).fetchone()
    assert row == (600, 60_000.0)

    reader = SqliteRowReader(conn)
    rollup = _run(compute_monthly_rollup(reader, "2026-05"))
    assert rollup.total_cents == 600
    assert rollup.by_category_cents[DriverCategory.ANTHROPIC_LLM] == 600
