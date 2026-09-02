-- Staging portal access fixture — STAGING DATABASE ONLY.
--
-- Applied by `npm run db:seed:staging`, which pins ss-console-db-staging by
-- name and uuid. This file must NEVER live under migrations/: .github/workflows/
-- deploy.yml runs `d1 migrations apply ss-console-db --remote` on every push to
-- main with migrations_dir unset, so anything in migrations/ is auto-applied to
-- PRODUCTION.
--
-- Creates the minimum substrate for an authenticated portal journey:
--   entities  — the fixture business the client is bound to
--   users     — a client (bound to that entity) and an admin (deliberately not)
--
-- The Clerk identities that sign in as these rows are created separately by
-- scripts/staging-clerk-users.mjs, in the Clerk DEVELOPMENT instance. The two
-- halves are joined by EMAIL, never by a stored Clerk id — see below.
--
-- Everything here is unmistakably fake. Ids carry the literal prefix
-- `01JSTAGING`, which doubles as the undo handle:
--   DELETE FROM users    WHERE id LIKE '01JSTAGING%';
--   DELETE FROM entities WHERE id LIKE '01JSTAGING%';
--
-- Deliberately seeds NO invoices, quotes, engagements, or milestones. Those
-- tables render as commitments in the portal, and no part of this journey needs
-- them.

-- ---------------------------------------------------------------------------
-- The fixture business.
-- Upserts on the natural key (org_id, slug) — its UNIQUE constraint — so a
-- pre-existing row with a different id is updated rather than colliding.
-- `stage` must be one of the values in the entities CHECK constraint;
-- 'ongoing' reflects a live engagement, which is what the portal renders for.
-- ---------------------------------------------------------------------------
INSERT INTO entities (id, org_id, name, slug, stage, source_pipeline)
VALUES (
  '01JSTAGING000ENTITY00001',
  '01JQFK0000SMDSERVICES000',
  'STAGING FIXTURE (not a real client)',
  'staging-fixture',
  'ongoing',
  'staging-seed'
)
ON CONFLICT(org_id, slug) DO UPDATE SET
  name       = excluded.name,
  stage      = excluded.stage,
  updated_at = datetime('now');

-- ---------------------------------------------------------------------------
-- The client identity.
--
-- Two properties are load-bearing:
--
-- 1. Upsert on the natural key (org_id, email), NOT on a fixed id. users carries
--    UNIQUE(org_id, email), and ensureLocalUser (src/lib/auth/clerk-bridge.ts)
--    JIT-inserts a RANDOM-id row if anyone signs in before this seed lands. A
--    fixed-id insert would then violate the unique constraint and the row could
--    never be repaired. Keying on the email survives both orderings.
--
-- 2. clerk_user_id appears in neither the column list nor the update set. Left
--    NULL on a first apply, ensureLocalUser's case-insensitive email auto-link
--    claims the row on first sign-in. Left untouched on re-apply, a re-seed can
--    never unlink a signed-in identity — and a wiped Clerk dev instance rebinds
--    by email with no re-seeding at all.
--
-- entity_id resolves by subselect rather than by literal, so it is correct even
-- if the entity above was an update of a pre-existing row.
-- ---------------------------------------------------------------------------
INSERT INTO users (id, org_id, email, name, role, entity_id)
VALUES (
  '01JSTAGING000USERCLIENT1',
  '01JQFK0000SMDSERVICES000',
  'staging-client@smd.services',
  'Staging Client',
  'client',
  (SELECT id FROM entities WHERE org_id = '01JQFK0000SMDSERVICES000' AND slug = 'staging-fixture')
)
ON CONFLICT(org_id, email) DO UPDATE SET
  name      = excluded.name,
  role      = excluded.role,
  entity_id = excluded.entity_id;

-- ---------------------------------------------------------------------------
-- The admin identity.
--
-- entity_id is deliberately NULL. chooseSignedInSurface
-- (src/lib/auth/after-sign-in-target.ts) computes
-- `portalEligible = Boolean(userRow.entity_id || auth.orgId)` and tests the
-- signed-in host first. Staging serves admin and portal from ONE workers.dev
-- host, so an admin carrying an entity_id would be dispatched to /portal on
-- every sign-in and never reach the console.
--
-- For the same reason this identity must not join any Clerk organization.
-- ---------------------------------------------------------------------------
INSERT INTO users (id, org_id, email, name, role, entity_id)
VALUES (
  '01JSTAGING0000USERADMIN1',
  '01JQFK0000SMDSERVICES000',
  'staging-admin@smd.services',
  'Staging Admin',
  'admin',
  NULL
)
ON CONFLICT(org_id, email) DO UPDATE SET
  name      = excluded.name,
  role      = excluded.role,
  entity_id = NULL;
