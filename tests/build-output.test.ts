import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'fs'
import { resolve, join } from 'path'

const distDir = resolve('dist')

function collectHtmlFiles(dir: string): string[] {
  const files: string[] = []
  if (!existsSync(dir)) return files
  function walk(d: string) {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const fullPath = join(d, entry.name)
      if (entry.isDirectory()) {
        walk(fullPath)
      } else if (entry.name.endsWith('.html')) {
        files.push(fullPath)
      }
    }
  }
  walk(dir)
  return files
}

describe('build output guard', () => {
  it('dist/ directory exists (run `npm run build` first)', () => {
    expect(existsSync(distDir)).toBe(true)
  })

  it('$2,500 does not appear in any built HTML file', () => {
    // Note: the built site has no prerendered HTML in dist/client/ (404 is
    // SSR, index is SSR, book/contact/scorecard/ai are all SSR). This
    // assertion passes trivially when nothing is prerendered. Kept in place
    // so that any future prerendered page gets scanned automatically.
    const htmlFiles = collectHtmlFiles(distDir)
    for (const filePath of htmlFiles) {
      const content = readFileSync(filePath, 'utf-8')
      expect(content, `$2,500 leaked into ${filePath}`).not.toContain('$2,500')
    }
  })

  it('"free assessment" does not appear in any built HTML file', () => {
    const htmlFiles = collectHtmlFiles(distDir)
    for (const filePath of htmlFiles) {
      const content = readFileSync(filePath, 'utf-8').toLowerCase()
      expect(content, `"free assessment" leaked into ${filePath}`).not.toContain('free assessment')
    }
  })
})
