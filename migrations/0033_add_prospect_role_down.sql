-- Migration 0033 ROLLBACK: Revert 'prospect' role addition.
--
-- Run only if the up migration (0033_add_prospect_role.sql) caused a
-- regression that wasn't caught by the CI smoke test. Per /critique 3
-- Pragmatist #3, captain dispatches this manually if needed.
--
-- WARNING: This rollback FAILS if any users row currently has role='prospect'.
-- Such rows would be created by Outside View Phase 1 PR-B after this migration
-- shipped. If you need to roll back AFTER prospects exist:
--   1. First decide what to do with those rows (delete? convert to client?).
--   2. Run a manual cleanup: `DELETE FROM users WHERE role = 'prospect';`
--      (or `UPDATE users SET role = 'client' WHERE role = 'prospect';`).
--   3. Then run this rollback.
--
-- The pre-flight guard below makes the failure explicit instead of silently
-- truncating to a narrower CHECK.

-- Step 0: Pre-flight. The CHECK below will fail if any prospect rows exist.
-- We intentionally rely on the constraint failure to halt the migration
-- rather than emitting a soft warning.

-- Step 1: Recreate table with original ('admin','client') CHECK.
CREATE TABLE users_old (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  email           TEXT NOT NULL,
  name            TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('admin', 'client')),
  last_login_at   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  password_hash   TEXT,
  entity_id       TEXT,
  UNIQUE(org_id, email)
);

-- Step 2: Copy rows. The CHECK on users_old will reject any prospect row.
INSERT INTO users_old (
  id, org_id, email, name, role,
  last_login_at, created_at, password_hash, entity_id
)
SELECT
  id, org_id, email, name, role,
  last_login_at, created_at, password_hash, entity_id
FROM users;

-- Step 3: Drop new table, rename old into place.
DROP TABLE users;
ALTER TABLE users_old RENAME TO users;

-- Step 4: Recreate indexes.
CREATE INDEX idx_users_entity ON users(org_id, entity_id);
