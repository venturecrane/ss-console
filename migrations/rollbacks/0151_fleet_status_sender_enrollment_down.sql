-- Rollback for 0151: drop fleet_status.sender_enrollment_json. Roll the web
-- Worker and the fleet-alerts Worker back first (and 0152 before this file):
-- the heartbeat upsert and the worker's SELECT both name the column. The seat
-- keeps enrolling and auditing; only the console's copy, and the
-- enrollment_guessing page, are lost.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE fleet_status DROP COLUMN sender_enrollment_json;
