/**
 * The rep training bundle (scripts/build-rep-bundle.ts) is built from the repo
 * and checked as a new representative would receive it: ten files, no dollar
 * amounts where a prospect could see one quoted, no client name, no em dash,
 * and none of the build-state disclaimers the capability ruling retired (a
 * capability is something SMD can configure the Operator to do; where it
 * stands for clients today is rep knowledge, in three labels).
 *
 * The build serves the real site in workerd, so this test takes tens of
 * seconds; it reads the output, not the repo's source text.
 */
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { parse } from 'yaml'
import { buildRepBundle } from '../scripts/build-rep-bundle'

const ROOT = resolve(import.meta.dirname, '..')
const DOLLAR = /\$\s?\d/
const EM_DASH = '—'

/**
 * Build-state disclaimers retired by the capability ruling. "not built" is
 * matched except as "not built for", which states design intent (the site's
 * "The Operator is not built for an industry. It is built for your business.").
 */
const DISCLAIMERS: { label: string; re: RegExp }[] = [
  { label: 'not built', re: /\bnot built\b(?! for\b)/i },
  { label: 'specified, not built', re: /specified, not built/i },
  { label: 'not yet in client use', re: /not yet in client use/i },
  { label: 'never described as available', re: /never described as available/i },
  { label: 'show only what is in production', re: /show only what is in production/i },
]

/**
 * Client names from every seat's customer.yaml. SMD's own seats (seat.kind
 * internal: "SMD Services" and the founder's personal seat, whose name the
 * public About page carries as SMD's founder) are not clients. A trailing
 * parenthetical note ("(SMD demo, fictional)") is not part of the name, and a
 * name is also checked without its entity suffix (LLP, LLC).
 */
function clientNames(): string[] {
  const dir = join(ROOT, 'operator', 'customers')
  const names = new Set<string>()
  for (const slug of readdirSync(dir)) {
    let raw: string
    try {
      raw = readFileSync(join(dir, slug, 'customer.yaml'), 'utf8')
    } catch {
      continue
    }
    const doc = parse(raw) as { customer_name?: unknown; seat?: { kind?: unknown } } | null
    if (typeof doc?.customer_name !== 'string' || doc.seat?.kind === 'internal') continue
    const name = doc.customer_name.replace(/\s*\(.*\)\s*$/, '').trim()
    if (!name || name === 'SMD Services') continue
    names.add(name)
    // "Ashton & Price" names the firm as surely as "Ashton & Price LLP".
    const core = name.replace(/,?\s+(LLP|LLC|PLLC|Inc\.?|PC)$/i, '').trim()
    if (core) names.add(core)
  }
  return [...names]
}

describe('the rep training bundle', () => {
  let outDir = ''
  let files: Record<string, string> = {}

  beforeAll(async () => {
    outDir = mkdtempSync(join(tmpdir(), 'rep-bundle-test-'))
    await buildRepBundle(outDir)
    files = Object.fromEntries(
      readdirSync(outDir).map((f) => [f, readFileSync(join(outDir, f), 'utf8')])
    )
  }, 300_000)

  afterAll(() => {
    if (outDir) rmSync(outDir, { recursive: true, force: true })
  })

  const byNumber = (n: string) => {
    const name = Object.keys(files).find((f) => f.startsWith(`${n} `))
    if (!name) throw new Error(`no bundle file numbered ${n}`)
    return { name, text: files[name] }
  }

  it('writes the ten numbered files, each opening with its source note', () => {
    expect(
      Object.keys(files)
        .map((f) => f.slice(0, 2))
        .sort()
    ).toEqual(['00', '01', '02', '03', '04', '05', '06', '07', '08', '09'])
    for (const [name, text] of Object.entries(files)) {
      expect(text.split('\n')[0], name).toMatch(/^Source: built from .+ at commit [0-9a-f]{7,} /)
      expect(text.length, name).toBeGreaterThan(1500)
    }
  })

  it('reads the website from the built pages, every industry page included', () => {
    expect(byNumber('02').text).toContain('Address: https://smd.services/operator')
    const packs = readdirSync(join(ROOT, 'src', 'pages', 'packs')).filter((f) =>
      f.endsWith('.astro')
    )
    const industry = byNumber('03').text
    for (const p of packs) {
      expect(industry).toContain(`Address: https://smd.services/packs/${p.replace(/\.astro$/, '')}`)
    }
    expect(byNumber('04').text).toContain('Address: https://smd.services/agent')
  })

  it('carries no dollar amount in the website files, the glossary or the common questions', () => {
    for (const n of ['02', '03', '04', '08', '09']) {
      const { name, text } = byNumber(n)
      expect(
        text.split('\n').filter((l) => DOLLAR.test(l)),
        name
      ).toEqual([])
    }
  })

  it('names no client', () => {
    const names = clientNames()
    expect(names.length).toBeGreaterThan(0)
    for (const [file, text] of Object.entries(files)) {
      const lower = text.toLowerCase()
      expect(
        names.filter((n) => lower.includes(n.toLowerCase())),
        file
      ).toEqual([])
    }
  })

  it('has no em dash', () => {
    for (const [file, text] of Object.entries(files)) {
      expect(text.includes(EM_DASH), file).toBe(false)
    }
  })

  it('carries none of the retired build-state disclaimers', () => {
    for (const [file, text] of Object.entries(files)) {
      expect(
        DISCLAIMERS.filter((d) => d.re.test(text)).map((d) => d.label),
        file
      ).toEqual([])
    }
  })
})
