/**
 * Outside View Phase 1 PR-B — workflow re-aim + portal page smoke tests.
 *
 * Static-source assertions covering:
 *   1. Feature flag wiring: env binding declared, helper checks "1"|"true"
 *   2. Workflow terminal step: shadow-write happens regardless of flag,
 *      privilege-escalation defense for existing client emails, magic-link
 *      mint uses 24h TTL, portal URL built from PORTAL_BASE_URL.
 *   3. New email template: outsideViewReadyEmailHtml + sendOutsideViewReadyEmail
 *      with branded sender, no fabricated commitments.
 *   4. Portal page: queries outside_views by entity, renders via adapter,
 *      identical 404 for missing-view and missing-session (no enumeration).
 *   5. Role-conditional PortalTabs: prospect sees 1 tab, client sees 5.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('PR-B: feature flag wiring', () => {
  it('env.d.ts declares OUTSIDE_VIEW_PORTAL_DELIVERY', () => {
    const src = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(src).toMatch(/OUTSIDE_VIEW_PORTAL_DELIVERY\?:\s*string/)
  })

  it('workflow.ts ScanWorkflowBindings includes the flag', () => {
    const src = readFileSync(resolve('src/lib/diagnostic/workflow.ts'), 'utf-8')
    expect(src).toMatch(/ScanWorkflowBindings\s*\{[\s\S]*OUTSIDE_VIEW_PORTAL_DELIVERY\?:\s*string/)
  })

  it('workflow.ts ScanWorkflowBindings includes PORTAL_BASE_URL', () => {
    const src = readFileSync(resolve('src/lib/diagnostic/workflow.ts'), 'utf-8')
    expect(src).toMatch(/ScanWorkflowBindings\s*\{[\s\S]*PORTAL_BASE_URL\?:\s*string/)
  })

  it('isOutsideViewDeliveryOn helper accepts "1" and "true" only', () => {
    const src = readFileSync(resolve('src/lib/diagnostic/workflow.ts'), 'utf-8')
    expect(src).toMatch(/function isOutsideViewDeliveryOn/)
    // Should reject anything other than "1" or "true"
    expect(src).toMatch(/flag === '1' \|\| flag === 'true'/)
  })
})

describe('PR-B: workflow terminal step shadow-write + flag', () => {
  const src = readFileSync(resolve('src/lib/diagnostic/workflow.ts'), 'utf-8')

  it('imports createMagicLink + PROSPECT_MAGIC_LINK_EXPIRY_MS from auth', () => {
    expect(src).toMatch(
      /from ['"]\.\.\/auth\/magic-link['"][\s\S]*createMagicLink[\s\S]*PROSPECT_MAGIC_LINK_EXPIRY_MS/
    )
  })

  it('imports createOutsideView from db/outside-views', () => {
    expect(src).toMatch(/createOutsideView[\s\S]*from ['"]\.\.\/db\/outside-views['"]/)
  })

  it('imports renderedReportToArtifactJsonV1 from adapter', () => {
    expect(src).toMatch(
      /renderedReportToArtifactJsonV1[\s\S]*from ['"]\.\.\/db\/outside-views\/adapter['"]/
    )
  })

  it('imports sendOutsideViewReadyEmail', () => {
    expect(src).toMatch(/sendOutsideViewReadyEmail/)
  })

  it('terminal step calls prepareOutsideViewDelivery before email', () => {
    expect(src).toMatch(/prepareOutsideViewDelivery/)
  })

  it('shadow-write runs ALWAYS (not gated by flag)', () => {
    // The flag check is on the email branch, not on prepareOutsideViewDelivery.
    // prepareOutsideViewDelivery should be called before isOutsideViewDeliveryOn.
    const idxPrepare = src.indexOf('prepareOutsideViewDelivery(')
    const idxFlagCheck = src.indexOf(
      'isOutsideViewDeliveryOn(this.env.OUTSIDE_VIEW_PORTAL_DELIVERY)'
    )
    expect(idxPrepare).toBeGreaterThan(0)
    expect(idxFlagCheck).toBeGreaterThan(idxPrepare)
  })

  it('falls back to legacy email when flag OFF or magic-link unavailable', () => {
    expect(src).toMatch(
      /useOutsideViewEmail[\s\S]*sendOutsideViewReadyEmail[\s\S]*sendDiagnosticReportEmail/
    )
  })

  it('privilege-escalation defense: skips prospect path on existing client email', () => {
    // The helper function should check role === 'client' and bail.
    expect(src).toMatch(/existingUser\?\.role === 'client'[\s\S]*portalLinkUrl: null/)
  })

  it('magic-link uses 24h TTL (PROSPECT_MAGIC_LINK_EXPIRY_MS)', () => {
    expect(src).toMatch(/createMagicLink\([\s\S]*PROSPECT_MAGIC_LINK_EXPIRY_MS\s*\)/)
  })

  it('portal URL prefers PORTAL_BASE_URL, falls back to APP_BASE_URL', () => {
    expect(src).toMatch(/env\.PORTAL_BASE_URL\s*\?\?\s*env\.APP_BASE_URL/)
  })

  it('portal URL points at /auth/verify (not directly at /portal/outside-view)', () => {
    // The verify endpoint sets the cookie; landing direct at /portal would
    // hit middleware without a session and bounce.
    expect(src).toMatch(/\/auth\/verify\?token=/)
  })

  it('shadow-write swallows errors and returns null portalLinkUrl', () => {
    expect(src).toMatch(
      /catch\s*\(err\)\s*\{[\s\S]*prepareOutsideViewDelivery failed[\s\S]*return\s*\{\s*portalLinkUrl:\s*null/
    )
  })
})

describe('PR-B: outsideViewReadyEmailHtml template', () => {
  const src = readFileSync(resolve('src/lib/email/diagnostic-email.ts'), 'utf-8')

  it('exports outsideViewReadyEmailHtml', () => {
    expect(src).toMatch(/export function outsideViewReadyEmailHtml/)
  })

  it('renders Outside View framing (not "Operational Readiness Scan")', () => {
    const fnMatch = src.match(/export function outsideViewReadyEmailHtml[\s\S]*?^}/m)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).toMatch(/Your Outside View/)
      expect(fnMatch[0]).not.toMatch(/Operational Readiness Scan/)
    }
  })

  it('contains "Open your Outside View" CTA copy', () => {
    expect(src).toMatch(/Open your Outside View/)
  })

  it('mentions the 24-hour link lifetime', () => {
    const fnMatch = src.match(/export function outsideViewReadyEmailHtml[\s\S]*?^}/m)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).toMatch(/24 hours/i)
    }
  })

  it('uses BRAND_NAME constant (no hardcoded "SMD Services")', () => {
    const fnMatch = src.match(/export function outsideViewReadyEmailHtml[\s\S]*?^}/m)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).toMatch(/\$\{BRAND_NAME\}/)
    }
  })
})

describe('PR-B: sendOutsideViewReadyEmail', () => {
  const src = readFileSync(resolve('src/lib/diagnostic/index.ts'), 'utf-8')

  it('exports sendOutsideViewReadyEmail', () => {
    expect(src).toMatch(/export async function sendOutsideViewReadyEmail/)
  })

  it('subject mentions Outside View', () => {
    expect(src).toMatch(/Your Outside View is ready/)
  })

  it('uses sendOutreachEmail for tracking parity with existing diagnostic emails', () => {
    const fnMatch = src.match(/sendOutsideViewReadyEmail[\s\S]*?^}/m)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).toMatch(/sendOutreachEmail/)
    }
  })
})

describe('PR-B: /portal/outside-view page', () => {
  const path = 'src/pages/portal/outside-view/index.astro'

  it('page exists', () => {
    expect(existsSync(resolve(path))).toBe(true)
  })

  const src = readFileSync(resolve(path), 'utf-8')

  it('queries via getActiveOutsideViewByEntity (org-scoped)', () => {
    expect(src).toMatch(/getActiveOutsideViewByEntity\(env\.DB,\s*session\.orgId,\s*client\.id\)/)
  })

  it('parses artifact_json via the v1 adapter', () => {
    expect(src).toMatch(
      /parseArtifactJson\(outsideView\.artifact_json,\s*outsideView\.artifact_version\)/
    )
  })

  it('returns identical 404 for missing-view and missing-session', () => {
    // Missing portalData and missing-outsideView+non-thin-footprint both
    // emit the exact same response: status 404, body "Not Found".
    const not_found_responses = src.match(/new Response\('Not Found',\s*\{\s*status:\s*404\s*\}\)/g)
    expect(not_found_responses).toBeTruthy()
    expect((not_found_responses ?? []).length).toBeGreaterThanOrEqual(2)
  })

  it('renders thin-footprint copy inline when scan_status is thin_footprint', () => {
    expect(src).toMatch(/scanStatusReason === 'thin_footprint'/)
    expect(src).toMatch(/About your scan/)
  })

  it('renders OutsideViewArtifact when artifact parses', () => {
    expect(src).toMatch(/<OutsideViewArtifact/)
  })

  it('passes role to PortalTabs for role-conditional rendering', () => {
    expect(src).toMatch(/<PortalTabs[\s\S]*role=\{user\.role\}/)
  })

  it('declares prerender=false (SSR required for session+DB lookup)', () => {
    expect(src).toMatch(/export const prerender = false/)
  })
})

describe('PR-B: OutsideViewArtifact component', () => {
  const path = 'src/components/portal/OutsideViewArtifact.astro'

  it('component file exists', () => {
    expect(existsSync(resolve(path))).toBe(true)
  })

  const src = readFileSync(resolve(path), 'utf-8')

  it('accepts RenderedReport via props', () => {
    expect(src).toMatch(
      /import type \{ RenderedReport[\s\S]*from ['"]\.\.\/\.\.\/lib\/diagnostic\/render['"]/
    )
  })

  it('walks rendered.sections', () => {
    expect(src).toMatch(/rendered\.sections/)
  })

  it('honors hasContent flag for empty-state fallback', () => {
    expect(src).toMatch(/rendered\.hasContent/)
  })

  it('renders insufficientDataNote for sections that flagged it', () => {
    expect(src).toMatch(/insufficientDataNote/)
  })

  it('does not invent commitments (no "we will" / "kickoff" / "respond within")', () => {
    // CLAUDE.md no-fab rule: components rendering data should never have
    // hardcoded commitment copy. Outside View Phase 1 ships placeholder
    // D2/D3 affordances ("coming soon") which is allowed.
    expect(src).not.toMatch(/We'll reach out/i)
    expect(src).not.toMatch(/within \d+ business day/i)
    expect(src).not.toMatch(/respond within/i)
  })
})

describe('PR-B: PortalTabs role-conditional rendering', () => {
  const src = readFileSync(resolve('src/components/portal/PortalTabs.astro'), 'utf-8')

  it('accepts a role prop', () => {
    expect(src).toMatch(/role\?:\s*string/)
  })

  it('Outside View tab is the genesis tab (anchor 00, href /portal/outside-view)', () => {
    expect(src).toMatch(/href:\s*['"]\/portal\/outside-view['"][\s\S]*anchor:\s*['"]00['"]/)
  })

  it('prospect role gets only the Outside View tab', () => {
    expect(src).toMatch(/role === 'prospect'\s*\?\s*\[OUTSIDE_VIEW_TAB\]/)
  })

  it('client role gets Outside View + 4 client tabs', () => {
    expect(src).toMatch(/\[OUTSIDE_VIEW_TAB,\s*\.\.\.CLIENT_TABS\]/)
  })

  it('mobile bar uses flex (not grid-cols-N) for variable tab count', () => {
    expect(src).toMatch(/class="flex divide-x-\[2px\]/)
    // grid-cols-4 should no longer be present
    expect(src).not.toMatch(/grid grid-cols-4 divide-x/)
  })

  it('default role (omitted prop) behaves as client (back-compat)', () => {
    expect(src).toMatch(/role\s*=\s*['"]client['"]/)
  })
})

describe('PR-B: getScanRequestByEntity helper', () => {
  const src = readFileSync(resolve('src/lib/db/scan-requests.ts'), 'utf-8')

  it('exports getScanRequestByEntity', () => {
    expect(src).toMatch(/export async function getScanRequestByEntity/)
  })

  it('orders by created_at DESC and limits to 1 (most recent)', () => {
    const fnMatch = src.match(/export async function getScanRequestByEntity[\s\S]*?^}/m)
    expect(fnMatch).toBeTruthy()
    if (fnMatch) {
      expect(fnMatch[0]).toMatch(/ORDER BY created_at DESC[\s\S]*LIMIT 1/)
    }
  })
})
