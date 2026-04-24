-- Worker heartbeats — monitoring table for cron-driven Workers.
--
-- Each Worker writes a row on every successful cron invocation.
-- Deploy heartbeat tooling reads this table to confirm crons are alive.
-- Old rows are pruned by the follow-up processor itself (retain 7 days).

CREATE TABLE worker_heartbeats (
  id          TEXT PRIMARY KEY,
  worker_name TEXT NOT NULL,
  ran_at      TEXT NOT NULL DEFAULT (datetime('now')),
  duration_ms INTEGER,
  summary     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_worker_heartbeats_worker_ran
  ON worker_heartbeats(worker_name, ran_at);
