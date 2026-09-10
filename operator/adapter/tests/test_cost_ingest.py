"""Tests for operator/adapter/cost_ingest.py (issue #824).

Exercises the Anthropic ingest path against in-memory sqlite that
mirrors the cost_telemetry schema from migration 0001.

Coverage:
  - Pricing JSON files load and have the expected shape
  - Anthropic ingest: known model -> correct cents math
  - Anthropic ingest: unknown model -> rows written with cents=0 + warning
  - Anthropic ingest: zero-token result writes no rows
  - Anthropic ingest: source failure is captured in SourceIngestResult.ok=False
  - UPSERT accumulates on repeat: re-running same day adds to existing row
  - run_ingest_for_customer: aggregates the Anthropic source outcome

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_cost_ingest.py -v
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

# Allow running from repo root or from operator/.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from adapter.cost_ingest import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    CustomerIngestContext,
    IngestRunResult,
    _compute_anthropic_cents,
    ingest_anthropic_billing,
    load_anthropic_pricing,
    run_ingest_for_customer,
)


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
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_SQL)
    return conn


class _SqliteExecutor:
    """Minimal async-shim executor backed by sqlite3."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: list) -> None:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._conn.commit()


class _FakeAnthropicSource:
    def __init__(self, rows, raises: Exception | None = None) -> None:
        self._rows = rows
        self._raises = raises
        self.calls: list[tuple] = []

    async def fetch_daily_usage(self, api_key, day):
        self.calls.append((api_key, day))
        if self._raises:
            raise self._raises
        return self._rows


def _read_rows(conn: sqlite3.Connection) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(
        "SELECT date, driver, amount_cents, units, unit_type "
        "FROM cost_telemetry ORDER BY date, driver"
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Pricing loaders
# ---------------------------------------------------------------------------


def test_anthropic_pricing_loads():
    pricing = load_anthropic_pricing()
    assert "models" in pricing
    assert "claude-opus-4-7" in pricing["models"]
    entry = pricing["models"]["claude-opus-4-7"]
    assert entry["input_per_million_cents"] == 1500
    assert entry["output_per_million_cents"] == 7500


# ---------------------------------------------------------------------------
# Anthropic cents math
# ---------------------------------------------------------------------------


def test_compute_anthropic_cents_known_model():
    pricing = load_anthropic_pricing()
    # Opus 4.7: 1500 cents per M input, 7500 cents per M output
    # 2,000,000 input tokens -> 3000 cents; 1,000,000 output -> 7500 cents
    in_cents, out_cents, warn = _compute_anthropic_cents(
        "claude-opus-4-7", 2_000_000, 1_000_000, pricing
    )
    assert in_cents == 3000
    assert out_cents == 7500
    assert warn is None


def test_compute_anthropic_cents_unknown_model():
    pricing = load_anthropic_pricing()
    in_cents, out_cents, warn = _compute_anthropic_cents(
        "claude-unknown-99", 1_000_000, 1_000_000, pricing
    )
    assert in_cents == 0
    assert out_cents == 0
    assert warn is not None
    assert "claude-unknown-99" in warn


def test_compute_anthropic_cents_integer_floor():
    pricing = load_anthropic_pricing()
    # 1 input token at 1500 cents/million = floor(1500/1_000_000) = 0
    in_cents, out_cents, _ = _compute_anthropic_cents(
        "claude-opus-4-7", 1, 0, pricing
    )
    assert in_cents == 0
    assert out_cents == 0


# ---------------------------------------------------------------------------
# Anthropic ingest
# ---------------------------------------------------------------------------


def test_ingest_anthropic_writes_two_driver_rows():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    source = _FakeAnthropicSource(
        [
            ("claude-opus-4-7", 1_000_000, 500_000),
            ("claude-sonnet-4-6", 2_000_000, 1_000_000),
        ]
    )

    result = asyncio.run(
        ingest_anthropic_billing(
            executor,
            source,
            "fake-key",
            date(2026, 5, 22),
        )
    )

    assert result.ok
    assert result.rows_written == 2
    # Opus: 1500 in + 3750 out = 5250
    # Sonnet: 600 in + 1500 out = 2100
    # Total: 7350
    assert result.cents_written == 5250 + 2100

    rows = _read_rows(conn)
    assert len(rows) == 2
    in_row = next(r for r in rows if r[1] == "claude_api_input_tokens")
    out_row = next(r for r in rows if r[1] == "claude_api_output_tokens")
    assert in_row[0] == "2026-05-22"
    assert in_row[2] == 1500 + 600  # 2100 cents
    assert in_row[3] == 3_000_000.0
    assert in_row[4] == "input_tokens"
    assert out_row[2] == 3750 + 1500  # 5250 cents
    assert out_row[3] == 1_500_000.0
    assert out_row[4] == "output_tokens"


def test_ingest_anthropic_unknown_model_writes_zero_cents_with_units():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    source = _FakeAnthropicSource(
        [
            ("claude-mystery-7", 1_000_000, 500_000),
        ]
    )

    result = asyncio.run(
        ingest_anthropic_billing(
            executor,
            source,
            "fake-key",
            date(2026, 5, 22),
        )
    )

    assert result.ok
    assert result.reason is not None
    assert "claude-mystery-7" in result.reason
    rows = _read_rows(conn)
    # Both rows still written (units preserved for triage); cents are 0.
    assert len(rows) == 2
    for row in rows:
        assert row[2] == 0  # amount_cents


def test_ingest_anthropic_zero_tokens_writes_no_rows():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    source = _FakeAnthropicSource([])

    result = asyncio.run(
        ingest_anthropic_billing(
            executor,
            source,
            "fake-key",
            date(2026, 5, 22),
        )
    )

    assert result.ok
    assert result.rows_written == 0
    assert _read_rows(conn) == []


def test_ingest_anthropic_source_failure_returns_not_ok():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    source = _FakeAnthropicSource([], raises=RuntimeError("HTTP 503"))

    result = asyncio.run(
        ingest_anthropic_billing(
            executor,
            source,
            "fake-key",
            date(2026, 5, 22),
        )
    )

    assert not result.ok
    assert "503" in (result.reason or "")
    assert _read_rows(conn) == []


# ---------------------------------------------------------------------------
# UPSERT accumulation
# ---------------------------------------------------------------------------


def test_upsert_accumulates_on_repeat():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    source = _FakeAnthropicSource(
        [("claude-opus-4-7", 1_000_000, 1_000_000)]
    )

    asyncio.run(
        ingest_anthropic_billing(executor, source, "k", date(2026, 5, 22))
    )
    # Second run same day — UPSERT must accumulate
    asyncio.run(
        ingest_anthropic_billing(executor, source, "k", date(2026, 5, 22))
    )

    rows = _read_rows(conn)
    in_row = next(r for r in rows if r[1] == "claude_api_input_tokens")
    out_row = next(r for r in rows if r[1] == "claude_api_output_tokens")
    # Each run wrote 1500 in_cents + 7500 out_cents; after 2 runs: 3000 / 15000
    assert in_row[2] == 3000
    assert out_row[2] == 15000
    # Units doubled too
    assert in_row[3] == 2_000_000.0
    assert out_row[3] == 2_000_000.0


# ---------------------------------------------------------------------------
# Per-customer run
# ---------------------------------------------------------------------------


def test_run_ingest_aggregates_anthropic_source():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    ctx = CustomerIngestContext(
        customer_slug="acme",
        anthropic_api_key="ak",
        executor=executor,
    )
    anthropic_src = _FakeAnthropicSource([("claude-opus-4-7", 100, 200)])

    result = asyncio.run(
        run_ingest_for_customer(ctx, anthropic_src, day=date(2026, 5, 22))
    )

    assert isinstance(result, IngestRunResult)
    sources = [s.source for s in result.sources]
    assert "anthropic_billing" in sources


def test_run_ingest_anthropic_failure_is_captured():
    conn = _make_conn()
    executor = _SqliteExecutor(conn)
    ctx = CustomerIngestContext(
        customer_slug="acme",
        anthropic_api_key="ak",
        executor=executor,
    )
    anthropic_src = _FakeAnthropicSource([], raises=RuntimeError("HTTP 503"))

    result = asyncio.run(
        run_ingest_for_customer(ctx, anthropic_src, day=date(2026, 5, 22))
    )

    sources = {s.source: s for s in result.sources}
    assert not sources["anthropic_billing"].ok
    assert result.any_failures is True


def test_run_ingest_requires_executor():
    ctx = CustomerIngestContext(
        customer_slug="acme",
        anthropic_api_key="ak",
        executor=None,
    )
    with pytest.raises(ValueError):
        asyncio.run(
            run_ingest_for_customer(
                ctx,
                _FakeAnthropicSource([]),
                day=date(2026, 5, 22),
            )
        )


# ---------------------------------------------------------------------------
# Pricing coverage: every fleet-declared model must be priced
# ---------------------------------------------------------------------------


def test_every_declared_model_has_a_pricing_entry():
    """Every `model` / `escalation_model` declared in any customer.yaml must
    have an entry in anthropic_pricing.json.

    Regression: the fleet's two-tier seats declared `escalation_model:
    claude-opus-4-8` while the pricing table only knew opus-4-7, so
    `_compute_anthropic_cents` costed all escalation-tier (Opus) spend at
    0 cents with only a log warning. The pricing JSON's _meta note claimed
    this test existed; now it does.
    """
    import yaml

    pricing = load_anthropic_pricing()
    priced = set(pricing["models"].keys())

    customers_dir = _HERE.parents[2] / "customers"
    declared: dict[str, str] = {}
    for cfg in sorted(customers_dir.glob("*/customer.yaml")):
        doc = yaml.safe_load(cfg.read_text())
        if not isinstance(doc, dict):
            continue
        for key in ("model", "escalation_model"):
            value = doc.get(key)
            if isinstance(value, str) and value:
                declared[value] = f"{cfg.parent.name}:{key}"

    assert declared, "no models declared in any customer.yaml — glob broken?"
    missing = {m: where for m, where in declared.items() if m not in priced}
    assert not missing, (
        f"models declared in customer.yaml but absent from "
        f"anthropic_pricing.json (their spend would be costed at 0): {missing}"
    )
