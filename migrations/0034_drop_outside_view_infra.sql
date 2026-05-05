-- Migration 0034: Drop Outside View infrastructure (#703).
--
-- Outside View (ADR 0002) was retired across two PRs:
--   - PR #702: user-visible surface (portal tab, /portal/outside-view, public form)
--   - PR #703 (this PR): infrastructure (diagnostic pipeline, scan worker, prospect role,
--     outside_views and scan_requests tables, dedicated tests, ADR supersession)
--
-- Production data state at retirement (verified live):
--   outside_views                  rows: 0
--   scan_requests                  rows: 0
--   users WHERE role='prospect'    rows: 0
--
-- Drop order is FK-dependent: outside_views.scan_request_id REFERENCES
-- scan_requests(id), so outside_views must be dropped first.
--
-- Rollback: see migrations/rollbacks/0034_drop_outside_view_infra_down.sql
-- (manual-only; restoring functionality requires reverting the deletion PR
-- and redeploying — schema alone is insufficient).
--
-- The users.role CHECK constraint is NOT restored. Migration 0033
-- explicitly removed the CHECK so future role additions are zero-migration;
-- TypeScript types and application-layer queries (verify.astro,
-- portal/session.ts, api/auth/magic-link.ts) enforce the active role set
-- at every read site. Restoring CHECK as ('admin', 'client') is a separate
-- decision tracked in a follow-up issue.

DROP TABLE IF EXISTS outside_views;
DROP TABLE IF EXISTS scan_requests;
