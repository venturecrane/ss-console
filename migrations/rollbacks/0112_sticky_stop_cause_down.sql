-- Rollback for 0112: drop the two sticky-stop cause columns.
--
-- Cheap and safe: both columns are nullable, purely observational, and
-- rewritten from scratch on every beat. Nothing derives from them and no
-- alert gates on them (the hard_stop condition still fires on
-- sticky_stop_level alone) -- dropping them only returns the fleet to
-- reporting THAT a seat stopped without WHY.
--
-- Manual-only; coordinate with Captain. D1 wraps this file in one atomic
-- transaction.

ALTER TABLE fleet_status DROP COLUMN sticky_stop_reason;
ALTER TABLE fleet_status DROP COLUMN sticky_stop_condition;
