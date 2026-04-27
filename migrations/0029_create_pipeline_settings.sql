-- Migration 0029: pipeline_settings + pipeline_settings_audit
--
-- Per-org, per-pipeline tunable thresholds and caps. Replaces hardcoded
-- worker constants (`PAIN_THRESHOLD = 7`, `MAX_REVIEW_CHECKS = 200`,
-- `OUTSCRAPER_BUDGET_USD_PER_RUN = 1.0`) with admin-config so ops can tune
-- signal quality and cost guards without a code deploy. Closes issue #595.
--
-- Design notes:
--   - Flat key-value store keyed by (org_id, pipeline, key). One row per
--     tunable setting. Values are TEXT — workers parse to int/float at
--     read time. Keeps the schema cheap to extend (new tunable = new key,
--     no ALTER TABLE).
--   - Workers read settings at the top of each handler invocation, NOT at
--     module load. This means the next cron run picks up the latest admin
--     value without a worker restart. Fallback to hardcoded defaults if
--     a row is absent — graceful degradation when the table is empty.
--   - Settings registry (allowed keys + types + defaults + ranges) lives
--     in src/lib/db/pipeline-settings.ts. The migration intentionally does
--     NOT enforce key whitelist via CHECK constraints — easier to add
--     tunables without coupling schema changes to code rollouts. Validation
--     is enforced in the DAL on write.
--   - The audit table is append-only. Every admin change records pipeline,
--     key, old_value, new_value, actor (user_id) and timestamp. This is
--     the audit log for compliance / "who turned the threshold to 3?"
--     forensics. Index supports the per-pipeline history view.

CREATE TABLE IF NOT EXISTS pipeline_settings (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL CHECK (pipeline IN (
    'new_business', 'job_monitor', 'review_mining', 'social_listening'
  )),
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by TEXT,
  UNIQUE(org_id, pipeline, key)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_settings_org_pipeline
  ON pipeline_settings(org_id, pipeline);

CREATE TABLE IF NOT EXISTS pipeline_settings_audit (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL,
  key TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  actor_user_id TEXT,
  actor_email TEXT,
  changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_settings_audit_org_pipeline_changed
  ON pipeline_settings_audit(org_id, pipeline, changed_at DESC);

-- down
-- DROP INDEX IF EXISTS idx_pipeline_settings_audit_org_pipeline_changed;
-- DROP TABLE IF EXISTS pipeline_settings_audit;
-- DROP INDEX IF EXISTS idx_pipeline_settings_org_pipeline;
-- DROP TABLE IF EXISTS pipeline_settings;
