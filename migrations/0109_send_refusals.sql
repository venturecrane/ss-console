-- 0109: refused and unsent escalations reach a human (ss#2547)
--
-- The incident (docs/runbooks/operator/incidents/2026-08-19-gate-muted-escalator.md,
-- evidence vfy_01M0N0NZBPQX6XADDVTZ5DEZXP): on 2026-08-19 the pilot seat's
-- deadline escalator woke with a court date seven days out, tried to email
-- scott@ five times between 14:00:45Z and 14:01:20Z, and every attempt was
-- refused by its own gates -- the em-dash marker once, the identifier gate on
-- `date` four times. On 2026-08-20 it woke with five needs-you items and did
-- not attempt a send at all. Each refusal was an audit row on the seat's
-- volume that nobody was watching, so from the outside "refused", "did not
-- try", and "nothing to report" produced the identical observation: silence.
-- The Captain noticed on 2026-08-22, three days later.
--
-- Twice before, the same shape: 2026-08-04 to 2026-08-09 the voice-spec gate
-- refused nine sends by the same routine (fixed under ss#2234), and on
-- 2026-08-13 the send tool itself raised on every call (ss#2348). Three times
-- a newly shipped gate silenced the one routine whose job is to reach a human.
--
-- EVENT-SHAPED, NOT CONDITION-SHAPED, and that is the whole design. A refusal
-- has no green state to transition back to: a seat that refused yesterday and
-- refused again today is not "still broken" in the level sense, it is two
-- separate things a human needed to know. Modelling it as a level would page
-- once and then go quiet through a week of daily refusals, and would eventually
-- send a RECOVERED email that means nothing. So the alerter compares a
-- monotonic marker instead:
--
--   send_refusals          count of refused-or-unsent events in the trailing
--                          24h. 0 is a REAL value and the load-bearing one --
--                          it is what distinguishes a seat whose sends are
--                          landing from a seat that cannot answer. NULL means
--                          unreported (an overlay without the field), and the
--                          pager holds.
--   send_refusals_last_ts  ISO-8601 of the newest event of either kind. This
--                          is the marker the pager compares; NULL is a hold.
--   send_refusals_json     the newest few events verbatim from the ledger
--                          (ts, routine, tool, kind, reason). The reason is
--                          the ledger's own error type, never a value from a
--                          client record.
--
-- All three are stored with COALESCE at ingest, unlike the 0107 alert fields:
-- overwriting a known marker with an absence would re-page the whole backlog
-- the next time the seat reported, which is the opposite of what a marker is
-- for. See src/pages/api/internal/heartbeat.ts.
--
-- The fleet_alert_state rebuild adds one condition and one column. Nothing
-- FK-references this table; the copy keeps every existing row (open rows in
-- prod today stay valid under the widened CHECK) and defaults the new column
-- to NULL. Same full-table rebuild as 0107 <- 0106 <- 0104 <- 0103 <- 0094,
-- because SQLite cannot ALTER a CHECK. Deploy ordering is safe by
-- construction: migrations apply before the worker deploy, and every
-- pre-existing condition value remains valid throughout.
--
-- `last_seen_marker` is the send_refused row's whole state. The row is written
-- with status='resolved' the moment it pages, deliberately: an event that is
-- never `open` can never be reported as a stale hold and can never emit a
-- RECOVERED notice.
--
-- Manual-only rollback at migrations/rollbacks/0109_send_refusals_down.sql.

ALTER TABLE fleet_status ADD COLUMN send_refusals INTEGER;
ALTER TABLE fleet_status ADD COLUMN send_refusals_last_ts TEXT;
ALTER TABLE fleet_status ADD COLUMN send_refusals_json TEXT;

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
  -- The newest send_refusals_last_ts this seat has already been paged for.
  -- NULL on every other condition, and on a send_refused row that has never
  -- paged. Compared as an ISO-8601 string, which sorts chronologically.
  last_seen_marker TEXT,
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
