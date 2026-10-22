-- 0135: a seat that drifts from what was authored pages
--
-- The gap (2026-10-22): every seat reports image_ref, config_digest and
-- skills_version on its heartbeat (0129), and the system records what each seat
-- is supposed to run (the blessed fleet_releases row, R2
-- vaults/<slug>/customer.yaml, R2 vaults/<slug>/skills-pointer.json). Nothing
-- compared the two, so a seat on the wrong image, config or skills was found by
-- whoever next looked.
--
-- The fleet-alerts worker now evaluates four standing conditions per seat
-- (workers/fleet-alerts/src/drift.ts) through the usual open/resolve rows:
--   image_drift    on an older image after its rollout of the blessed release was done
--   image_unknown  on an image that matches no fleet_releases row
--   config_drift   customer.yaml digest differs from sha256 of the R2 object
--   skills_drift   skills_version differs from the seat's skills pointer
--
-- The rebuild admits them. Same full-table copy as 0133 <- 0119 <- 0116 <- ...,
-- because SQLite cannot ALTER a CHECK; every existing row stays valid. A new
-- condition without this widening is a REJECTED row: the worker emails first and
-- records second, so it would page every two minutes forever.
--
-- Deploy ordering is safe by construction: deploy.yml applies migrations
-- before the worker deploys.
--
-- Manual-only rollback at migrations/rollbacks/0135_drift_conditions_down.sql.

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
        'image_drift','image_unknown','config_drift','skills_drift'
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
  -- rollout_failed. NULL on every other condition, the 0135 drift four included.
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
