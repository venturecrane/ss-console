-- Rollback for 0107: drop the four gateway-loop columns and narrow the
-- fleet_alert_state CHECK back to the 0106 vocabulary. Open rows for the five
-- 0107 conditions are dropped by the copy filter -- they cannot satisfy the
-- narrowed CHECK.
--
-- Manual-only; coordinate with Captain. D1 wraps this file in one atomic
-- transaction.

ALTER TABLE fleet_status DROP COLUMN gateway_loop_ok;
ALTER TABLE fleet_status DROP COLUMN gateway_loop_age_seconds;
ALTER TABLE fleet_status DROP COLUMN gateway_supervisor_state;
ALTER TABLE fleet_status DROP COLUMN gateway_restarts_last_hour;

CREATE TABLE fleet_alert_state_new (
  customer_slug   TEXT NOT NULL,
  condition       TEXT NOT NULL
    CHECK (
      condition IN ('heartbeat_red','hard_stop','scheduler_error','work_overdue','connector_check_error','spec_control_unprovable','webhook_surface_unprovable')
      OR condition LIKE 'connector_down:%'
      OR condition LIKE 'connector_token_expiring:%'
      OR condition LIKE 'spec_control_broken:%'
      OR condition LIKE 'webhook_surface_missing:%'
    ),
  status          TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  opened_at       TEXT NOT NULL,
  resolved_at     TEXT,
  last_alert_id   TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, condition)
);

INSERT INTO fleet_alert_state_new (
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
SELECT
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at
FROM fleet_alert_state
WHERE condition NOT IN (
  'gateway_loop_wedged','gateway_loop_unprovable','gateway_restarted',
  'gateway_supervisor_refusing','gateway_supervisor_inert'
);

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
