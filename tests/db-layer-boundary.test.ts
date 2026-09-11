/**
 * Layer boundaries in src/lib, made mechanical.
 *
 * Two edges the 2026-09-10 code review found and this PR removed, pinned so
 * they cannot return:
 *
 *   1. The data layer (`src/lib/db/`) called UPWARD into four sibling
 *      domains (stripe, sow, follow-ups, booking), and the Stripe layer
 *      imported a pricing rule back DOWN from the data layer. A data module
 *      that imports a domain module cannot be read on its own, and the
 *      domain module cannot be mocked out from under it.
 *   2. The Operator split: `src/lib/operator/` is the lower layer and
 *      `src/lib/portal/` presents it. One value import ran the other way
 *      (the entitlement compiler reading the governance floors) and a
 *      second (the MCP resolver reading the connector projection parser).
 *
 * What makes this test red: any `import ... from '<path>'` in a non-test file
 * under the guarded root whose resolved target enters a forbidden directory.
 * `import type` is allowed on the Operator edge (erased at compile time, no
 * runtime coupling) and forbidden on the data-layer edge, where even a type
 * dependency on a sibling domain is the smell.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'fs'
import { resolve, dirname, relative, sep } from 'path'

const SRC = resolve('src/lib')

function walk(root: string): string[] {
  return readdirSync(root, { recursive: true })
    .map((entry) => String(entry))
    .filter((rel) => /\.(ts|tsx)$/.test(rel) && !/\.test\.tsx?$/.test(rel))
    .map((rel) => `${root}${sep}${rel}`)
}

interface ImportEdge {
  file: string
  specifier: string
  typeOnly: boolean
  target: string
}

const IMPORT_RE = /^\s*(?:import|export)\s+(type\s+)?(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/gm

function edges(file: string): ImportEdge[] {
  const src = readFileSync(file, 'utf8')
  const out: ImportEdge[] = []
  for (const match of src.matchAll(IMPORT_RE)) {
    const specifier = match[2]
    if (!specifier.startsWith('.')) continue
    out.push({
      file,
      specifier,
      typeOnly: match[1] !== undefined,
      target: resolve(dirname(file), specifier),
    })
  }
  return out
}

function rel(p: string): string {
  return relative(resolve('.'), p)
}

function under(target: string, dir: string): boolean {
  const d = resolve(dir)
  return target === d || target.startsWith(`${d}${sep}`)
}

describe('src/lib/db never imports from a sibling domain', () => {
  const FORBIDDEN = ['src/lib/stripe', 'src/lib/sow', 'src/lib/follow-ups', 'src/lib/booking']

  it('has no value or type import into stripe, sow, follow-ups, or booking', () => {
    const offenders: string[] = []
    for (const file of walk(`${SRC}${sep}db`)) {
      for (const edge of edges(file)) {
        if (FORBIDDEN.some((dir) => under(edge.target, dir))) {
          offenders.push(`${rel(file)} -> ${edge.specifier}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the Stripe layer does not import the card-fee rule from the data layer', () => {
    const offenders: string[] = []
    for (const file of walk(`${SRC}${sep}stripe`)) {
      for (const edge of edges(file)) {
        if (under(edge.target, 'src/lib/db') && /invoices$/.test(edge.target) && !edge.typeOnly) {
          const src = readFileSync(file, 'utf8')
          if (/cardProcessingFeeCents|CARD_FEE_LINE_DESCRIPTION/.test(src)) {
            offenders.push(`${rel(file)} -> ${edge.specifier}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('src/lib/operator never imports a value from src/lib/portal', () => {
  it('has no runtime import from operator/** into portal/**', () => {
    const offenders: string[] = []
    for (const file of walk(`${SRC}${sep}operator`)) {
      for (const edge of edges(file)) {
        if (under(edge.target, 'src/lib/portal') && !edge.typeOnly) {
          offenders.push(`${rel(file)} -> ${edge.specifier}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('the scan can fail', () => {
  it('detects an upward edge in a fixture the same way it would in the tree', () => {
    const fixture = "import { thing } from '../stripe/client'\nexport const x = thing\n"
    const found = [...fixture.matchAll(IMPORT_RE)].map((m) => m[2])
    expect(found).toEqual(['../stripe/client'])
    expect(under(resolve('src/lib/db', '../stripe/client'), 'src/lib/stripe')).toBe(true)
  })

  it('classifies a type-only import as type-only', () => {
    const fixture = "import type { T } from '../../portal/customer-config'\n"
    const m = [...fixture.matchAll(IMPORT_RE)][0]
    expect(m[1]).toBe('type ')
  })
})
