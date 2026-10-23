-- Rollback for 0137: drop fleet_status.selftest_json. Roll the web Worker and
-- the fleet-alerts Worker back first: the heartbeat upsert and the worker's
-- fleet_status SELECT both name the column and fail on its absence. The seat
-- keeps writing its results file; only the console's copy is lost.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN selftest_json;
