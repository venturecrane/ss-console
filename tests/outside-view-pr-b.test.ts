/**
 * Outside View Phase 1 PR-B — dormant-pipeline coverage.
 *
 * The user-visible Outside View surfaces (portal page, OutsideViewArtifact
 * component, role-conditional PortalTabs) were retired in a separate PR;
 * the assertions for those layers were removed alongside the source files.
 * What remains here covers the dormant scan/diagnostic pipeline that is
 * still callable via direct API and may not be retired immediately.
 *
 * Static-source assertions covering:
 *   1. Feature flag wiring: env binding declared, helper checks "1"|"true"
 *   2. Workflow terminal step: shadow-write happens regardless of flag,
 *      privilege-escalation defense for existing client emails, magic-link
 *      mint uses 24h TTL, portal URL built from PORTAL_BASE_URL.
 *   3. Email template: outsideViewReadyEmailHtml + sendOutsideViewReadyEmail
 *      with branded sender, no fabricated commitments.
 *   4. getScanRequestByEntity helper — used by the workflow's re-run path.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
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

  it('terminal step calls writeOutsideViewArtifact before mintProspectMagicLink', () => {
    expect(src).toMatch(/writeOutsideViewArtifact/)
    expect(src).toMatch(/mintProspectMagicLink/)
    const idxArtifact = src.indexOf('writeOutsideViewArtifact(this.env')
    const idxMint = src.indexOf('mintProspectMagicLink(')
    expect(idxArtifact).toBeGreaterThan(0)
    expect(idxMint).toBeGreaterThan(idxArtifact)
  })

  it('shadow-write runs UNCONDITIONALLY (no role/flag gate before artifact write)', () => {
    // writeOutsideViewArtifact must run before mintProspectMagicLink AND
    // before the flag check. The split decouples shadow-write from the
    // mint so client-role submitters still produce outside_views rows.
    const idxArtifact = src.indexOf('writeOutsideViewArtifact(this.env')
    const idxFlagCheck = src.indexOf(
      'isOutsideViewDeliveryOn(this.env.OUTSIDE_VIEW_PORTAL_DELIVERY)'
    )
    expect(idxArtifact).toBeGreaterThan(0)
    expect(idxFlagCheck).toBeGreaterThan(idxArtifact)
  })

  it('falls back to legacy email when flag OFF or magic-link unavailable', () => {
    expect(src).toMatch(
      /useOutsideViewEmail[\s\S]*sendOutsideViewReadyEmail[\s\S]*sendDiagnosticReportEmail/
    )
  })

  it('mintProspectMagicLink skips for any non-prospect existing role (client, admin, etc.)', () => {
    // The single guard `existingUser && existingUser.role !== 'prospect'`
    // covers both the privilege-escalation defense (client) AND the
    // admin-INSERT-throws bug class (Q4 in the code review).
    expect(src).toMatch(
      /existingUser && existingUser\.role !== 'prospect'[\s\S]*portalLinkUrl: null/
    )
  })

  it('magic-link uses 24h TTL (PROSPECT_MAGIC_LINK_EXPIRY_MS)', () => {
    expect(src).toMatch(/createMagicLink\([\s\S]*PROSPECT_MAGIC_LINK_EXPIRY_MS\s*\)/)
  })

  it('portal URL prefers PORTAL_BASE_URL, falls back to APP_BASE_URL', () => {
    expect(src).toMatch(/env\.PORTAL_BASE_URL\s*\?\?\s*env\.APP_BASE_URL/)
  })

  it('portal URL points at /auth/verify (not directly at /portal/outside-view)', () => {
    expect(src).toMatch(/\/auth\/verify\?token=/)
  })

  it('writeOutsideViewArtifact swallows errors and returns outsideViewId: null', () => {
    expect(src).toMatch(
      /catch\s*\(err\)\s*\{[\s\S]*writeOutsideViewArtifact failed[\s\S]*return\s*\{\s*outsideViewId:\s*null/
    )
  })

  it('mintProspectMagicLink swallows errors and returns portalLinkUrl: null', () => {
    expect(src).toMatch(
      /catch\s*\(err\)\s*\{[\s\S]*mintProspectMagicLink failed[\s\S]*return\s*\{\s*portalLinkUrl:\s*null/
    )
  })
})

describe('PR-B: workers/scan-workflow wrangler.toml binds PORTAL_BASE_URL', () => {
  const wranglerSrc = readFileSync(resolve('workers/scan-workflow/wrangler.toml'), 'utf-8')

  it('declares PORTAL_BASE_URL in [vars] (not just as a comment)', () => {
    expect(wranglerSrc).toMatch(/^PORTAL_BASE_URL\s*=\s*"https:\/\/portal\.smd\.services"/m)
  })

  it('declares OUTSIDE_VIEW_PORTAL_DELIVERY in [vars] for explicit flag state', () => {
    expect(wranglerSrc).toMatch(/^OUTSIDE_VIEW_PORTAL_DELIVERY\s*=\s*"[01]"/m)
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
