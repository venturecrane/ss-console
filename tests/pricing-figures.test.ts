/**
 * Internal pricing figures stay out of ss-console.
 *
 * Venture policy: no dollar amount appears on any public surface, and the
 * Operator launch price specifically is "internal, never published" (ADR 0063,
 * decision #50). This repo is PUBLIC (`gh api repos/venturecrane/ss-console
 * --jq .private` returns false; visibility decision 2026-08), so this gate is
 * the load-bearing control, not defence in depth: the figures live in
 * `venturecrane/engagements:pricing/` where confidentiality is a property of
 * what the repo is for, and this test is what keeps them from coming back.
 *
 * WHAT MOVED IS THE NUMBER, NOT THE DECISION. ADR 0063 keeps its path, its
 * reasoning, its supersession of ADR 0004's deferred-pricing clause, and all
 * eight cross-references into it. `docs/handbook/pricing-economics.md` keeps
 * rendering at /admin/playbook. Decision #50 keeps its row. A decision record
 * that cannot explain itself is worth less than one that cannot quote a figure.
 *
 * This gate is deliberately narrow: it bans the SPECIFIC locked figures rather
 * than any currency-shaped string, because plenty of legitimate dollar amounts
 * belong here (third-party benchmarks, vendor list prices, cost ceilings).
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative, resolve } from 'node:path'

const REPO_ROOT = resolve('.')

/**
 * The locked internal figures, in every spelling they have appeared in.
 * Adding a price here is correct; adding an exemption below to silence a
 * failure is not.
 */
const BANNED = [
  /\$5,?000\s*\/\s*(mo|month)/i,
  /\$5k\s*\/\s*mo/i,
  /\$4,?000\s+(one-time\s+)?stand[- ]up/i,
  /\$4k\s+stand[- ]up/i,
  /\$3,?500\s*\/\s*mo/i, // the superseded working baseline
  /recurring_price\s*=\s*5000/i,
]

/**
 * Exemptions, each for a stated reason. Never add a path here to silence a
 * finding about our own current price.
 *
 * THIRD-PARTY BENCHMARKS that merely look like ours. `docs/research/paid-ads/`
 * cites LinkedIn ad-budget guidance of "$3,000-$5,000/mo" from a vendor blog,
 * and the archived lead-gen research cites a Google Ads SMB minimum of
 * "$3K-$5K/month". Both match the retainer pattern and neither is our pricing;
 * an earlier plan listed the paid-ads file for removal on exactly this false
 * positive.
 *
 * DATED RECORDS. `operator/grading/runs/` holds timestamped run logs whose
 * value is that they say what was true on their date. Rewriting them falsifies
 * the record, which is why `tests/forbidden-strings.test.ts` already excludes
 * the same directory from its retired-name scan. The convention is followed
 * here rather than contradicted.
 */
const EXEMPT_PREFIXES = [
  resolve('docs/research/paid-ads'),
  resolve('docs/archive'),
  resolve('operator/grading/runs'),
  resolve('tests/pricing-figures.test.ts'), // this file spells the patterns
]

const SCAN_ROOTS = ['docs', 'src', 'tests', 'operator', 'scripts', 'workers']
const TEXT_EXT = new Set(['.md', '.ts', '.tsx', '.astro', '.py', '.json', '.yaml', '.yml', '.sh'])

function walk(dir: string): string[] {
  if (!existsSync(dir)) return []
  return readdirSync(dir).flatMap((name) => {
    if (name === 'node_modules' || name === '.git') return []
    const full = join(dir, name)
    if (EXEMPT_PREFIXES.some((ex) => full === ex || full.startsWith(ex + '/'))) return []
    let st
    try {
      st = statSync(full)
    } catch {
      return []
    }
    if (st.isDirectory()) return walk(full)
    return TEXT_EXT.has(extname(full)) ? [full] : []
  })
}

describe('internal pricing figures', () => {
  const files = SCAN_ROOTS.flatMap((r) => walk(resolve(r)))

  it('finds files to scan (sanity)', () => {
    expect(files.length).toBeGreaterThan(100)
  })

  it('no locked pricing figure appears in ss-console', () => {
    const violations: string[] = []
    for (const file of files) {
      const src = readFileSync(file, 'utf-8')
      for (const pattern of BANNED) {
        const m = src.match(pattern)
        if (m) violations.push(`${relative(REPO_ROOT, file)}: "${m[0]}"`)
      }
    }
    expect(
      violations,
      'Internal pricing figures live in venturecrane/engagements:pricing/, not here. ' +
        'Describe the decision and point at that path; do not restate the amount.\n' +
        violations.join('\n')
    ).toEqual([])
  })

  it('the decision record survived the redaction', () => {
    // The failure mode of a redaction sweep is deleting the reasoning along
    // with the number. ADR 0063 must still exist, still be reachable, and
    // still say why the price is what it is.
    const adr = resolve('docs/adr/0063-operator-launch-pricing.md')
    expect(existsSync(adr), 'ADR 0063 must keep its path: eight documents link to it').toBe(true)

    const src = readFileSync(adr, 'utf-8')
    expect(src, 'the salary-anchor rationale is the decision').toMatch(/salary anchor/i)
    expect(src, 'the supersession must survive').toMatch(/supersede/i)
    expect(src, 'the ADR must name where the figures went').toContain(
      'venturecrane/engagements:pricing/'
    )

    const handbook = resolve('docs/handbook/pricing-economics.md')
    expect(existsSync(handbook), 'the handbook page still renders at /admin/playbook').toBe(true)
  })
})
