/**
 * Every top-level admin surface is reachable from the admin nav.
 *
 * WHY THIS EXISTS. On 2026-09-17 the obligation register shipped with a working
 * page at /admin/obligations, correct data in production, and no link to it
 * anywhere. The Captain opened the console and could not find it. That is the
 * built-but-not-wired failure (Law 9) — and it happened in the PR that built a
 * system whose entire purpose is catching work that exists but reaches nobody.
 *
 * A page nobody can navigate to is not shipped. This test is the mechanical
 * version of that sentence: it enumerates the top-level admin routes on disk and
 * requires each to be reachable from the nav spine, so the next surface added
 * without a link fails here rather than in front of the Captain.
 *
 * SCOPE, deliberately narrow: only TOP-LEVEL sections (src/pages/admin/<x>/ and
 * src/pages/admin/<x>.astro). Drill-in pages are reached from their parent, and
 * requiring a nav entry for every leaf would make the nav useless — the test
 * would then be enforcing noise rather than reachability.
 */

import { describe, expect, it } from 'vitest'
import { readdirSync, existsSync } from 'fs'
import { resolve } from 'path'
import { buildAdminNav, isAdminNavActive } from '../src/lib/admin/nav'

const ADMIN_PAGES = resolve(process.cwd(), 'src/pages/admin')

/**
 * Sections reached from inside another surface rather than from the spine.
 * Each entry names where it IS reachable from, so an exemption is a documented
 * routing decision and not a place to hide an orphan.
 */
const REACHED_FROM_ELSEWHERE: Record<string, string> = {
  settings: 'the gear affordance in the header (nav.ts: "Settings is NOT a spine word")',
  assessments: 'linked from a client/entity record',
  entities: 'linked from Clients',
  engagements: 'linked from a client record',
  'follow-ups': 'linked from Home',
  'hosted-agent': 'linked from Fleet',
  services: 'linked from Home (Delivery card)',
}

function topLevelSections(): string[] {
  return readdirSync(ADMIN_PAGES, { withFileTypes: true })
    .filter((e) => !e.name.startsWith('_') && !e.name.startsWith('['))
    .map((e) => (e.isDirectory() ? e.name : e.name.replace(/\.astro$/, '')))
    .filter((name) => name !== 'index')
}

describe('admin nav reachability', () => {
  const nav = buildAdminNav()

  it('exposes the obligation register on the spine', () => {
    // The specific regression: the register shipped with no way in.
    const owed = nav.find((d) => d.href === '/admin/obligations')
    expect(owed, '/admin/obligations must be a top-level admin destination').toBeTruthy()
    expect(owed?.label).toBe('Owed')
    expect(isAdminNavActive(owed!, '/admin/obligations')).toBe(true)
  })

  it('every top-level admin section is either on the spine or documented as reached elsewhere', () => {
    const sections = topLevelSections()
    expect(sections.length, 'expected to find admin sections on disk').toBeGreaterThan(3)

    const orphans = sections.filter((section) => {
      if (section in REACHED_FROM_ELSEWHERE) return false
      return !nav.some((d) => isAdminNavActive(d, `/admin/${section}`))
    })

    expect(
      orphans,
      `these admin sections have no nav entry and no documented route in:\n` +
        orphans.map((o) => `  /admin/${o}`).join('\n') +
        `\nAdd a nav destination, or record where it is linked from in REACHED_FROM_ELSEWHERE.`
    ).toEqual([])
  })

  it('every nav destination points at a page that exists', () => {
    // The mirror failure: a link to nothing. Without this, the test above could
    // be satisfied by adding nav entries for pages that were never built.
    for (const dest of nav) {
      const section = dest.href.replace(/^\/admin\/?/, '')
      if (section === '') continue
      const asDir = resolve(ADMIN_PAGES, section, 'index.astro')
      const asFile = resolve(ADMIN_PAGES, `${section}.astro`)
      expect(
        existsSync(asDir) || existsSync(asFile),
        `nav points at ${dest.href} but no page exists for it`
      ).toBe(true)
    }
  })

  it('the exemption list does not name sections that no longer exist', () => {
    // Falsifier for the exemption mechanism: a stale exemption silently widens
    // the hole the test is meant to close.
    const sections = new Set(topLevelSections())
    const stale = Object.keys(REACHED_FROM_ELSEWHERE).filter((s) => !sections.has(s))
    expect(stale, 'remove exemptions for sections that are gone').toEqual([])
  })
})
