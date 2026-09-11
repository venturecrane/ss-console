-- Rollback for 0116: narrow the fleet_alert_state CHECK back to the 0109
-- vocabulary. Rows for the 'edge_down' condition are dropped by the copy
-- filter; they cannot satisfy the narrowed CHECK. Running this while the
-- worker still polls (0115 in place, EDGE_POLL_TARGETS set) reintroduces the
-- defect 0116 fixed: a down edge pages every tick and never records.
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
        'send_refused'
      )
      OR condition LIKE 'connector_down:%'
      OR condition LIKE 'connector_token_expiring:%'
      OR condition LIKE 'spec_control_broken:%'
      OR condition LIKE 'webhook_surface_missing:%'
    ),
  status          TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  opened_at       TEXT NOT NULL,
  resolved_at     TEXT,
  last_alert_id   TEXT,
  last_seen_marker TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, condition)
);

INSERT INTO fleet_alert_state_new (
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at)
SELECT
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, last_seen_marker, updated_at
FROM fleet_alert_state
WHERE condition <> 'edge_down';

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
