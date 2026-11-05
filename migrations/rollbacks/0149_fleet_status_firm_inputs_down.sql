-- Rollback for 0149: drop fleet_status.firm_inputs_json. Roll the web Worker
-- back first: the heartbeat upsert names the column and fails on its absence.
-- The seat keeps writing its state file and adopting firm inputs; only the
-- console's copy is lost.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN firm_inputs_json;
