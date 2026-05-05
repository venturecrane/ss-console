-- Rollback for migration 0034 (drop Outside View infrastructure).
--
-- MANUAL-ONLY. This file lives in /rollbacks/ so wrangler does NOT
-- auto-apply it. Apply with:
--   npx wrangler d1 execute ss-console-db --remote --file migrations/rollbacks/0034_drop_outside_view_infra_down.sql
--
-- Schema-only rollback. Restoring functionality requires reverting
-- PR #703 and redeploying — without the diagnostic pipeline, scan worker,
-- API endpoints, and prospect role plumbing, these tables are inert.
--
-- Recreation order is FK-dependent: outside_views.scan_request_id
-- REFERENCES scan_requests(id). Recreate scan_requests FIRST.
--
-- The schema below inherits the as-of-0032 state:
--   - 0029 created scan_requests with the original 11 columns + 5 indexes
--   - 0030 added scan_status_reason
--   - 0031 added workflow_run_id
--   - 0032 created outside_views with 3 indexes (FK to scan_requests)
-- Indexes and CHECK constraints are reproduced verbatim from those migrations.

----------------------------------------------------------------------
-- scan_requests (recreate first; FK target for outside_views)
----------------------------------------------------------------------
CREATE TABLE scan_requests (
  id                        TEXT PRIMARY KEY,

  email                     TEXT NOT NULL,
  domain                    TEXT NOT NULL,
  linkedin_url              TEXT,

  verification_token_hash   TEXT NOT NULL,
  verified_at               TEXT,

  scan_started_at           TEXT,
  scan_completed_at         TEXT,
  scan_status               TEXT NOT NULL DEFAULT 'pending_verification' CHECK (scan_status IN (
    'pending_verification',
    'verified',
    'completed',
    'thin_footprint',
    'failed'
  )),

  thin_footprint_skipped    INTEGER NOT NULL DEFAULT 0 CHECK (thin_footprint_skipped IN (0, 1)),
  entity_id                 TEXT REFERENCES entities(id),

  email_sent_at             TEXT,

  request_ip                TEXT,
  error_message             TEXT,

  created_at                TEXT NOT NULL DEFAULT (datetime('now')),

  -- 0030
  scan_status_reason        TEXT,

  -- 0031
  workflow_run_id           TEXT
);

CREATE INDEX idx_scan_requests_ip
  ON scan_requests(request_ip, created_at DESC);

CREATE INDEX idx_scan_requests_email
  ON scan_requests(email, created_at DESC);

CREATE INDEX idx_scan_requests_domain
  ON scan_requests(domain, scan_status, created_at);

CREATE UNIQUE INDEX idx_scan_requests_token
  ON scan_requests(verification_token_hash);

CREATE INDEX idx_scan_requests_created
  ON scan_requests(created_at);

CREATE INDEX idx_scan_requests_entity
  ON scan_requests(entity_id)
  WHERE entity_id IS NOT NULL;

----------------------------------------------------------------------
-- outside_views (recreate after scan_requests; FK references it)
----------------------------------------------------------------------
CREATE TABLE outside_views (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id),
  entity_id         TEXT NOT NULL REFERENCES entities(id),
  scan_request_id   TEXT REFERENCES scan_requests(id),

  depth             TEXT NOT NULL CHECK (depth IN ('d1', 'd2', 'd3')),
  artifact_version  INTEGER NOT NULL DEFAULT 1,
  artifact_json     TEXT NOT NULL,

  rendered_at       TEXT NOT NULL,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_outside_views_entity_recent
  ON outside_views(entity_id, created_at DESC);

CREATE INDEX idx_outside_views_entity_depth
  ON outside_views(entity_id, depth);

CREATE INDEX idx_outside_views_scan_request
  ON outside_views(scan_request_id)
  WHERE scan_request_id IS NOT NULL;
