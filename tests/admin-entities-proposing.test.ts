import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * The Proposing tab of the admin entities list renders nothing for an entity
 * with no active quote, never a placeholder sentence (empty-state pattern).
 */
describe('admin/entities/index: Proposing tab quote status', () => {
  // Quote status badge and relativeTime were extracted to EntityListRow.astro to
  // keep index.astro within the 500-line ceiling. Combined source covers both.
  const source = () =>
    readFileSync(resolve('src/pages/admin/entities/index.astro'), 'utf-8') +
    '\n' +
    readFileSync(resolve('src/components/admin/EntityListRow.astro'), 'utf-8') +
    '\n' +
    readFileSync(resolve('src/lib/admin/entity-row-view.ts'), 'utf-8')

  it('renders nothing when no active quote is present (no placeholder copy)', () => {
    // CLAUDE.md: render nothing rather than a fabricated "No quote yet" string.
    const code = source()
    expect(code).not.toContain('No quote yet')
    expect(code).toContain('r.activeQuote && (')
  })
})
