-- Rollback for 0131: drop audit_write_failure_reasons_json. NOT fail-soft: the
-- web Worker's heartbeat upsert names the column, and SQLite fails a statement
-- on an unknown column, so running this while that Worker is deployed stops
-- every heartbeat write. Roll the Worker back first, then run this.
--
-- What it costs: the last reason histogram per seat. The seat-side reasons file
-- on the Fly volume keeps counting, so the next beat after a re-apply restores
-- it (and its first delta re-reports every reason once).
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN audit_write_failure_reasons_json;
