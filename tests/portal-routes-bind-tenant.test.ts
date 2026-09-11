/**
 * Guard: every portal route binds the tenant itself.
 *
 * `enforcePortalAuth` in src/middleware.ts admits ANY signed-in Clerk user (or
 * a legacy client session) with no role or tenant check; tenant binding is per
 * route through one of the helpers below. That makes a portal route that
 * forgets its helper a cross-tenant read, and until 2026-09-10 nothing
 * enumerated the set (2026-09-09 review Security #2; the admin side has had
 * tests/admin-routes-require-session.test.ts since 2026-07-02).
 *
 * Two sets are walked: the API routes under src/pages/api/portal and the page
 * routes under src/pages/portal. Consumption is a CALL SITE (`helper(`) or an
 * import of the helper, never a bare mention, so a comment naming the helper
 * does not satisfy the rule. Files that legitimately carry no helper are
 * listed in EXEMPT with their reason and pruned by the second test.
 */
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve } from 'path'

const API_ROOT = resolve('src/pages/api/portal')
const PAGE_ROOT = resolve('src/pages/portal')

/**
 * The tenant-binding vocabulary. Each resolves the signed-in user to their own
 * entity (getPortalClient) or to an instance that entity owns (the rest), or,
 * for the OAuth callback, verifies a state signed for a specific customer and
 * reviewer before any effect.
 */
const TENANT_HELPERS = [
  'getPortalClient',
  'resolveOperatorAccess',
  'resolveHostedAgentAccess',
  'authorizeAdvancedSettings',
  'validateStateOrReject',
] as const

const CALL_OR_IMPORT = new RegExp(
  `(?:\\b(?:${TENANT_HELPERS.join('|')})\\s*\\()|(?:import\\s*(?:type\\s*)?\\{[^}]*\\b(?:${TENANT_HELPERS.join('|')})\\b[^}]*\\}\\s*from)`
)

/** Routes that legitimately bind no tenant. Path, then why. */
const EXEMPT = new Map<string, string>([
  [
    'src/pages/api/portal/operator/settings/trust-ceiling.ts',
    '410 tombstone: refuses every verb, reads nothing (ADR 0056 retired the ceiling)',
  ],
  ...(['account', 'configure', 'people', 'scope', 'work'] as const).map(
    (segment) =>
      [
        `src/pages/portal/products/operator/[instance]/${segment}/index.astro`,
        'redirect tombstone: echoes the caller-supplied instance param into a redirect, reads nothing',
      ] as [string, string]
  ),
])

const HTTP_HANDLER = /export\s+const\s+(GET|POST|PUT|PATCH|DELETE|ALL)\b/

function walk(root: string): string[] {
  return readdirSync(root, { recursive: true })
    .map((entry) => String(entry))
    .filter((rel) => (rel.endsWith('.ts') || rel.endsWith('.astro')) && !rel.endsWith('.test.ts'))
    .map((rel) => `${root}/${rel}`)
}

function relative(file: string): string {
  return file.replace(`${resolve('.')}/`, '')
}

function offenders(files: string[], isRoute: (src: string, file: string) => boolean): string[] {
  const out: string[] = []
  for (const file of files) {
    const rel = relative(file)
    if (EXEMPT.has(rel)) continue
    const src = readFileSync(file, 'utf8')
    if (!isRoute(src, file)) continue
    if (!CALL_OR_IMPORT.test(src)) out.push(rel)
  }
  return out
}

describe('portal tenant-binding convention', () => {
  it('every portal API route calls a tenant-binding helper (or is a documented exception)', () => {
    const files = walk(API_ROOT)
    expect(files.length).toBeGreaterThanOrEqual(15)
    expect(offenders(files, (src) => HTTP_HANDLER.test(src))).toEqual([])
  })

  it('every portal page route calls a tenant-binding helper (or is a documented exception)', () => {
    const files = walk(PAGE_ROOT)
    expect(files.length).toBeGreaterThanOrEqual(25)
    // Every .astro file under src/pages is a page; a .ts file is a route only
    // when it exports a handler.
    expect(
      offenders(files, (src, file) => file.endsWith('.astro') || HTTP_HANDLER.test(src))
    ).toEqual([])
  })

  it('every EXEMPT entry still exists (prunes stale exceptions)', () => {
    for (const rel of EXEMPT.keys()) {
      expect(statSync(resolve(rel)).isFile()).toBe(true)
    }
  })

  it('the vocabulary regex matches a call site and an import, not a mention', () => {
    expect(CALL_OR_IMPORT.test('const data = await getPortalClient(env.DB, locals)')).toBe(true)
    expect(CALL_OR_IMPORT.test("import { resolveOperatorAccess } from '../x'")).toBe(true)
    expect(CALL_OR_IMPORT.test('// see getPortalClient for the tenant rule')).toBe(false)
  })
})
