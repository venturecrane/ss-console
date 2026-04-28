-- Migration 0033: Add 'prospect' role for Outside View prospects (ADR 0002).
--
-- Widens the users.role CHECK constraint from ('admin', 'client') to
-- ('admin', 'client', 'prospect'). Phase 1 of Outside View needs prospect
-- users (created at /scan completion in PR-B) so they can have a portal
-- session and access portal.smd.services/outside-view.
--
-- IMPORTANT — D1 transaction semantics (per /critique 3 Pragmatist #3):
-- D1 does not transaction-wrap multi-statement migrations the way
-- Postgres does. If this migration partially applies during concurrent
-- writes, sessions or magic_links could break.
--
-- Mitigations:
--   1. Captain dispatches manually during a known-quiet window
--      (NOT auto-merged). Use `wrangler d1 migrations apply` directly
--      with the captain confirming low-write-volume.
--   2. The CI smoke test in this PR validates that admin login + client
--      login still work post-migration — fails fast if FKs/indexes
--      weren't preserved.
--   3. Rollback SQL committed alongside as 0033_add_prospect_role_down.sql.
--      Run if smoke test or production verification fails.
--
-- Why widen, not drop: SQLite has no ALTER TABLE DROP CONSTRAINT.
-- Removing or changing a CHECK requires the same rename-recreate-copy-drop
-- as widening it. So we widen.
--
-- Why only 'prospect' (not 'prospect_engaged') in this migration:
-- D2 completion (Phase 3) uses an outside_views.completed_d2_at column
-- instead of a second role flip — reversible, queryable, no second
-- CHECK rewrite (per /critique 3 Pragmatist #8). We pay the migration
-- cost once.
--
-- Schema preserved (matches state after 0001 + 0004 + 0018):
--   - id, org_id, email, name, role
--   - last_login_at, created_at
--   - password_hash (added 0004)
--   - entity_id (added 0018; client_id was dropped in 0018)
--   - UNIQUE(org_id, email)

-- Step 1: Create new table with widened role CHECK.
-- Includes all columns from 0001 + 0004 + 0018 in their final order.
CREATE TABLE users_new (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  email           TEXT NOT NULL,
  name            TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('admin', 'client', 'prospect')),
  last_login_at   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  password_hash   TEXT,
  entity_id       TEXT,
  UNIQUE(org_id, email)
);

-- Step 2: Copy rows. Column order matches the current users table layout
-- after migrations 0001 + 0004 + 0018.
INSERT INTO users_new (
  id, org_id, email, name, role,
  last_login_at, created_at, password_hash, entity_id
)
SELECT
  id, org_id, email, name, role,
  last_login_at, created_at, password_hash, entity_id
FROM users;

-- Step 3: Drop old table, rename new into place.
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

-- Step 4: Recreate indexes that referenced users (idx_users_entity from 0018,
-- plus any others). Without these the portal session resolver and other
-- entity-scoped lookups regress to table scans.
CREATE INDEX idx_users_entity ON users(org_id, entity_id);
