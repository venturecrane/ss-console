-- ============================================================================
-- Migration 0004: sticky-stop state (issue #843)
-- ============================================================================
--
-- The sticky-stop circuit breaker pins the agent's dispatch path when the
-- substrate observes runaway-loop signals: consecutive tool failures,
-- refusal cascades, time-budget overruns, or cost-threshold breaches. The
-- state machine itself lives at operator/safety_substrate/sticky_stop.py.
-- This migration adds the D1 row it persists to.
--
-- Source spec:    docs/specs/operator/sticky-stop.md
-- Applied by:     operator/adapter/run_migrations.py (invoked from
--                 bin/provision-customer.sh during customer provisioning)
-- Owns the row:   operator/safety_substrate/sticky_stop.py
--
-- Per-customer isolation: the table lives in the per-customer D1
-- (hermes-{slug}-d1) per ADR 0008. The `customer` column is the
-- customer_id slug, denormalized into the row for audit symmetry with the
-- machine's API surface. It does NOT lift cross-customer queries — the
-- D1 binding is still single-tenant per Hermes Machine (ADR 0009).
--
-- One row per (customer, persona). The composite primary key enforces
-- uniqueness without an extra index.
-- ============================================================================

CREATE TABLE sticky_stop_state (
  customer                          TEXT NOT NULL,                              -- customer_id slug; matches D1 binding
  persona                           TEXT NOT NULL,                              -- customer.yaml.personas[].slug
  level                             TEXT NOT NULL DEFAULT 'OK',                 -- 'OK' | 'WARN' | 'SOFT_STOP' | 'HARD_STOP'
  updated_at                        TEXT NOT NULL,                              -- ISO 8601 UTC
  reason                            TEXT,                                       -- human-readable condition snapshot at last transition
  condition                         TEXT,                                       -- 'consecutive_tool_failures' | 'refusal_cascade' | 'time_budget_exceeded' | 'cost_threshold' | 'captain_clear'

  -- Rolling counters. Persisted in the row so the machine survives
  -- restarts without losing failure history per safety invariant #4.
  consecutive_tool_failures         INTEGER NOT NULL DEFAULT 0,
  tool_failure_window_started_at    TEXT,                                       -- ISO 8601 UTC; NULL when no streak active
  refusal_count                     INTEGER NOT NULL DEFAULT 0,
  refusal_window_started_at         TEXT,                                       -- ISO 8601 UTC
  cost_cents_today                  INTEGER NOT NULL DEFAULT 0,
  cost_date                         TEXT,                                       -- YYYY-MM-DD; resets cost_cents_today on day rollover

  PRIMARY KEY (customer, persona)
);

-- Newest-first scan over all personas for dashboard surfacing. The
-- dashboard "is anything stuck?" indicator queries WHERE level != 'OK'
-- ORDER BY updated_at DESC.
CREATE INDEX idx_sticky_stop_active
  ON sticky_stop_state(updated_at DESC)
  WHERE level != 'OK';

-- Bump user_version. 0003 set it to 3; this is migration 4.
PRAGMA user_version = 4;
