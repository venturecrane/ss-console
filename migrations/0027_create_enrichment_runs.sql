-- Per-module enrichment-run state. Replaces the in-memory EnrichResult that
-- workers/admin endpoints discarded after every call, leaving us with no way
-- to see which modules ran for which entity (or why some — like Cactus
-- Creative Studio's review_synthesis — silently returned null and vanished).
--
-- Append-only. One row per (entity_id, module) per attempt.
--
-- status taxonomy:
--   running    — wrapper started a run; not yet completed.
--   succeeded  — module produced data and a context row was written.
--   no_data    — module ran cleanly but returned nothing useful (e.g.,
--                Outscraper found no place; Claude returned a non-JSON body).
--                Distinct from `succeeded` so we can tell "ran and worked"
--                from "ran and produced nothing" — that distinction is the
--                load-bearing fix for the silent-return bug.
--   skipped    — wrapper did not call the module body (missing input,
--                missing API key, lock contention).
--   failed    — module threw. error_message holds a truncated message; reason
--                holds a classified kind ('fetch_failed', 'parse_error',
--                'timeout', 'api_error', 'unknown').
--
-- input_fingerprint: optional SHA-256 (1KB-truncated) of the module's
-- assembled-context input. Recorded only for review_synthesis and
-- intelligence_brief — the modules whose output materially changes when
-- upstream context grows. Stored now; consumed (for stale-rerun decisions)
-- later if telemetry justifies.

CREATE TABLE enrichment_runs (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id),
  entity_id         TEXT NOT NULL,

  module            TEXT NOT NULL,
  status            TEXT NOT NULL CHECK (status IN (
    'running', 'succeeded', 'no_data', 'skipped', 'failed'
  )),
  reason            TEXT,
  error_message     TEXT,
  input_fingerprint TEXT,

  started_at        TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at      TEXT,
  duration_ms       INTEGER,

  triggered_by      TEXT NOT NULL,
  mode              TEXT NOT NULL CHECK (mode IN ('full', 'reviews-and-news', 'single')),
  context_entry_id  TEXT
);

CREATE INDEX idx_enrichment_runs_entity_module
  ON enrichment_runs(entity_id, module, started_at DESC);

CREATE INDEX idx_enrichment_runs_module_status
  ON enrichment_runs(module, status, started_at DESC);

CREATE INDEX idx_enrichment_runs_org
  ON enrichment_runs(org_id, started_at DESC);
