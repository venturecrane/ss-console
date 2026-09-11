-- 0116: admit 'edge_down' into fleet_alert_state's condition CHECK.
--
-- The defect (found 2026-09-11 while proving #2775's alert path): the pager's
-- outside-in probe of the web Worker (migration 0115, wave 8.2 of the 09-10
-- review) opens its alert as a fleet_alert_state row under the 'edge_down'
-- condition, and 0115 never widened this table's CHECK to accept it. The
-- pager sends the email FIRST and records the open row SECOND, so the third
-- failed probe would have paged, the INSERT would have been rejected, nothing
-- would have been recorded, and the next tick would have paged again: one
-- email every two minutes, and never a recovery notice. The worker's own
-- tests run against a fake database and the edge-poll tests apply 0115
-- alone, so nothing exercised the real constraint;
-- tests/fleet-alert-conditions-migrated.test.ts now applies the whole chain
-- and inserts every condition the worker can emit.
--
-- SQLite cannot ALTER a CHECK, so this is the full-table rebuild copied from
-- 0109 (itself from 0107, 0106, 0104, 0103, 0094, 0093): identical columns,
-- identical PK, last_seen_marker carried across, and 'edge_down' added to the
-- IN list. Every pre-existing condition value stays valid throughout.
--
-- Ordering is safe by construction: deploy.yml applies migrations before the
-- worker deploys, and the worker already writes this condition.

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
