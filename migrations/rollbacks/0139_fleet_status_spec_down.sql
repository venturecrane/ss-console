-- Rollback for 0139: drop fleet_status.spec_json. Roll the web Worker and the
-- fleet-alerts Worker back first: the heartbeat upsert and the worker's
-- fleet_status SELECT both name the column and fail on its absence. The seat
-- keeps writing its state file; only the console's copy is lost.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN spec_json;
