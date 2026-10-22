-- Rollback for 0135: narrow the fleet_alert_state CHECK back to the 0133
-- vocabulary. Rows for the four drift conditions are dropped by the copy
-- filter; they cannot satisfy the narrowed CHECK. Running this while a worker
-- that still emits them is deployed reintroduces the 0116 defect: the page
-- sends, the row is rejected, and it pages again every tick.
--
-- Manual-only; coordinate with Captain. D1 wraps this file in one atomic
-- transaction.

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
FROM fleet_alert_state
WHERE condition NOT IN ('image_drift', 'image_unknown', 'config_drift', 'skills_drift');

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
