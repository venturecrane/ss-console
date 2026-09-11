-- 0107: gateway loop liveness + supervisor state alerting (ss#2488 part 2)
--
-- The defect (ss#2488): on 2026-08-20 the paying client's gateway event loop
-- wedged for 33 minutes and every liveness signal a human could see stayed
-- green. Fly's /health is a literal constant. The control-plane heartbeat this
-- table stores, and the healthchecks.io ping, are both emitted by the webhook
-- gate -- a separate process on the seat that never wedged. fleet-alerts'
-- work_overdue condition would have caught it, but the seat's crons were off for
-- go-live (ss#2332). Three instruments blind by construction, one by config.
--
-- Part 1 (#2502) made a wedged gateway restart itself, proven on hermes-scott
-- (vfy_01M0HEM5VM26XFVD8W4XW19AQF: wedge -> new pid in 5m05s, nobody involved).
-- It did not change what a human can see. This migration is the console half of
-- part 2: the gate (which survives a wedge) now ships the gateway's pulse age
-- and the part-1 supervisor's own state on the heartbeat, and this is where
-- they land.
--
-- Four columns, all nullable, all tri-state, all overwrite-including-NULL at
-- ingest (the 0093 discipline: a stale pinned verdict must never outlive the
-- signal that produced it):
--
--   gateway_loop_ok             1 the seat's check could look / 0 it could not
--                               (OUR blindness, paged on its own) / NULL unreported.
--   gateway_loop_age_seconds    seconds since the loop last beat. NULL while the
--                               seat's arming latch is closed (a stale beat from
--                               the PREVIOUS boot is on the persistent volume at
--                               every cold start), on a Hermes pin with no loop
--                               heartbeat at all (0.18 -- hermes-smd-staging today,
--                               vfy_01M0HBR1NZHSRMWSFPSQM32D1E), and inside the
--                               seat's post-boot suppression window. NULL = hold.
--   gateway_supervisor_state    armed | not-armed | inert | not-watching | refusing.
--                               Closed vocabulary, enforced at ingest. NULL on a
--                               pin without the supervisor.
--   gateway_restarts_last_hour  kill-ledger lines inside the last hour. The one
--                               field a restart cannot race: the age can be
--                               refreshed by the very restart that fixed the
--                               wedge before the 2-minute cron samples it; the
--                               ledger line is on the volume before the container
--                               dies, so the first beat after reboot carries it.
--
-- The CHECK rebuild adds five conditions. Nothing FK-references this table; the
-- copy keeps every existing row (there are open rows in prod today, and they stay
-- valid under the widened CHECK). Same full-table rebuild as 0106 <- 0104 <- 0103
-- <- 0094, because SQLite cannot ALTER a CHECK. Deploy ordering is safe by
-- construction: migrations apply before the worker deploy, and every
-- pre-existing condition value remains valid throughout.
--
-- Manual-only rollback at migrations/rollbacks/0107_gateway_loop_observability_down.sql.

ALTER TABLE fleet_status ADD COLUMN gateway_loop_ok INTEGER;
ALTER TABLE fleet_status ADD COLUMN gateway_loop_age_seconds INTEGER;
ALTER TABLE fleet_status ADD COLUMN gateway_supervisor_state TEXT;
ALTER TABLE fleet_status ADD COLUMN gateway_restarts_last_hour INTEGER;

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
FROM fleet_alert_state;

DROP TABLE fleet_alert_state;
ALTER TABLE fleet_alert_state_new RENAME TO fleet_alert_state;
