import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'fs'
import { resolve, join } from 'path'

const srcDir = resolve('src')
const componentsDir = resolve('src/components')

function readComponent(name: string): string {
  return readFileSync(join(componentsDir, name), 'utf-8')
}

function readAllSrcFiles(): string[] {
  const files: string[] = []
  function walk(dir: string) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const fullPath = join(dir, entry.name)
      if (entry.isDirectory()) {
        walk(fullPath)
      } else if (entry.name.endsWith('.astro') || entry.name.endsWith('.ts')) {
        files.push(fullPath)
      }
    }
  }
  walk(srcDir)
  return files
}

describe('component existence', () => {
  const expectedComponents = [
    'CtaButton.astro',
    'Hero.astro',
    'ProblemCards.astro',
    'HowItWorks.astro',
    'WhatYouGet.astro',
    'Pricing.astro',
    'About.astro',
    'WhoWeHelp.astro',
    'FinalCta.astro',
    'Footer.astro',
    'JsonLd.astro',
  ]

  it.each(expectedComponents)('%s exists', (component) => {
    expect(existsSync(join(componentsDir, component))).toBe(true)
  })
})

describe('content integrity', () => {
  it('no dollar amounts published in src/', () => {
    const files = readAllSrcFiles()
    const dollarPattern = /\$[\d,]+/
    for (const filePath of files) {
      const content = readFileSync(filePath, 'utf-8')
      expect(content, `Dollar amount found in ${filePath}`).not.toMatch(dollarPattern)
    }
  })
})

describe('voice standard', () => {
  const marketingComponents = [
    'Hero.astro',
    'ProblemCards.astro',
    'HowItWorks.astro',
    'WhatYouGet.astro',
    'About.astro',
    'WhoWeHelp.astro',
    'FinalCta.astro',
  ]

  it.each(marketingComponents)('%s does not use first-person singular "I "', (component) => {
    const content = readComponent(component)
    // Check for "I " as a standalone word (not inside owner quotes like "I can't take a day off")
    // Exclude lines that are clearly customer quotes
    const lines = content.split('\n')
    for (const line of lines) {
      // Skip lines that are clearly customer/problem quotes
      if (line.includes('quote:') || line.includes('"I ') || line.includes("'I ")) continue
      // Check for standalone "I " as the author's voice (beginning of sentence)
      const stripped = line.replace(/['"][^'"]*['"]/g, '')
      expect(stripped, `First-person "I " found in ${component}: ${line.trim()}`).not.toMatch(
        /\bI\s(?!can't|don't|have|personally|text)/
      )
    }
  })
})

describe('JSON-LD schema', () => {
  it('JsonLd.astro contains LocalBusiness type', () => {
    const content = readComponent('JsonLd.astro')
    expect(content).toContain('LocalBusiness')
  })

  it('JsonLd.astro contains schema.org context', () => {
    const content = readComponent('JsonLd.astro')
    expect(content).toContain('https://schema.org')
  })
})

describe('decision compliance', () => {
  it('no "free assessment" language in any src file', () => {
    const files = readAllSrcFiles()
    for (const filePath of files) {
      const content = readFileSync(filePath, 'utf-8').toLowerCase()
      expect(content, `"free assessment" found in ${filePath}`).not.toContain('free assessment')
      expect(content, `"free consultation" found in ${filePath}`).not.toContain('free consultation')
    }
  })

  it('no "the consultant" language in marketing components', () => {
    const files = readAllSrcFiles()
    for (const filePath of files) {
      if (!filePath.endsWith('.astro')) continue
      const content = readFileSync(filePath, 'utf-8').toLowerCase()
      expect(content, `"the consultant" found in ${filePath}`).not.toContain('the consultant')
    }
  })
})
