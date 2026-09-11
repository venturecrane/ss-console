/**
 * docs/api/README.md is generated from src/pages/api and cannot drift.
 *
 * The 2026-09-10 code review found 98 route files and no API documentation,
 * with the issue that tracked it closed by a backlog reset. A hand-written page
 * would have gone stale by the next PR; this one is derived from the tree and
 * the test below fails whenever the committed page and the tree disagree, so a
 * route added, moved, retired, or re-gated without regenerating the page
 * cannot merge. Regenerate: `npx tsx scripts/api-inventory.ts --write`.
 *
 * The second block pins the invariants the inventory makes visible: no admin,
 * portal, or internal route reads as public, and no helper module sits under
 * src/pages/api where Astro would serve it as a URL (the two known ones are
 * listed so their removal turns this green rather than red).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { buildInventory, renderInventory, collectRouteFiles } from '../scripts/lib/api-inventory'

const REPO_ROOT = resolve('.')
const API_ROOT = resolve('src/pages/api')
const PAGE = resolve('docs/api/README.md')

const rows = buildInventory(API_ROOT, REPO_ROOT)

describe('API inventory page tracks the tree', () => {
  it('the page exists and matches what the generator produces from src/pages/api', () => {
    expect(existsSync(PAGE)).toBe(true)
    const committed = readFileSync(PAGE, 'utf8')
    const rendered = renderInventory(rows) + '\n'
    expect(committed).toBe(rendered)
  })

  it('every route file has a row and every row names a file that exists', () => {
    const files = collectRouteFiles(API_ROOT).map((f) => f.replace(REPO_ROOT + '/', ''))
    expect(rows.map((r) => r.file).sort()).toEqual(files.sort())
    for (const r of rows) expect(existsSync(resolve(r.file))).toBe(true)
  })

  it('the generator can see routes (sanity: the walk is not empty)', () => {
    expect(rows.length).toBeGreaterThan(50)
  })
})

describe('invariants the inventory makes visible', () => {
  it('no admin, portal, or internal route reads as public', () => {
    const offenders = rows
      .filter((r) => ['admin', 'portal', 'internal'].includes(r.area))
      .filter((r) => r.gate === 'public')
      .map((r) => r.file)
    expect(offenders).toEqual([])
  })

  it('no helper module lives under src/pages/api (Astro serves every file here as a URL)', () => {
    // The two that did (booking/confirmation-emails.ts, booking/reserve-helpers.ts)
    // moved to src/lib/booking on 2026-09-11; tests/api-routes-are-routes.test.ts
    // pins the same invariant from the source side.
    const helpers = rows
      .filter((r) => r.methods.startsWith('none'))
      .map((r) => r.file)
      .sort()
    expect(helpers).toEqual([])
  })

  it('the page carries no em dash', () => {
    expect(readFileSync(PAGE, 'utf8')).not.toContain('—')
  })
})
