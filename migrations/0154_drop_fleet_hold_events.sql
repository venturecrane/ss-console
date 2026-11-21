-- Removes the hold history 0153 added (fleet_hold_events and its two triggers
-- on fleet_holds). The Captain retired the lead-time measurement it fed on
-- 2026-11-21; fleet_holds and every writer of it are untouched. 0153's file is
-- gone from the tree; it stays only as a row in d1_migrations where it ran.
-- IF EXISTS throughout, so a database 0153 never reached runs this as a no-op.
DROP TRIGGER IF EXISTS fleet_holds_record_placed;
DROP TRIGGER IF EXISTS fleet_holds_record_lifted;
DROP INDEX IF EXISTS idx_fleet_hold_events_slug;
DROP TABLE IF EXISTS fleet_hold_events;
