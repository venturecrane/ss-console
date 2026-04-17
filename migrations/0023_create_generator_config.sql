-- Migration 0023: generator_config
--
-- Per-org, per-pipeline configuration for the 4 lead-gen workers
-- (new_business, job_monitor, review_mining, social_listening).
-- Replaces hardcoded values in worker source so the admin can tune
-- targeting without a code deploy.
--
-- `config_json` is a JSON blob parsed through a hand-written validator
-- at read time. Validation failure returns defaults + a parseError that
-- the UI surfaces as a yellow banner — never silently reverts.
--
-- `last_run_at` / `last_run_signals_count` / `last_run_error` are populated
-- by the worker at the end of every invocation. This is the authoritative
-- liveness indicator (not cron schedule derivation).

CREATE TABLE IF NOT EXISTS generator_config (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL CHECK (pipeline IN (
    'new_business', 'job_monitor', 'review_mining', 'social_listening'
  )),
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL,
  last_run_at TEXT,
  last_run_signals_count INTEGER,
  last_run_error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, pipeline)
);

CREATE INDEX IF NOT EXISTS idx_generator_config_org_pipeline
  ON generator_config(org_id, pipeline);
