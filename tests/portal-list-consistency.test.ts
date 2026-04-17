import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Regression guard for unified portal list scaffolding. Originally from
 * PRs #413-415 when the three list pages shared canonical inline markup.
 *
 * After UI-PATTERNS R7 (portal list-row registry), the canonical markup
 * lives in `src/components/portal/PortalListItem.astro`. The scaffolding
 * contract is now: each list page imports the primitive, uses the shared
 * spacing-rhythm container, and uses the token-based h1 class that
 * replaced the literal slate-900 classes.
 *
 * The per-row rendering assertions from the pre-registry version moved
 * to `tests/forbidden-strings.test.ts` (R7 presence assertion).
 */

const CANONICAL_H1_CLASS = 'text-title text-[color:var(--color-text-primary)] mb-section'
const CANONICAL_LIST_CONTAINER = 'space-y-row'

const PAGES = [
  { name: 'Proposals', path: 'src/pages/portal/quotes/index.astro' },
  { name: 'Invoices', path: 'src/pages/portal/invoices/index.astro' },
  { name: 'Documents', path: 'src/pages/portal/documents/index.astro' },
] as const

describe('portal list pages: unified scaffolding (R7 registry)', () => {
  for (const page of PAGES) {
    const source = () => readFileSync(resolve(page.path), 'utf-8')

    describe(page.name, () => {
      it('uses the canonical token-based h1 class', () => {
        expect(source()).toContain(CANONICAL_H1_CLASS)
      })

      it('uses space-y-row for the list container', () => {
        expect(source()).toContain(CANONICAL_LIST_CONTAINER)
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
