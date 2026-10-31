-- 0144: the jobs bundle is a release kind (ADR 0100 W3).
--
-- The job runner (/opt/medchron) and the Smokeball connector venv
-- (/opt/connectors/smokeball) stop riding only in the seat image. CI builds them
-- on merge, inside the published core image at the same paths, as one versioned
-- artifact, the `jobs` bundle (scripts/ci-publish-bundle.sh), and the release
-- controller ships it ring by ring ONLY to the seats whose customer.yaml authors
-- a job lane (operator/bin/lib/fleet/bundles.py). A seat that authors none is
-- never touched.
--
-- fleet_releases gains the kind 'bundle' and two columns:
--   artifact_name     the bundle's name ('jobs'); NULL for image/skills rows
--   artifact_version  YYYYMMDDHHMM-<sha8>, the skills version shape; NULL likewise
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0142 shape):
-- identical columns in 0129 order plus the two new ones, every row carried
-- across with the two new columns NULL. 0129 created no index or trigger on
-- fleet_releases, so none is re-created. Every existing value stays valid.
--
-- fleet_status gains bundles_json: what bundle version each bundle runs at, as
-- the seat reports it on its heartbeat (`bundles`: {"jobs": "<version>"} when
-- mounted, {"jobs": "image"} when the image's own copy runs, absent when the
-- seat authors no lane). Plain overwrite at ingest, INCLUDING back to NULL, like
-- image_ref: the controller's read-back must never see a value the seat no
-- longer reports. See src/lib/db/fleet-status.ts.
--
-- Ordering is safe: deploy.yml applies migrations before the Workers deploy and
-- before publish-bundle inserts a 'bundle' row, and only the new code names the
-- columns.
--
-- Manual-only rollback at migrations/rollbacks/0144_fleet_bundle_releases_down.sql.

CREATE TABLE fleet_releases_new (
  release_id                TEXT PRIMARY KEY,
  kind                      TEXT CHECK (kind IN ('image', 'skills', 'bundle')),
  image_ref                 TEXT,
  image_digest              TEXT,
  skills_version            TEXT,
  ss_console_sha            TEXT,
  overlay_ref               TEXT,
  volume_forward_compatible INTEGER NOT NULL DEFAULT 1,
  declared_secrets          TEXT, -- JSON array of secret NAMES, never values
  notes                     TEXT,
  created_at                TEXT NOT NULL,
  completed_last_ring_at    TEXT,
  artifact_name             TEXT,
  artifact_version          TEXT
);

INSERT INTO fleet_releases_new (
  release_id, kind, image_ref, image_digest, skills_version, ss_console_sha, overlay_ref,
  volume_forward_compatible, declared_secrets, notes, created_at, completed_last_ring_at)
SELECT
  release_id, kind, image_ref, image_digest, skills_version, ss_console_sha, overlay_ref,
  volume_forward_compatible, declared_secrets, notes, created_at, completed_last_ring_at
FROM fleet_releases;

DROP TABLE fleet_releases;
ALTER TABLE fleet_releases_new RENAME TO fleet_releases;

ALTER TABLE fleet_status ADD COLUMN bundles_json TEXT;
