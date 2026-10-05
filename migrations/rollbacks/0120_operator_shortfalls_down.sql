-- Rollback for 0120: drop the shortfall ledger and the four added columns.
-- Running this while a worker that reads operator_shortfalls is deployed
-- makes every tick log a failed shortfall query (fail-soft; nothing pages).
--
-- Manual-only; coordinate with Captain.

DROP INDEX IF EXISTS idx_operator_shortfalls_unnotified;
DROP TABLE IF EXISTS operator_shortfalls;
ALTER TABLE fleet_alert_state DROP COLUMN alert_message_id;
ALTER TABLE fleet_status DROP COLUMN shortfalls_json;
ALTER TABLE fleet_status DROP COLUMN shortfalls_last_ts;
ALTER TABLE fleet_status DROP COLUMN shortfalls;
