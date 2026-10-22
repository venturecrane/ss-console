-- 0133: a failed fleet rollout reaches a human the same minute
--
-- The gap (2026-10-20 -> 2026-10-21): captain-mark's rollout of
-- img-202610202216-41f5627a was refused at preflight ("secrets are STAGED on
-- the seat and this release does not declare them: TELEGRAM_BOT_TOKEN"). The
-- row sat in fleet_rollouts as `failed`. No release could complete its last
-- ring after that, so every seat's reprovision refused, and nobody knew until
-- a reprovision tried a day later. Nothing reads fleet_rollouts for alerting.
--
-- The fleet-alerts worker now pages on a MARKER ADVANCE over fleet_rollouts
-- rows that ended failed / rolled_back / tooling_error (workers/fleet-alerts/
-- src/rollout-failed.ts), one fleet_alert_state row per seat under condition
-- `rollout_failed`, its last_seen_marker the newest finished_at already paged,
-- written status='resolved' permanently (the send_refused shape, 0109).
--
-- The rebuild admits `rollout_failed`. Same full-table copy as 0119 <- 0116 <-
-- 0109 <- 0107 <- 0106 <- 0104 <- 0103 <- 0094, because SQLite cannot ALTER a
-- CHECK; every existing row stays valid. A new condition without this widening
-- is a REJECTED row: the worker emails first and records second, so it would
-- page every two minutes forever (what 'edge_down' did between #2775 and 0116).
--
-- Deploy ordering is safe by construction: deploy.yml applies migrations
-- before the worker deploys.
--
-- Manual-only rollback at migrations/rollbacks/0133_rollout_failed_condition_down.sql.

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
        'edge_down',
        'rollout_failed'
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
  -- The newest marker this seat has already been paged for: send_refusals_last_ts
  -- (0109) for send_refused, fleet_rollouts.finished_at (0133) for
  -- rollout_failed. NULL on every other condition.
  last_seen_marker TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  -- The Message-ID of the alert email, so its RECOVERED notice and reminders
  -- thread under it (0120). Carried through this rebuild.
  alert_message_id TEXT,
  PRIMARY KEY (customer_slug, condition)
);

INSERT INTO fleet_alert_state_new (
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at, alert_message_id)
SELECT
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at,
  alert_message_id
FROM fleet_alert_state;

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
