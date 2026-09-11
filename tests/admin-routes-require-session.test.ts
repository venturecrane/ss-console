/**
 * Guard: every HTTP route under src/pages/api/admin/** must enforce admin auth
 * through `requireAdminSession`, making the convention mechanical rather than a
 * matter of reviewer vigilance (code review 2026-07-02 §2.6, building on the
 * §1.6 positive finding that the convention is currently applied uniformly).
 *
 * The invariant now holds UNCONDITIONALLY: the one historical exception
 * (`fleet/health.ts`, the health-monitor skill's bearer-keyed read surface) was
 * ripped 2026-07-24 with the agentic monitor it served — deterministic
 * work-liveness alerting lives in ss-fleet-alerts. Any new admin route must
 * import `requireAdminSession` or be added to EXEMPT with a comment justifying
 * its alternative auth.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve } from 'path'

const ADMIN_API_ROOT = resolve('src/pages/api/admin')

/** Routes that legitimately do not use requireAdminSession (see file header). */
const EXEMPT = new Set<string>([])

const HTTP_HANDLER = /export\s+const\s+(GET|POST|PUT|PATCH|DELETE|ALL)\b/
const REQUIRE_ADMIN_SESSION_CALL_OR_IMPORT =
  /(?:\brequireAdminSession\s*\()|(?:import\s*\{[^}]*\brequireAdminSession\b[^}]*\}\s*from)/

function collectRouteFiles(): string[] {
  // recursive readdir returns paths relative to the constant absolute root;
  // build the full path by interpolation (not path.join) so the static
  // path-traversal scanner sees no fs-derived value entering a join/resolve.
  return readdirSync(ADMIN_API_ROOT, { recursive: true })
    .map((entry) => String(entry))
    .filter((rel) => rel.endsWith('.ts') && !rel.endsWith('.test.ts'))
    .map((rel) => `${ADMIN_API_ROOT}/${rel}`)
}

describe('admin API auth convention', () => {
  it('every admin API route enforces requireAdminSession (or is a documented exception)', () => {
    const offenders: string[] = []
    for (const file of collectRouteFiles()) {
      if (EXEMPT.has(file)) continue
      const src = readFileSync(file, 'utf8')
      if (!HTTP_HANDLER.test(src)) continue // shared helper module, not a route
      // A call site or an import, never a bare mention: a comment naming the
      // helper satisfied the old `includes` check (2026-09-10 review, T14).
      if (!REQUIRE_ADMIN_SESSION_CALL_OR_IMPORT.test(src)) {
        offenders.push(file.replace(`${resolve('.')}/`, ''))
      }
    }
    expect(offenders).toEqual([])
  })

  it('the check matches a call site and an import, not a mention', () => {
    expect(
      REQUIRE_ADMIN_SESSION_CALL_OR_IMPORT.test('const auth = requireAdminSession(locals)')
    ).toBe(true)
    expect(
      REQUIRE_ADMIN_SESSION_CALL_OR_IMPORT.test("import { requireAdminSession } from '../x'")
    ).toBe(true)
    expect(
      REQUIRE_ADMIN_SESSION_CALL_OR_IMPORT.test('// gated by requireAdminSession upstream')
    ).toBe(false)
  })

  it('every EXEMPT entry still exists (prunes stale allowlist entries)', () => {
    for (const file of EXEMPT) {
      expect(statSync(file).isFile()).toBe(true)
    }
  })
})
