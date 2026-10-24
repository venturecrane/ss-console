-- 0140: a seat whose signed spec does not verify pages
--
-- 0139 stores the seat-spec verifier's verdict each seat ships on its
-- heartbeat. The fleet-alerts worker now evaluates one standing condition from
-- it and the published pointer (workers/fleet-alerts/src/spec.ts):
--
--   spec_unverified  the seat reports a spec that does not verify: a bad
--                    signature or digest, a key its image does not carry, a
--                    spec for another seat, a missing spec object, an unmet
--                    requirement, or a verifier that stopped reporting; or the
--                    spec it verified is not the published pointer's, more
--                    than 15 minutes after that pointer was written. A seat
--                    verifying the published spec resolves it. NULL spec_json
--                    holds.
--
-- The rebuild admits it. Same full-table copy as 0138 <- 0135 <- 0133 <- ...,
-- because SQLite cannot ALTER a CHECK; every existing row stays valid. A new
-- condition without this widening is a REJECTED row: the worker emails first and
-- records second, so it would page every two minutes forever.
--
-- Deploy ordering is safe by construction: deploy.yml applies migrations
-- before the worker deploys.
--
-- Manual-only rollback at migrations/rollbacks/0140_spec_unverified_condition_down.sql.

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
        'rollout_failed',
        'image_drift','image_unknown','config_drift','skills_drift',
        'selftest_failing',
        'spec_unverified'
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
  -- rollout_failed. NULL on every other condition, spec_unverified included.
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
