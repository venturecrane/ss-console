/**
 * Outside View Phase 1 PR-A — schema + auth plumbing smoke tests.
 *
 * Per ADR 0002 and /critique 3 Pragmatist #3, the prospect-role migration
 * (rename-recreate-copy-drop on users) is the riskiest change in PR-A. D1
 * does not transaction-wrap multi-statement migrations the way Postgres
 * does, so a partial apply could break sessions or magic_links.
 *
 * These tests are static-source assertions (the codebase pattern; no live
 * D1 in CI). They validate that:
 *   1. The migrations have the expected shape — outside_views table created
 *      with required columns and indexes; users CHECK constraint widened to
 *      include 'prospect'; rollback migration committed alongside.
 *   2. The auth code paths (verify, magic-link API, portal session resolver,
 *      middleware) admit the prospect role. If any of these queries still
 *      hard-codes role='client', a prospect would 403 on the portal.
 *   3. createMagicLink requires an explicit ttlMs argument — preventing
 *      accidental admin TTL extension via a forgotten default.
 *   4. Admin login flow continues to work: admin role check unchanged;
 *      admin routes still demand role='admin'; admin session duration
 *      stays at 7 days.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('PR-A: outside_views migration', () => {
  const sql = existsSync(resolve('migrations/0032_create_outside_views.sql'))
    ? readFileSync(resolve('migrations/0032_create_outside_views.sql'), 'utf-8')
    : ''

  it('migration file exists', () => {
    expect(existsSync(resolve('migrations/0032_create_outside_views.sql'))).toBe(true)
  })

  it('creates outside_views table', () => {
    expect(sql).toMatch(/CREATE TABLE outside_views\b/)
  })

  it('has the depth CHECK constraint with d1/d2/d3', () => {
    expect(sql).toMatch(
      /depth\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*depth\s+IN\s*\(\s*'d1',\s*'d2',\s*'d3'/
    )
  })

  it('has artifact_version column for v1/v2 dispatch', () => {
    expect(sql).toMatch(/artifact_version\s+INTEGER\s+NOT NULL\s+DEFAULT\s+1/)
  })

  it('artifact_json is required', () => {
    expect(sql).toMatch(/artifact_json\s+TEXT\s+NOT NULL/)
  })

  it('entity_id is required and FKs entities', () => {
    expect(sql).toMatch(/entity_id\s+TEXT\s+NOT NULL\s+REFERENCES entities\(id\)/)
  })

  it('scan_request_id is nullable (D2/D3 rows have no scan)', () => {
    expect(sql).toMatch(/scan_request_id\s+TEXT\s+REFERENCES scan_requests\(id\)/)
  })

  it('creates entity_recent index for portal latest-artifact lookup', () => {
    expect(sql).toMatch(
      /CREATE INDEX idx_outside_views_entity_recent[\s\S]*entity_id,\s*created_at DESC/
    )
  })

  it('does NOT include superseded_by as a column (deferred to Phase 4)', () => {
    // Comments may mention the future column; the column itself must not exist.
    expect(sql).not.toMatch(/^\s*superseded_by\s+TEXT/m)
  })
})

describe('PR-A: prospect role migration', () => {
  const up = existsSync(resolve('migrations/0033_add_prospect_role.sql'))
    ? readFileSync(resolve('migrations/0033_add_prospect_role.sql'), 'utf-8')
    : ''
  const down = existsSync(resolve('migrations/0033_add_prospect_role_down.sql'))
    ? readFileSync(resolve('migrations/0033_add_prospect_role_down.sql'), 'utf-8')
    : ''

  it('up migration exists', () => {
    expect(existsSync(resolve('migrations/0033_add_prospect_role.sql'))).toBe(true)
  })

  it('rollback migration exists alongside up migration', () => {
    expect(existsSync(resolve('migrations/0033_add_prospect_role_down.sql'))).toBe(true)
  })

  it('up migration widens role CHECK to admin/client/prospect', () => {
    expect(up).toMatch(
      /role\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*role\s+IN\s*\(\s*'admin',\s*'client',\s*'prospect'\s*\)/
    )
  })

  it('up migration uses rename-recreate-copy-drop pattern', () => {
    expect(up).toMatch(/CREATE TABLE users_new/)
    expect(up).toMatch(/INSERT INTO users_new[\s\S]*FROM users/)
    expect(up).toMatch(/DROP TABLE users/)
    expect(up).toMatch(/ALTER TABLE users_new RENAME TO users/)
  })

  it('up migration recreates idx_users_entity index', () => {
    expect(up).toMatch(/CREATE INDEX idx_users_entity ON users\(org_id, entity_id\)/)
  })

  it('up migration documents D1 transaction limitations', () => {
    expect(up).toMatch(/D1 does not transaction-wrap/i)
  })

  it('up migration does NOT add prospect_engaged to the CHECK constraint', () => {
    // Comments explain why prospect_engaged is excluded; the constraint itself
    // must not include it. Match the role CHECK constraint and verify only
    // admin/client/prospect appear.
    const checkMatch = up.match(/role\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*role\s+IN\s*\(([^)]+)\)/)
    expect(checkMatch).toBeTruthy()
    if (checkMatch) {
      expect(checkMatch[1]).not.toMatch(/prospect_engaged/)
    }
  })

  it('rollback narrows CHECK back to admin/client', () => {
    expect(down).toMatch(/CHECK\s*\(\s*role\s+IN\s*\(\s*'admin',\s*'client'\s*\)/)
  })

  it('rollback warns about prospect rows blocking the rollback', () => {
    expect(down).toMatch(/WARNING/i)
    expect(down).toMatch(/prospect/i)
  })
})

describe('PR-A: auth role-set widening', () => {
  it('verify.astro accepts client OR prospect role', () => {
    const src = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(src).toMatch(/role IN \('client', 'prospect'\)/)
    expect(src).not.toMatch(/role = 'client'/)
  })

  it('/api/auth/magic-link accepts client OR prospect role', () => {
    const src = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(src).toMatch(/role IN \('client', 'prospect'\)/)
    expect(src).not.toMatch(/role = 'client'/)
  })

  it('getPortalClient accepts client OR prospect role', () => {
    const src = readFileSync(resolve('src/lib/portal/session.ts'), 'utf-8')
    expect(src).toMatch(/role IN \('client', 'prospect'\)/)
    expect(src).not.toMatch(/role = 'client'/)
  })

  it('getSessionDurationMs treats prospect like client (30 days)', () => {
    const src = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(src).toMatch(/role === 'client' \|\| role === 'prospect'/)
  })
})

describe('PR-A: middleware role gate', () => {
  const src = readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('admin routes still require role=admin', () => {
    expect(src).toMatch(/sessionRole === 'admin'/)
  })

  it('portal routes admit role=client AND role=prospect', () => {
    expect(src).toMatch(/sessionRole === 'client'/)
    expect(src).toMatch(/sessionRole === 'prospect'/)
  })

  it('cookie refresh handles prospect like client (portal session)', () => {
    expect(src).toMatch(/isPortalSession/)
    expect(src).toMatch(/role === 'client' \|\| context\.locals\.session\.role === 'prospect'/)
  })

  it('does NOT use the old strict-equality requiredRole pattern', () => {
    expect(src).not.toMatch(
      /const requiredRole = isAdminRoute \|\| isAdminApiRoute \? 'admin' : 'client'/
    )
  })

  it('still returns 403 JSON on API role mismatch', () => {
    expect(src).toMatch(/error: 'Forbidden'/)
    expect(src).toMatch(/status: 403/)
  })

  it('still returns 401 JSON on missing session for API routes', () => {
    expect(src).toMatch(/error: 'Unauthorized'/)
    expect(src).toMatch(/status: 401/)
  })

  it('still redirects portal-route unauthed to /auth/portal-login', () => {
    expect(src).toMatch(/\/auth\/portal-login/)
  })
})

describe('PR-A: createMagicLink ttlMs is required', () => {
  const src = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')

  it('signature requires ttlMs as a third parameter', () => {
    // Signature: createMagicLink(db, subject, ttlMs)
    expect(src).toMatch(/createMagicLink\([\s\S]*ttlMs:\s*number[\s\S]*\):\s*Promise<string>/)
  })

  it('ttlMs is NOT optional (no default ttlMs)', () => {
    // The function signature must NOT have ttlMs?: number or ttlMs = X
    const fnMatch = src.match(/export async function createMagicLink\([^)]*\)/s)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).not.toMatch(/ttlMs\?:/)
      expect(fnMatch[0]).not.toMatch(/ttlMs\s*:\s*number\s*=/)
    }
  })

  it('exports MAGIC_LINK_EXPIRY_MS constant for admin/client login', () => {
    expect(src).toMatch(/export const MAGIC_LINK_EXPIRY_MS = 15 \* 60 \* 1000/)
  })

  it('exports PROSPECT_MAGIC_LINK_EXPIRY_MS constant for Outside View', () => {
    expect(src).toMatch(/export const PROSPECT_MAGIC_LINK_EXPIRY_MS = 24 \* 60 \* 60 \* 1000/)
  })

  it('uses ttlMs param (not the const) in expires_at calculation', () => {
    expect(src).toMatch(/Date\.now\(\) \+ ttlMs/)
  })
})

describe('PR-A: createMagicLink call sites pass explicit TTL', () => {
  it('/api/auth/magic-link passes MAGIC_LINK_EXPIRY_MS', () => {
    const src = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(src).toMatch(/createMagicLink\([\s\S]*MAGIC_LINK_EXPIRY_MS\s*\)/)
  })

  it('/api/admin/resend-invitation passes MAGIC_LINK_EXPIRY_MS', () => {
    const src = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    expect(src).toMatch(/createMagicLink\([\s\S]*MAGIC_LINK_EXPIRY_MS\s*\)/)
  })
})

describe('PR-A: portal index redirects prospects', () => {
  const src = readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')

  it('redirects role=prospect to /portal/outside-view', () => {
    expect(src).toMatch(/session\.role === 'prospect'/)
    expect(src).toMatch(/Astro\.redirect\(['"]\/portal\/outside-view['"]\)/)
  })

  it('redirect happens before client-shaped data fetch', () => {
    const prospectRedirectIdx = src.indexOf("session.role === 'prospect'")
    const portalDataIdx = src.indexOf('getPortalClient(')
    expect(prospectRedirectIdx).toBeGreaterThan(0)
    expect(portalDataIdx).toBeGreaterThan(prospectRedirectIdx)
  })
})

describe('PR-A: outside_views CRUD module', () => {
  const src = readFileSync(resolve('src/lib/db/outside-views.ts'), 'utf-8')

  it('exports createOutsideView', () => {
    expect(src).toMatch(/export async function createOutsideView/)
  })

  it('exports getActiveOutsideViewByEntity', () => {
    expect(src).toMatch(/export async function getActiveOutsideViewByEntity/)
  })

  it('OutsideViewDepth type lists d1/d2/d3', () => {
    expect(src).toMatch(/'d1'\s*\|\s*'d2'\s*\|\s*'d3'/)
  })

  it('createOutsideView defaults artifact_version to 1', () => {
    expect(src).toMatch(/input\.artifact_version \?\? 1/)
  })

  it('getActiveOutsideViewByEntity scopes by org_id (tenant safety)', () => {
    expect(src).toMatch(/WHERE org_id = \?[\s\S]*AND entity_id = \?/)
  })
})

describe('PR-A: outside_views v1 adapter', () => {
  const src = readFileSync(resolve('src/lib/db/outside-views/adapter.ts'), 'utf-8')

  it('exports renderedReportToArtifactJsonV1 (write path)', () => {
    expect(src).toMatch(/export function renderedReportToArtifactJsonV1/)
  })

  it('exports parseArtifactJson (read path)', () => {
    expect(src).toMatch(/export function parseArtifactJson/)
  })

  it('writes a versioned wrapper { version: 1, report: ... }', () => {
    expect(src).toMatch(/version:\s*1,\s*report/)
  })

  it('returns null on JSON parse failure (no partial render)', () => {
    expect(src).toMatch(/JSON\.parse/)
    expect(src).toMatch(/return null/)
  })
})

describe('PR-A: admin login regression guard (post-migration)', () => {
  it('admin session duration stays at 7 days', () => {
    const src = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(src).toContain('ADMIN_SESSION_DURATION_MS = 7 * 24 * 60 * 60 * 1000')
  })

  it('middleware still enforces admin-only on /admin/* routes', () => {
    const src = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(src).toMatch(/isAdminAccess[\s\S]*sessionRole === 'admin'/)
  })

  it('admin cookies still set 7-day Max-Age', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test', 'admin')
    expect(cookie).toContain('Max-Age=604800')
  })

  it('client cookies still set 30-day Max-Age', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test', 'client')
    expect(cookie).toContain('Max-Age=2592000')
  })

  it('prospect cookies match client (30-day Max-Age)', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test', 'prospect')
    expect(cookie).toContain('Max-Age=2592000')
  })
})
