import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Regression guard for unified portal list scaffolding. Originally from
 * PRs #413-415 when the three list pages shared canonical inline markup.
 *
 * After UI-PATTERNS R7 (portal list-row registry), the canonical markup
 * lives in `src/components/portal/PortalListItem.astro`. After the
 * Plainspoken Sign Shop refit (2026-04-23, PR B), every list page also
 * renders its page head through the shared `PortalPageHead` component
 * and wraps ticket-chrome rows in a 3px ink-bordered container instead
 * of the old `space-y-row` soft-card stack.
 *
 * The per-row rendering assertions from the pre-registry version moved
 * to `tests/forbidden-strings.test.ts` (R7 presence assertion).
 */

const PAGES = [
  { name: 'Proposals', path: 'src/pages/portal/quotes/index.astro' },
  { name: 'Invoices', path: 'src/pages/portal/invoices/index.astro' },
  { name: 'Documents', path: 'src/pages/portal/documents/index.astro' },
] as const

describe('portal list pages: unified scaffolding (R7 registry)', () => {
  for (const page of PAGES) {
    const source = () => readFileSync(resolve(page.path), 'utf-8')

    describe(page.name, () => {
      it('renders page head through the shared PortalPageHead primitive', () => {
        expect(source()).toContain('<PortalPageHead')
      })

      it('wraps list rows in a Plainspoken ink-bordered ticket container', () => {
        // Ticket chrome replaces the previous soft-card space-y-row stack.
        // The ink-bordered wrapper is the structural contract: rows stitch
        // together with their own 2px dividers inside.
        expect(source()).toMatch(/border-\[3px\] border-\[color:var\(--ss-color-text-primary\)\]/)
      })

      it('imports PortalListItem primitive', () => {
        const code = source()
        expect(code).toContain(
          "import PortalListItem from '../../../components/portal/PortalListItem.astro'"
        )
      })

      it('renders rows through <PortalListItem>', () => {
        expect(source()).toContain('<PortalListItem')
      })

      it('does not use a "Your X" heading', () => {
        const code = source()
        expect(code).not.toMatch(/<h1[^>]*>\s*Your\s/i)
      })
    })
  }
})
