-- 0153: the database records every seat hold placed and lifted.
--
-- The admin Fleet page's "Merge to seat" card splits a change's minutes into
-- pipeline time and time a client seat was on hold. fleet_holds keeps only the
-- holds in place NOW: `fleet unhold` deletes the row, and the release tick
-- never writes a rollout row for a held seat (tick.py `_front` leaves it out
-- of the seats it rolls), so a hold placed and lifted left no trace anywhere
-- and the card could not say how long a change waited on one.
--
-- History only. No reader of fleet_holds changes, and no writer: two triggers
-- on fleet_holds record each hold as an interval here.
--   fleet_hold_events   one row per hold: the seat, when it was placed
--                       (fleet_holds.held_at), and when it was lifted
--                       (NULL while it is in place)
--   placed trigger      AFTER INSERT on fleet_holds opens an interval, unless
--                       the seat already has one open. `FleetStore.hold`
--                       (operator/bin/lib/fleet/d1.py) is an upsert: re-holding
--                       a held seat takes the ON CONFLICT DO UPDATE path, which
--                       fires no INSERT trigger in SQLite, and the guard keeps
--                       an INSERT OR REPLACE (whose implicit delete fires no
--                       DELETE trigger) from opening a second interval
--   lifted trigger      AFTER DELETE on fleet_holds closes the seat's open
--                       interval: `fleet unhold` and a decommission alike
-- The holds in place when this runs are backfilled as open intervals.
-- Retained when a seat is retired, like fleet_rollouts (release history: a
-- seat slug and timestamps, no client content); the decommission DELETE on
-- fleet_holds still closes the seat's open interval through the trigger.
-- `reason` and `held_by` are deliberately not copied: the card needs only
-- when, and the reason text stays where it is written.
--
-- WARNING. Never drop, rename or rebuild fleet_hold_events without dropping
-- the two triggers on fleet_holds FIRST. A trigger whose table is missing makes
-- every `fleet hold` and `fleet unhold` fail, so a seat could not be held or
-- released. If a hold ever fails with "no such table: fleet_hold_events", run
-- migrations/rollbacks/0153_fleet_hold_events_down.sql, which drops the
-- triggers before the table.
--
-- Back-out is DROP TRIGGER by hand (fleet_holds_record_placed,
-- fleet_holds_record_lifted); the card then reads the triggers as missing and
-- shows hold time as not recorded. Re-creating the triggers must be a new
-- numbered migration, so the card's "recorded since" moves with it.
--
-- Manual-only rollback at migrations/rollbacks/0153_fleet_hold_events_down.sql.

CREATE TABLE fleet_hold_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_slug TEXT NOT NULL,
  held_at       TEXT NOT NULL,
  lifted_at     TEXT
);

CREATE INDEX idx_fleet_hold_events_slug ON fleet_hold_events (customer_slug, held_at);

INSERT INTO fleet_hold_events (customer_slug, held_at)
SELECT customer_slug, held_at FROM fleet_holds;

CREATE TRIGGER fleet_holds_record_placed
AFTER INSERT ON fleet_holds
WHEN NOT EXISTS (
  SELECT 1 FROM fleet_hold_events
  WHERE customer_slug = NEW.customer_slug AND lifted_at IS NULL
)
BEGIN
  INSERT INTO fleet_hold_events (customer_slug, held_at) VALUES (NEW.customer_slug, NEW.held_at);
END;

CREATE TRIGGER fleet_holds_record_lifted
AFTER DELETE ON fleet_holds
BEGIN
  UPDATE fleet_hold_events SET lifted_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
  WHERE customer_slug = OLD.customer_slug AND lifted_at IS NULL;
END;
