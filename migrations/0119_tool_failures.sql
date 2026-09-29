-- 0119: a watched tool that keeps failing on a seat reaches a human (ss#2793 follow-on)
--
-- The gap (2026-09-29, the open-house voice path): a rostered real estate
-- agent emails a voice memo to their seat, the transcriber refuses, and the
-- agent reads "could not transcribe" in their own thread. Nobody at SMD
-- knows. The sticky-stop ladder halts a seat at eight tool failures in ten
-- minutes and pages nothing before that; send_refused (0109) watches only
-- cron-turn external sends; connector_down watches only MCP servers. No
-- per-tool outcome leaves the Machine in real time, so three failures a day
-- of the one tool a person depends on is invisible from here.
--
-- The overlay (hermes-smd-overlay#401) now ships `tool_failures` on the
-- heartbeat: per watched tool (voice_note_transcribe, record_store_write),
-- the run of consecutive non-ok outcomes ending at the tool's newest call in
-- the trailing day, read from the seat's own audit ledger. Same shape as the
-- connectors map, and the same discipline here:
--
--   tool_failures_json   {"<tool>": {consecutive_failures, first_error_ts,
--                        last_error_ts, last_error} | {consecutive_failures:
--                        0, last_ok_ts}}. Plain overwrite at ingest,
--                        INCLUDING back to NULL: a stale run must never
--                        outlive the beat that reported it. NULL = the seat
--                        cannot read its ledger (hold); {} = watched, nothing
--                        ran today (every key holds); a 0 entry resolves.
--
-- The fleet_alert_state rebuild admits `tool_failing:<tool>`. Same full-table
-- copy as 0116 <- 0109 <- 0107 <- 0106 <- 0104 <- 0103 <- 0094, because SQLite
-- cannot ALTER a CHECK; every existing row stays valid. A new condition
-- without this widening is a REJECTED row: the worker emails first and
-- records second, so it would page every two minutes forever (what
-- 'edge_down' did between #2775 and 0116).
--
-- Deploy ordering is safe by construction: deploy.yml applies migrations
-- before the worker deploys, and the worker's SELECT names the new column.
--
-- Manual-only rollback at migrations/rollbacks/0119_tool_failures_down.sql.

ALTER TABLE fleet_status ADD COLUMN tool_failures_json TEXT;

CREATE TABLE fleet_alert_state_new (
  customer_slug   TEXT NOT NULL,
  condition       TEXT NOT NULL
    CHECK (
      condition IN (
        'heartbeat_red','hard_stop','scheduler_error','work_overdue',
        'connector_check_error','spec_control_unprovable','webhook_surface_unprovable',
        'gateway_loop_wedged','gateway_loop_unprovable','gateway_restarted',
        'gateway_supervisor_refusing','gateway_supervisor_inert',
        'send_refused',
        'edge_down'
      )
      OR condition LIKE 'connector_down:%'
      OR condition LIKE 'connector_token_expiring:%'
      OR condition LIKE 'spec_control_broken:%'
      OR condition LIKE 'webhook_surface_missing:%'
      OR condition LIKE 'tool_failing:%'
    ),
  status          TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  opened_at       TEXT NOT NULL,
  resolved_at     TEXT,
  last_alert_id   TEXT,
  -- The newest send_refusals_last_ts this seat has already been paged for
  -- (0109). NULL on every other condition.
  last_seen_marker TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, condition)
);

INSERT INTO fleet_alert_state_new (
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at)
SELECT
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at
FROM fleet_alert_state;

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
