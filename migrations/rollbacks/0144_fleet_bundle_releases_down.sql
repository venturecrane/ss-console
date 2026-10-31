-- Rollback for 0144: narrow fleet_releases.kind back to ('image', 'skills'),
-- drop artifact_name / artifact_version, and drop fleet_status.bundles_json.
-- NOT fail-soft. Every kind 'bundle' row would violate the narrowed CHECK, so
-- those rows are dropped here, along with their fleet_rollouts rows (export
-- them first: SELECT * FROM fleet_releases WHERE kind = 'bundle'). Roll the web
-- Worker back first (its heartbeat upsert names bundles_json), and stop the
-- release controller's bundle step and CI's publish-bundle job (both write
-- kind 'bundle'). Seats keep their pointers and installed bundles; a seat boot
-- with a pointer still mounts what it has.
--
-- Manual-only; coordinate with Captain.

DELETE FROM fleet_rollouts WHERE release_id IN (SELECT release_id FROM fleet_releases WHERE kind = 'bundle');

CREATE TABLE fleet_releases_old (
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

INSERT INTO fleet_releases_old (
  release_id, kind, image_ref, image_digest, skills_version, ss_console_sha, overlay_ref,
  volume_forward_compatible, declared_secrets, notes, created_at, completed_last_ring_at)
SELECT
  release_id, kind, image_ref, image_digest, skills_version, ss_console_sha, overlay_ref,
  volume_forward_compatible, declared_secrets, notes, created_at, completed_last_ring_at
FROM fleet_releases
WHERE kind IS NULL OR kind != 'bundle';

DROP TABLE fleet_releases;
ALTER TABLE fleet_releases_old RENAME TO fleet_releases;

ALTER TABLE fleet_status DROP COLUMN bundles_json;
