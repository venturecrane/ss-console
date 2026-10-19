-- Rollback for 0129: drop the four fleet release tables and the six
-- fleet_status columns. NOT fail-soft: the web Worker's heartbeat upsert names
-- the six columns, and SQLite fails a statement on an unknown column, so
-- running this while that Worker is deployed stops every heartbeat write. Roll
-- the Worker back first, then run this. Release and rollout history is lost;
-- export it first if it matters:
--   SELECT * FROM fleet_releases; SELECT * FROM fleet_rollouts;
--
-- Manual-only; coordinate with Captain.

DROP TABLE IF EXISTS fleet_lease;
DROP TABLE IF EXISTS fleet_holds;
DROP INDEX IF EXISTS idx_fleet_rollouts_slug_started;
DROP INDEX IF EXISTS idx_fleet_rollouts_release;
DROP TABLE IF EXISTS fleet_rollouts;
DROP TABLE IF EXISTS fleet_releases;
ALTER TABLE fleet_status DROP COLUMN wakes_pending;
ALTER TABLE fleet_status DROP COLUMN jobs_queued;
ALTER TABLE fleet_status DROP COLUMN jobs_running;
ALTER TABLE fleet_status DROP COLUMN config_digest;
ALTER TABLE fleet_status DROP COLUMN skills_version;
ALTER TABLE fleet_status DROP COLUMN image_ref;
