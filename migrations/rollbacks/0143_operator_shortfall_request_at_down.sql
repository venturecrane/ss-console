-- Rollback for 0143: drop operator_shortfalls.request_at.
-- Roll the fleet-alerts Worker back first: a Worker that still writes the
-- column fails its INSERT once it is gone. The rows survive; only the link to
-- the request each was serving is lost.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE operator_shortfalls DROP COLUMN request_at;
