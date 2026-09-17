-- Rollback for 0118_alert_source_obligation.sql — narrows
-- cost_anomaly_alerts.source back to the pre-obligation vocabulary.
--
-- WHAT BREAKS IF YOU RUN THIS WHILE THE CODE IS STILL DEPLOYED:
-- scripts/ci-reconcile-obligations.ts writes source='obligation' rows and will
-- fail its INSERT against the narrowed CHECK, so the reconciler exits non-zero
-- and its workflow goes red. That is the loud failure, and it is preferable to
-- the alternative: overdue client work silently ceasing to reach the Captain.
--
-- DATA LOSS: every existing obligation alert row is DELETED by the filtered
-- copy below. It has to be — a row whose source the new CHECK forbids cannot
-- be carried across. The underlying obligations in client_obligations are
-- untouched, and the next reconcile run re-raises anything still overdue, so
-- the loss is the alert history, not the work.

DELETE FROM cost_anomaly_alerts WHERE source = 'obligation';

-- Same FK-bearing rebuild shape as the up-migration; same pragma.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE cost_anomaly_alerts_old (
  entity_id            TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  customer_slug        TEXT NOT NULL,
  alert_date           TEXT NOT NULL,
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
    CHECK (source IN ('cost','sentry','healthchecks','audit_integrity')),
  summary              TEXT,
  details_json         TEXT,
  notified_at          TEXT,
  PRIMARY KEY (entity_id, alert_date, driver)
);

INSERT INTO cost_anomaly_alerts_old (
  entity_id, customer_slug, alert_date, driver, daily_cents, rolling_avg_cents,
  ratio_bps, threshold_bps, detected_at, snoozed_until, acknowledged_at,
  acknowledged_by, source, summary, details_json, notified_at)
SELECT
  entity_id, customer_slug, alert_date, driver, daily_cents, rolling_avg_cents,
  ratio_bps, threshold_bps, detected_at, snoozed_until, acknowledged_at,
  acknowledged_by, source, summary, details_json, notified_at
FROM cost_anomaly_alerts;

DROP TABLE cost_anomaly_alerts;
ALTER TABLE cost_anomaly_alerts_old RENAME TO cost_anomaly_alerts;

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
