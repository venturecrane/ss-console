-- Migration 0024: events
--
-- Site-wide event instrumentation for the marketing surface (apex
-- `smd.services`). Captures page views and CTA clicks so the persona
-- review can reconstruct conversion paths (parent epic #483, child #488).
--
-- Scope notes:
--   - Marketing-only. NOT scoped by `org_id` or `entity_id` — these are
--     anonymous visitors, not tenant data. Admin/portal surfaces do NOT
--     emit events (see src/components/EventsTracker.astro and middleware
--     subdomain rules).
--   - No PII. `user_agent` and `referrer` are behavioural signals only;
--     `path` is scrubbed of query strings before insert.
--   - `metadata` is a free-form JSON blob for event-specific payload
--     (e.g. `{"cta":"home-primary-cta","href":"/book"}`).
--   - `country` is derived from `request.cf.country` at ingest — no IP
--     storage.
--
-- Schema is intentionally additive: no foreign keys, no CHECK constraints
-- on event_name, indexes only on session/path/event for the dashboard
-- aggregate queries in src/pages/admin/analytics/index.astro.
--
-- Rollback is a clean DROP — see the `-- down` section at the bottom.

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  event_name TEXT NOT NULL,
  path TEXT,
  ts INTEGER NOT NULL,
  metadata TEXT,
  user_agent TEXT,
  referrer TEXT,
  country TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch() * 1000)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_path_ts ON events(path, ts);
CREATE INDEX IF NOT EXISTS idx_events_name_ts ON events(event_name, ts);

-- down
-- DROP INDEX IF EXISTS idx_events_name_ts;
-- DROP INDEX IF EXISTS idx_events_path_ts;
-- DROP INDEX IF EXISTS idx_events_session;
-- DROP TABLE IF EXISTS events;
