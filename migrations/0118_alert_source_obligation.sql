-- 0118: widen cost_anomaly_alerts.source to admit 'obligation' (ADR 0088).
--
-- WHY. The obligation register (0117) needs overdue work to reach the Captain
-- without him going to look for it. src/lib/admin/fleet-alerts.ts:17-22 states
-- the rule for this store: it is already multi-source, and a new source is a
-- WRITER, not a new surface. Writing here also buys the email for free —
-- workers/fleet-alerts/src/sink-notify.ts:57 selects
-- `WHERE notified_at IS NULL AND source != 'cost'`, sends to the ops inbox,
-- and stamps notified_at only after the send actually succeeded.
--
-- WHY A FULL REBUILD. SQLite cannot ALTER a CHECK constraint. This is the same
-- rebuild chain 0116 performed on fleet_alert_state, and the same one 0044
-- created here when it added the source column in the first place.
--
-- THE COLUMN LIST BELOW WAS READ OFF THE LIVE DATABASE, not reconstructed from
-- the migration chain: `SELECT sql FROM sqlite_master WHERE name =
-- 'cost_anomaly_alerts'` against ss-console-db --remote on 2026-09-17. Columns
-- added by later ALTERs (source/summary/details_json at 0044, notified_at at
-- 0095) land at the end of the physical column order, which a rebuild written
-- from the 0041 text alone would silently get wrong.
--
-- NOT REUSING an existing source value. 'audit_integrity' is the closest in
-- spirit and would need no migration, but an obligation alert is not an audit
-- finding: it derives a different severity, deep-links somewhere else, and
-- carries a different meaning for the Captain. Borrowing a source tag to dodge
-- a rebuild is how an alert feed stops meaning anything.
--
-- Written by: scripts/ci-reconcile-obligations.ts (obligation_overdue,
--             obligation_unverifiable, obligation_capture_gap).
-- Read by:    src/lib/admin/fleet-alerts.ts, workers/fleet-alerts/src/sink-notify.ts.

CREATE TABLE cost_anomaly_alerts_new (
  entity_id            TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  customer_slug        TEXT NOT NULL,
  alert_date           TEXT NOT NULL,
  -- Empty string is the all-drivers aggregate; obligation rows carry
  -- 'obligation:<obligation_id>:<condition>' so several obligations for one
  -- client on one day stay distinct rows under the composite key.
  driver               TEXT NOT NULL,
  daily_cents          INTEGER NOT NULL,
  rolling_avg_cents    INTEGER NOT NULL,
  ratio_bps            INTEGER NOT NULL,
  threshold_bps        INTEGER NOT NULL,
  detected_at          TEXT NOT NULL DEFAULT (datetime('now')),
  snoozed_until        TEXT,
  acknowledged_at      TEXT,
  acknowledged_by      TEXT REFERENCES users(id),
  source               TEXT NOT NULL DEFAULT 'cost'
    CHECK (source IN ('cost','sentry','healthchecks','audit_integrity','obligation')),
  summary              TEXT,
  details_json         TEXT,
  notified_at          TEXT,
  PRIMARY KEY (entity_id, alert_date, driver)
);

INSERT INTO cost_anomaly_alerts_new (
  entity_id, customer_slug, alert_date, driver, daily_cents, rolling_avg_cents,
  ratio_bps, threshold_bps, detected_at, snoozed_until, acknowledged_at,
  acknowledged_by, source, summary, details_json, notified_at)
SELECT
  entity_id, customer_slug, alert_date, driver, daily_cents, rolling_avg_cents,
  ratio_bps, threshold_bps, detected_at, snoozed_until, acknowledged_at,
  acknowledged_by, source, summary, details_json, notified_at
FROM cost_anomaly_alerts;

DROP TABLE cost_anomaly_alerts;
ALTER TABLE cost_anomaly_alerts_new RENAME TO cost_anomaly_alerts;

-- Recreated: a DROP TABLE takes EVERY index with it, not just the one this
-- migration cares about. All four were read off the live database
-- (`SELECT name, sql FROM sqlite_master WHERE type='index' AND
-- tbl_name='cost_anomaly_alerts'`, 2026-09-17) rather than recalled from the
-- migration chain. Losing idx_cost_anomaly_alerts_undelivered in particular
-- would degrade the email sink's scan silently — nothing would fail, the
-- query would just get slower as the table grows.
CREATE INDEX IF NOT EXISTS idx_cost_anomaly_alerts_open
  ON cost_anomaly_alerts(detected_at DESC)
  WHERE acknowledged_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cost_anomaly_alerts_entity
  ON cost_anomaly_alerts(entity_id, alert_date DESC);

CREATE INDEX IF NOT EXISTS idx_cost_anomaly_alerts_source_open
  ON cost_anomaly_alerts(source, detected_at DESC)
  WHERE acknowledged_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cost_anomaly_alerts_undelivered
  ON cost_anomaly_alerts(source, detected_at)
  WHERE notified_at IS NULL;
