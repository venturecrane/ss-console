-- 0129: the seat fleet release system (seat fleet scaling, 2026-10-19).
--
-- Seats stop being built one at a time. One image is built per release into the
-- Fly registry app `hermes-fleet`, and `fleet release` rolls it through rings of
-- seats, waiting for each seat to go idle, smoking it, and rolling a seat that
-- truly fails back to the image it came from. Skills ship through the same
-- rings as a versioned artifact, with no restart.
--
-- fleet_status gains what each seat says it runs and how busy it is, so the
-- rollout can read back a deploy and `fleet status` can show the fleet:
--
--   image_ref       the image the seat booted (registry ref, seat-reported)
--   skills_version  the skills manifest version the seat has loaded
--   config_digest   digest of the customer config the seat booted with
--   jobs_running    lane jobs in flight right now
--   jobs_queued     lane jobs accepted and waiting
--   wakes_pending   scheduled wakes not yet taken
--
-- All nullable. NULL = unknown, 0 = none. Overwritten every beat: the overlay
-- omits a value it does not know, so an absent key stores NULL (a seat on an
-- older overlay emits none of these and reads as unknown). See
-- src/lib/db/fleet-status.ts.
--
-- The four new tables are the rollout's own state:
--   fleet_releases  one row per built image or skills version
--   fleet_rollouts  one row per (release, seat) attempt and its outcome
--   fleet_holds     seats a rollout must skip, set instantly by `fleet hold`
--   fleet_lease     a single named lease, taken by conditional insert/update,
--                   so at most one rollout drives the fleet at a time
--
-- Manual-only rollback at migrations/rollbacks/0129_fleet_release_system_down.sql.

ALTER TABLE fleet_status ADD COLUMN image_ref TEXT;
ALTER TABLE fleet_status ADD COLUMN skills_version TEXT;
ALTER TABLE fleet_status ADD COLUMN config_digest TEXT;
ALTER TABLE fleet_status ADD COLUMN jobs_running INTEGER;
ALTER TABLE fleet_status ADD COLUMN jobs_queued INTEGER;
ALTER TABLE fleet_status ADD COLUMN wakes_pending INTEGER;

CREATE TABLE fleet_releases (
  release_id                TEXT PRIMARY KEY,
  kind                      TEXT CHECK (kind IN ('image', 'skills')),
  image_ref                 TEXT,
  image_digest              TEXT,
  skills_version            TEXT,
  ss_console_sha            TEXT,
  overlay_ref               TEXT,
  volume_forward_compatible INTEGER NOT NULL DEFAULT 1,
  declared_secrets          TEXT, -- JSON array of secret NAMES, never values
  notes                     TEXT,
  created_at                TEXT NOT NULL,
  completed_last_ring_at    TEXT
);

CREATE TABLE fleet_rollouts (
  rollout_id          TEXT PRIMARY KEY,
  release_id          TEXT NOT NULL,
  customer_slug       TEXT NOT NULL,
  ring                INTEGER,
  state               TEXT CHECK (state IN (
                        'pending', 'waiting_idle', 'deploying', 'smoking', 'done',
                        'rolled_back', 'failed', 'halted', 'skipped_held',
                        'tooling_error')),
  from_image          TEXT,
  from_toml           TEXT,
  to_image            TEXT,
  from_skills_version TEXT,
  to_skills_version   TEXT,
  snapshot_id         TEXT,
  smoke_class         TEXT,
  detail              TEXT,
  started_at          TEXT,
  finished_at         TEXT
);

CREATE INDEX idx_fleet_rollouts_release ON fleet_rollouts (release_id);
CREATE INDEX idx_fleet_rollouts_slug_started ON fleet_rollouts (customer_slug, started_at);

CREATE TABLE fleet_holds (
  customer_slug TEXT PRIMARY KEY,
  reason        TEXT NOT NULL,
  held_by       TEXT,
  held_at       TEXT NOT NULL
);

CREATE TABLE fleet_lease (
  lease_name  TEXT PRIMARY KEY,
  holder      TEXT NOT NULL,
  release_id  TEXT,
  acquired_at TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);
