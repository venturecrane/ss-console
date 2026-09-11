-- Rollback for 0107: drop the three ledger-observability columns.
--
-- Cheap and complete. Nothing else references them: the write-failure delta is
-- computed at ingest from the prior stored value rather than held as a standing
-- fleet_alert_state condition, so there is no CHECK to narrow and no open alert
-- row to strip (contrast 0106, which had both).
--
-- What rolling back COSTS, stated rather than assumed away: the recorded count
-- of rows each seat has lost goes with the columns. The Machine-side tally on
-- the Fly volume is unaffected and keeps counting, so the numbers return on the
-- first beat after the columns do — but the delta baseline restarts from
-- whatever that first beat reports, and any failure the console saw before the
-- rollback is no longer on record here.

ALTER TABLE fleet_status DROP COLUMN audit_write_failures;
ALTER TABLE fleet_status DROP COLUMN audit_head;
ALTER TABLE fleet_status DROP COLUMN audit_rows;
