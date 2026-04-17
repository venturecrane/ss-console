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
