-- Rollback for 0109: drop the three send-refusal columns and narrow the
-- fleet_alert_state CHECK back to the 0107 vocabulary, dropping the
-- last_seen_marker column with it. Rows for the 'send_refused' condition are
-- dropped by the copy filter -- they cannot satisfy the narrowed CHECK, and
-- their only state was the marker this file removes.
--
-- Manual-only; coordinate with Captain. D1 wraps this file in one atomic
-- transaction. Note the cost of running it: every seat's paged-through marker
-- is lost, so re-applying 0109 afterwards pages once per seat that has a
-- send_refusals_last_ts, which is correct rather than convenient.

ALTER TABLE fleet_status DROP COLUMN send_refusals;
ALTER TABLE fleet_status DROP COLUMN send_refusals_last_ts;
ALTER TABLE fleet_status DROP COLUMN send_refusals_json;

CREATE TABLE fleet_alert_state_new (
  customer_slug   TEXT NOT NULL,
  condition       TEXT NOT NULL
    CHECK (
      condition IN (
        'heartbeat_red','hard_stop','scheduler_error','work_overdue',
        'connector_check_error','spec_control_unprovable','webhook_surface_unprovable',
        'gateway_loop_wedged','gateway_loop_unprovable','gateway_restarted',
        'gateway_supervisor_refusing','gateway_supervisor_inert'
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
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, condition)
);

INSERT INTO fleet_alert_state_new (
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
SELECT
  customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at
FROM fleet_alert_state
WHERE condition <> 'send_refused';

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
