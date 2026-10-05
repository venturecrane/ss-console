-- Rollback for 0120: drop the shortfall ledger and the four added columns.
-- NOT fail-soft. The fleet-alerts Worker's one fleet_status SELECT names the
-- shortfall columns and its alert-state read names alert_message_id, and
-- SQLite fails a statement on an unknown column: running this while that
-- Worker (or the web Worker's heartbeat upsert) is deployed stops EVERY pager
-- and every heartbeat write. Roll the Workers back first, then run this.
--
-- Manual-only; coordinate with Captain.

DROP INDEX IF EXISTS idx_operator_shortfalls_unnotified;
DROP TABLE IF EXISTS operator_shortfalls;
ALTER TABLE fleet_alert_state DROP COLUMN alert_message_id;
ALTER TABLE fleet_status DROP COLUMN shortfalls_json;
ALTER TABLE fleet_status DROP COLUMN shortfalls_last_ts;
ALTER TABLE fleet_status DROP COLUMN shortfalls;
