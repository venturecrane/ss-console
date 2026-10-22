-- Rollback for 0132: drop audit_write_failures_rose_at. NOT fail-soft: the web
-- Worker's heartbeat upsert and the fleet-alerts Worker's read both name the
-- column, and SQLite fails a statement on an unknown column, so running this
-- while either Worker is deployed stops every heartbeat write and every alert
-- read. Roll both Workers back first, then run this.
--
-- What it costs: the time each seat last lost a row. The next rise sets it again.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN audit_write_failures_rose_at;
