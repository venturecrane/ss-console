/**
 * Every file under src/pages/api is a route.
 *
 * Astro registers every file under src/pages as a public URL, so a helper
 * module placed there occupies an address with no handler behind it. The
 * 2026-09-10 code review found two such modules under src/pages/api/booking
 * (reserve-helpers.ts, confirmation-emails.ts); they now live in
 * src/lib/booking/. This test keeps the directory honest: a .ts file here
 * exports an HTTP verb (or opts out of routing with `prerender`), or it does
 * not belong here.
 *
 * What makes it red: a .ts file under src/pages/api with no
 * `export const GET|POST|PUT|PATCH|DELETE|ALL|prerender`.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'fs'
import { resolve, relative, sep } from 'path'

const API_ROOT = resolve('src/pages/api')
const HTTP_HANDLER =
  /export\s+(?:const|async\s+function|function)\s+(GET|POST|PUT|PATCH|DELETE|ALL|prerender)\b/

function routeFiles(): string[] {
  return readdirSync(API_ROOT, { recursive: true })
    .map((entry) => String(entry))
    .filter((rel) => rel.endsWith('.ts') && !rel.endsWith('.test.ts'))
    .map((rel) => `${API_ROOT}${sep}${rel}`)
}

describe('src/pages/api holds routes only', () => {
  it('every .ts file exports an HTTP verb or prerender', () => {
    const offenders: string[] = []
    for (const file of routeFiles()) {
      const src = readFileSync(file, 'utf8')
      if (!HTTP_HANDLER.test(src)) offenders.push(relative(resolve('.'), file))
    }
    expect(offenders).toEqual([])
  })

  it('scans a non-empty tree', () => {
    expect(routeFiles().length).toBeGreaterThan(50)
  })

  it('the handler pattern would miss a helper module', () => {
    expect(HTTP_HANDLER.test('export function formatSlotLabelLong(a: string) { return a }')).toBe(
      false
    )
    expect(HTTP_HANDLER.test('export const POST: APIRoute = (ctx) => handlePost(ctx)')).toBe(true)
  })
})
