-- Manual-only rollback of 0153. Drops the hold history and its two triggers;
-- fleet_holds and every writer of it are untouched. The Fleet page's merge to
-- seat card then reads hold time as not recorded and says so. The intervals
-- recorded so far are lost: export them first if they are wanted.
DROP TRIGGER IF EXISTS fleet_holds_record_placed;
DROP TRIGGER IF EXISTS fleet_holds_record_lifted;
DROP INDEX IF EXISTS idx_fleet_hold_events_slug;
DROP TABLE IF EXISTS fleet_hold_events;
