/**
 * Regression guard: forbidden strings must not appear in shipped source files.
 *
 * These strings represent CLAUDE.md Pattern A/B violations (committed template
 * sentences promising uncontracted behavior, or hardcoded fallback identities).
 * Any re-introduction of these strings is a P0 compliance failure.
 *
 * @see CLAUDE.md — "No fabricated client-facing content"
 * @see docs/reviews/code-review-2026-04-16.md
 * @see GitHub issues #398
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve, join, extname } from 'path'

const SRC_ROOT = resolve('src')

/** Collect all .astro, .ts, .tsx files under src/ (excluding test files and dev harness) */
function collectSourceFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      // Exclude src/pages/dev/ — developer harness, not shipped client-facing code
      if (fullPath === resolve('src/pages/dev')) continue
      files.push(...collectSourceFiles(fullPath))
    } else {
      const ext = extname(entry)
      if (
        ['.astro', '.ts', '.tsx'].includes(ext) &&
        !entry.endsWith('.test.ts') &&
        !entry.endsWith('.test.tsx')
      ) {
        files.push(fullPath)
      }
    }
  }
  return files
}

const FORBIDDEN_PATTERNS: Array<{ label: string; pattern: RegExp | string }> = [
  {
    label: 'Pattern A: hardcoded kickoff outreach promise',
    pattern: "We'll reach out to schedule kickoff",
  },
  {
    label: 'Pattern A: hardcoded "within two weeks" start window',
    pattern: 'Work begins within two weeks',
  },
  {
    label: 'Pattern A: hardcoded SLA "Replies within 1 business day"',
    pattern: 'Replies within 1 business day',
  },
  {
    label: 'Pattern A: hardcoded "2-week stabilization period" duration',
    pattern: '2-week stabilization period',
  },
  {
    label: 'Pattern A: hardcoded "within 1 business day of receiving"',
    pattern: 'within 1 business day of receiving',
  },
  {
    label: 'Pattern A: hardcoded "within one business day" promise',
    pattern: 'within one business day',
  },
  {
    label: 'Pattern A: hardcoded "will reach out" consultant outreach promise',
    // 2026-04-17 audit finding: dashboard fallback rendered
    // `${consultantFirst} will reach out to schedule the next check-in.` as
    // fabricated next-step copy when no authored touchpoint existed.
    pattern: /will reach out/i,
  },
  {
    label: 'Pattern B: synthesized "Kickoff next:" next-step copy',
    // 2026-04-17 audit finding: signed-state copy synthesized
    // `Kickoff next: ${engagement.scope_summary}.` when next_touchpoint_label
    // was missing. scope_summary is not an authored next-step field.
    pattern: /Kickoff next:/,
  },
  {
    label: 'Pattern B: fabricated "Engagement work" invoice line-item fallback',
    // 2026-04-17 audit finding: invoice line-item fallback fabricated
    // 'Engagement work' when no line items existed. Send-gate now blocks
    // sending an invoice without line items; this guards re-introduction
    // of a client-facing placeholder.
    pattern: /['"]Engagement work['"]/,
  },
  {
    label: 'Pattern B: hardcoded "Scott" fallback in portal render paths',
    // Match ?? 'Scott' or : 'Scott' (ternary / nullish) in portal pages and components.
    // Does not flag 'Scott' in test fixtures, variable names, or email author strings.
    pattern: /\?\? ['"]Scott['"]/,
  },
  {
    label: "Pattern B: hardcoded default consultantFirstName = 'Scott'",
    pattern: /consultantFirstName\s*=\s*['"]Scott['"]/,
  },
]

const sourceFiles = collectSourceFiles(SRC_ROOT)

describe('forbidden-strings: Pattern A/B violations must not appear in shipped source', () => {
  for (const { label, pattern } of FORBIDDEN_PATTERNS) {
    it(`must not contain: ${label}`, () => {
      const violations: string[] = []
      for (const file of sourceFiles) {
        const content = readFileSync(file, 'utf-8')
        const matched =
          typeof pattern === 'string' ? content.includes(pattern) : pattern.test(content)
        if (matched) {
          // Compute a relative path for readable failure messages
          const rel = file.replace(SRC_ROOT, 'src')
          violations.push(rel)
        }
      }
      expect(violations).toEqual([])
    })
  }
})

// ============================================================================
// Portal list-row registry — UI-PATTERNS R7 enforcement.
//
// List-row markup drifted across portal surfaces (proposals, invoices,
// documents) when Stitch generated each screen in isolation. The registry
// collapses the pattern to one component (`PortalListItem.astro`) + two
// helper modules (`src/lib/portal/{formatters,status}.ts`). These tests
// enforce the registry at CI time so drift fails the build, not review.
//
// Scope: every `src/pages/portal/*/index.astro` EXCEPT the home dashboard
// (which iterates its timeline, not list rows). New portal list surfaces
// auto-enroll — explicit exceptions go in LIST_INDEX_ALLOWLIST with a
// comment explaining why.
// ============================================================================

const PORTAL_INDEX_ROOT = resolve('src/pages/portal')
const PORTAL_HOME = resolve('src/pages/portal/index.astro')

/**
 * Allowlist for portal list-index files that legitimately cannot use
 * `PortalListItem` (e.g., because they iterate something that isn't a
 * list-row card — timeline, form fields, milestone rail). Each entry needs
 * a comment explaining why.
 */
const LIST_INDEX_ALLOWLIST: string[] = [
  // `engagement/index.astro` is the engagement DETAIL surface (one
  // engagement per client), not a list of engagements. Its milestone
  // rendering is a vertical-timeline with marker-ring state semantics, not
  // a repeating card — a different primitive than `PortalListItem`. Track
  // as a follow-up if milestone rail drifts or gains a second use.
  resolve('src/pages/portal/engagement/index.astro'),
]

/** Collect every `index.astro` under `src/pages/portal/` EXCEPT the home. */
function collectPortalListIndexFiles(): string[] {
  const files: string[] = []
  function walk(dir: string): void {
    for (const entry of readdirSync(dir)) {
      const fullPath = join(dir, entry)
      const stat = statSync(fullPath)
      if (stat.isDirectory()) {
        walk(fullPath)
      } else if (entry === 'index.astro' && fullPath !== PORTAL_HOME) {
        files.push(fullPath)
      }
    }
  }
  walk(PORTAL_INDEX_ROOT)
  return files.filter((f) => !LIST_INDEX_ALLOWLIST.includes(f))
}

const portalListIndexFiles = collectPortalListIndexFiles()

describe('portal list-row registry: UI-PATTERNS R7 enforcement', () => {
  it('finds at least one portal list-index file to check (sanity)', () => {
    // If this fails, the file collection logic broke — not the registry.
    expect(portalListIndexFiles.length).toBeGreaterThan(0)
  })

  // ----------------------------------------------------------------
  // Presence assertion.
  //
  // If the file iterates (`.map(`), it must render through
  // `<PortalListItem`. Class-reorder evasion (the Devil's Advocate's
  // objection to "no forbidden markup string" assertions) is defeated
  // because this asserts PRESENCE of the primitive, not absence of
  // specific class strings.
  // ----------------------------------------------------------------
  for (const file of portalListIndexFiles) {
    const rel = file.replace(resolve('.') + '/', '')
    it(`${rel} — must render list rows through <PortalListItem>`, () => {
      const content = readFileSync(file, 'utf-8')
      const iteratesList = /\.map\(/.test(content)
      if (!iteratesList) return // not a list surface, no assertion
      const usesPrimitive = content.includes('<PortalListItem')
      expect(
        usesPrimitive,
        `${rel} iterates with .map( but does not render through <PortalListItem>. ` +
          `Portal list-row markup must go through src/components/portal/PortalListItem.astro.`
      ).toBe(true)
    })
  }

  // ----------------------------------------------------------------
  // No local helper redefinition.
  //
  // Drift starts when a page defines its own formatDate / statusColorMap
  // instead of importing from src/lib/portal/{formatters,status}.ts.
  // Catch it at the declaration site.
  // ----------------------------------------------------------------
  const FORBIDDEN_LOCAL_DECLARATIONS: Array<{ name: string; pattern: RegExp }> = [
    {
      name: 'formatDate',
      pattern: /^\s*(?:const|function)\s+formatDate\b/m,
    },
    {
      name: 'formatCurrency',
      pattern: /^\s*(?:const|function)\s+formatCurrency\b/m,
    },
    {
      name: 'statusColorMap',
      pattern: /^\s*const\s+statusColorMap\b/m,
    },
    {
      name: 'statusLabelMap',
      pattern: /^\s*const\s+statusLabelMap\b/m,
    },
    {
      name: 'typeLabels',
      // Matches `typeLabels` as a standalone const; does NOT match
      // `typeLabel` or `typeLabelMap` (detail pages use those for
      // one-off single-title mapping and are out of scope).
      pattern: /^\s*const\s+typeLabels\s*(?::|=)/m,
    },
  ]

  for (const file of portalListIndexFiles) {
    const rel = file.replace(resolve('.') + '/', '')
    for (const { name, pattern } of FORBIDDEN_LOCAL_DECLARATIONS) {
      it(`${rel} — must not redefine local helper \`${name}\``, () => {
        const content = readFileSync(file, 'utf-8')
        expect(
          pattern.test(content),
          `${rel} declares a local \`${name}\`. Import from ` +
            `src/lib/portal/formatters.ts or src/lib/portal/status.ts instead.`
        ).toBe(false)
      })
    }
  }
})
