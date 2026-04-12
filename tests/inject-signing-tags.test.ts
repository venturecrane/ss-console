import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('inject-signing-tags: module', () => {
  it('inject-signing-tags.ts exists', () => {
    expect(existsSync(resolve('src/lib/pdf/inject-signing-tags.ts'))).toBe(true)
  })

  it('exports injectSigningTags function', () => {
    const code = readFileSync(resolve('src/lib/pdf/inject-signing-tags.ts'), 'utf-8')
    expect(code).toContain('export async function injectSigningTags')
  })

  it('uses pdf-lib to inject text tags', () => {
    const code = readFileSync(resolve('src/lib/pdf/inject-signing-tags.ts'), 'utf-8')
    expect(code).toContain("from 'pdf-lib'")
    expect(code).toContain('drawText')
    expect(code).toContain('{{signature:1:y:')
    expect(code).toContain('{{date:1:y:')
  })

  it('uses spaces for empty tag fields (not empty colons)', () => {
    const code = readFileSync(resolve('src/lib/pdf/inject-signing-tags.ts'), 'utf-8')
    // Empty colons (::::) break SignWell's parser. Must use spaces (: : :).
    // Check only the drawText calls, not JSDoc comments.
    const drawCalls = code.split('\n').filter((l) => l.includes('drawText') || l.includes('{{'))
    const tagLines = drawCalls.join('\n')
    expect(tagLines).not.toContain('::::')
    expect(tagLines).toContain(': : :')
  })

  it('imports layout constants from signing-layout', () => {
    const code = readFileSync(resolve('src/lib/pdf/inject-signing-tags.ts'), 'utf-8')
    expect(code).toContain('SIGNING_PAGE')
    expect(code).toContain('PAGE_SIZE')
  })
})

describe('inject-signing-tags: render.ts integration', () => {
  it('render.ts imports and calls injectSigningTags', () => {
    const code = readFileSync(resolve('src/lib/pdf/render.ts'), 'utf-8')
    expect(code).toContain('injectSigningTags')
    expect(code).toContain("from './inject-signing-tags'")
  })

  it('renderSow calls injectSigningTags after Forme render', () => {
    const code = readFileSync(resolve('src/lib/pdf/render.ts'), 'utf-8')
    // The function should call renderDocument then injectSigningTags
    const renderIdx = code.indexOf('renderDocument')
    const injectIdx = code.indexOf('injectSigningTags(pdf)')
    expect(renderIdx).toBeGreaterThan(-1)
    expect(injectIdx).toBeGreaterThan(renderIdx)
  })
})
