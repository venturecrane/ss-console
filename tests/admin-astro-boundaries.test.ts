import { describe, expect, it } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const entityPageSrc = readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')
const quotePageSrc = readFileSync(
  resolve('src/pages/admin/entities/[id]/quotes/[quoteId].astro'),
  'utf-8'
)
const engagementPageSrc = readFileSync(resolve('src/pages/admin/engagements/[id].astro'), 'utf-8')

function lineCount(source: string): number {
  return source.split('\n').length
}

describe('admin Astro boundary rules', () => {
  it('keeps direct DOM wiring out of the Astro page source', () => {
    for (const pageSrc of [entityPageSrc, quotePageSrc, engagementPageSrc]) {
      expect(pageSrc).not.toContain('document.getElementById')
      expect(pageSrc).not.toContain('addEventListener(')
      expect(pageSrc).not.toContain('querySelectorAll<')
    }
  })

  it('caps the largest admin detail surfaces at their new concern boundaries', () => {
    expect(lineCount(entityPageSrc)).toBeLessThanOrEqual(1050)
    expect(lineCount(quotePageSrc)).toBeLessThanOrEqual(900)
    expect(lineCount(engagementPageSrc)).toBeLessThanOrEqual(850)
  })
})
