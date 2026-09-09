/**
 * Integrity gate for docs/doctrine/agent-operating-doctrine.md (the operating-law
 * registry) and its always-on surface, .claude/hooks/reflex-primer.sh.
 *
 * Why this exists (2026-07-26, the Christa-reply session): a five-word review
 * request went sideways because the venture's 72 recorded corrections lived as
 * prose behind index pointers with no binding to the moment of action. The
 * doctrine registry distills them into laws, each carrying its incidents and its
 * enforcement mechanism, honestly labeled by tier. This test is the registry's
 * conformance gate in the handbook-integrity idiom:
 *
 *   1. every law parses against a closed schema (id / primer_line / cost /
 *      tier / enforcement / incidents / escalation)
 *   2. every enforcement pointer resolves to a real file (a law claiming gate
 *      coverage that does not exist is the C2 failure class in a registry row)
 *   3. every law cites at least one dated incident (rules trace to harm; that
 *      is what keeps them from being deleted as noise)
 *   4. every primer_line appears VERBATIM in reflex-primer.sh (the doctrine
 *      file is the single source of truth; the primer is a rendered copy, and
 *      unpinned copies drift -- the exact failure the crane Directives block
 *      has, ported here with a test so it cannot happen to the laws)
 *   5. no em dashes (venture style law)
 *   6. structural: every real engagement with a correspondence/ archive has a
 *      dossier.md (Law 2 has nothing to gate on otherwise)
 *   7. mechanisms under review carry a falsifiable success criterion and a
 *      review date (a mechanism that cannot be demoted is ceremony)
 *
 * @see docs/doctrine/agent-operating-doctrine.md
 */
import { describe, it, expect } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
import { join, resolve } from 'path'
import { parse as parseYaml } from 'yaml'

const DOCTRINE_PATH = resolve('docs/doctrine/agent-operating-doctrine.md')
const PRIMER_PATH = resolve('.claude/hooks/reflex-primer.sh')
const CUSTOMERS_ROOT = resolve('operator/customers')

const TIERS = ['gate', 'radar', 'primer', 'prose'] as const
const COSTS = ['high', 'medium', 'low'] as const

interface Law {
  id: string
  primer_line: string
  cost: string
  tier: string
  enforcement: string[]
  incidents: Array<{ date: string; ref: string }>
  escalation: string
}

interface Mechanism {
  id: string
  file: string
  hypothesis: string
  success_criterion: string
  review: string | Date
  on_failure: string
}

const doctrineRaw = readFileSync(DOCTRINE_PATH, 'utf8')
const primerRaw = readFileSync(PRIMER_PATH, 'utf8')

const yamlFences = [...doctrineRaw.matchAll(/```yaml\n([\s\S]*?)```/g)].map((m) => m[1])
const laws: Law[] = yamlFences
  .map((f) => parseYaml(f) as Record<string, unknown>)
  .filter((doc): doc is Law & Record<string, unknown> => typeof doc?.id === 'string')
const mechanisms: Mechanism[] = yamlFences
  .map((f) => parseYaml(f) as Record<string, unknown>)
  .filter((doc) => Array.isArray(doc?.mechanisms))
  .flatMap((doc) => doc.mechanisms as Mechanism[])

describe('doctrine registry: schema', () => {
  it('finds the laws (sanity: an emptied registry must not pass green)', () => {
    expect(laws.length).toBeGreaterThanOrEqual(8)
  })

  it('law ids are unique kebab-case', () => {
    const ids = laws.map((l) => l.id)
    expect(new Set(ids).size).toBe(ids.length)
    const bad = ids.filter((id) => !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(id))
    expect(bad).toEqual([])
  })

  it('every law has a non-empty primer_line, valid cost, and valid tier', () => {
    const violations: string[] = []
    for (const law of laws) {
      if (typeof law.primer_line !== 'string' || law.primer_line.trim().length === 0)
        violations.push(`${law.id}: empty primer_line`)
      if (!COSTS.includes(law.cost as (typeof COSTS)[number]))
        violations.push(`${law.id}: cost "${law.cost}" not in ${COSTS.join('/')}`)
      if (!TIERS.includes(law.tier as (typeof TIERS)[number]))
        violations.push(`${law.id}: tier "${law.tier}" not in ${TIERS.join('/')}`)
      if (typeof law.escalation !== 'string' || law.escalation.length === 0)
        violations.push(`${law.id}: missing escalation state`)
    }
    expect(violations).toEqual([])
  })
})

// A law's enforcement may live in another repo: client material moved to the
// private venturecrane/engagements, and Law 5's correspondence README went with
// it. Such pointers carry an explicit `venturecrane/engagements:` prefix, and
// are resolved against that checkout when it is available. When it is not
// (ss-console CI, deliberately tokenless for client data), the pointer is
// reported as unverifiable rather than silently accepted -- a law claiming
// coverage that cannot be checked must not read the same as one that passed.
const CROSS_REPO_PREFIX = 'venturecrane/engagements:'
const ENGAGEMENTS_DIR =
  process.env.SS_ENGAGEMENTS_DIR || join(process.env.HOME ?? '', 'dev', 'engagements')
const ENGAGEMENTS_GUARD_FILE = join(ENGAGEMENTS_DIR, 'tests', 'engagement-guards.test.ts')
const ENGAGEMENTS_GUARD_FILE_PRESENT = existsSync(ENGAGEMENTS_GUARD_FILE)

describe('doctrine registry: enforcement pointers resolve', () => {
  const unverifiable: string[] = []

  it('every enforcement pointer names a file that exists', (ctx) => {
    const violations: string[] = []
    for (const law of laws) {
      if (!Array.isArray(law.enforcement) || law.enforcement.length === 0) {
        violations.push(`${law.id}: no enforcement pointers`)
        continue
      }
      for (const pointer of law.enforcement) {
        // A cross-repo pointer contains a colon, so it MUST be quoted in the
        // doctrine YAML or it parses as a map instead of a string. Catch that
        // authoring slip here rather than letting it read as a missing file.
        if (typeof pointer !== 'string') {
          violations.push(
            `${law.id}: enforcement entry parsed as ${typeof pointer}, not a string ` +
              `(a pointer containing ":" must be quoted in the YAML)`
          )
          continue
        }
        if (pointer.startsWith(CROSS_REPO_PREFIX)) {
          const rel = pointer.slice(CROSS_REPO_PREFIX.length).trim()
          if (existsSync(join(ENGAGEMENTS_DIR, rel))) continue
          if (existsSync(ENGAGEMENTS_DIR)) {
            violations.push(`${law.id}: "${rel}" missing from the engagements repo`)
          } else {
            unverifiable.push(`${law.id}: "${pointer}"`)
          }
          continue
        }
        if (!existsSync(resolve(pointer))) violations.push(`${law.id}: "${pointer}" does not exist`)
      }
    }
    expect(violations).toEqual([])

    // Pointers into the private repo could not be checked at all when it is
    // not on this machine. Reporting that as a pass would be the same lie the
    // vacuous guards told, so the test is skipped instead: it leaves the
    // passed count and says why, rather than claiming coverage it lacks.
    if (unverifiable.length > 0) {
      ctx.skip(
        `${unverifiable.length} cross-repo enforcement pointer(s) NOT VERIFIED (engagements repo ` +
          `not at ${ENGAGEMENTS_DIR}): ${unverifiable.join(', ')}`
      )
    }
  })

  /**
   * Resolving to a real file is not the same as being invoked. A hook listed as
   * enforcement that no harness event ever runs is built, not wired, and a
   * reader of the law will believe the failure mode is closed when it can recur
   * exactly as before.
   *
   * Caught on 2026-09-09 in PR #2722, which registered
   * .claude/hooks/memory-audit.mjs under Law 2 while nothing invoked it. Two
   * independent reviewers found it; the existing existsSync check could not,
   * because the file did exist. `lib/` is excluded: those are modules imported
   * by wired hooks, not entry points of their own.
   */
  it('every hook cited as enforcement is actually wired into settings.json', () => {
    const settings = readFileSync(resolve('.claude/settings.json'), 'utf8')
    const violations: string[] = []
    for (const law of laws) {
      for (const pointer of law.enforcement ?? []) {
        if (typeof pointer !== 'string') continue
        if (!/^\.claude\/hooks\/[^/]+\.(mjs|sh)$/.test(pointer)) continue
        const basename = pointer.split('/').pop() as string
        if (!settings.includes(basename)) {
          violations.push(
            `${law.id}: "${pointer}" is cited as enforcement but no hook in ` +
              `.claude/settings.json runs it, so nothing invokes it`
          )
        }
      }
    }
    expect(violations).toEqual([])
  })
})

describe('doctrine registry: incident provenance', () => {
  it('every law cites at least one dated incident', () => {
    const violations: string[] = []
    for (const law of laws) {
      if (!Array.isArray(law.incidents) || law.incidents.length === 0) {
        violations.push(`${law.id}: no incidents`)
        continue
      }
      for (const inc of law.incidents) {
        const date = String(inc.date ?? '')
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date))
          violations.push(`${law.id}: incident date "${date}" not YYYY-MM-DD`)
        if (typeof inc.ref !== 'string' || inc.ref.trim().length === 0)
          violations.push(`${law.id}: incident missing ref`)
      }
    }
    expect(violations).toEqual([])
  })
})

describe('doctrine <-> primer parity (the anti-drift pin)', () => {
  // 2026-08-01 consolidation: only judgment-tier laws (primer/radar) are
  // injected in full -- gate-tier laws have real mechanisms enforcing them
  // and are compressed to a pointer line carrying their id, so the injected
  // block stays short enough to be read rather than skimmed. The pin holds
  // in both directions: judgment lines verbatim, gate-tier ids present.
  it('every injected-tier primer_line appears verbatim in reflex-primer.sh', () => {
    const missing = laws
      .filter((law) => law.tier === 'primer' || law.tier === 'radar')
      .filter((law) => !primerRaw.includes(law.primer_line))
      .map((law) => `${law.id}: primer_line not found verbatim in ${PRIMER_PATH}`)
    expect(missing).toEqual([])
  })

  it('every gate-tier law id appears in the primer pointer line', () => {
    const missing = laws
      .filter((law) => law.tier === 'gate' || law.tier === 'prose')
      .filter((law) => !primerRaw.includes(law.id))
      .map((law) => `${law.id}: id not found in ${PRIMER_PATH} pointer line`)
    expect(missing).toEqual([])
  })

  it('the primer names the doctrine file so readers can find the source', () => {
    expect(primerRaw).toContain('docs/doctrine/agent-operating-doctrine.md')
  })
})

describe('doctrine style', () => {
  it('the doctrine file contains no em dashes', () => {
    const lines = doctrineRaw
      .split('\n')
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => line.includes('—'))
      .map(({ n }) => `line ${n}`)
    expect(lines).toEqual([])
  })
})

// Structural check for Law 2, now enforced across a repo boundary.
//
// Dossiers and correspondence live in the private venturecrane/engagements
// repo. Left as-written, this check would keep passing here forever: it
// filters for slugs that HAVE a correspondence/ archive, and after the split
// none do, so the filter empties and the assertion trivially holds. A guard
// that goes green because it stopped looking is the exact failure the split
// was supposed to remove, not introduce.
//
// So the enforcement moved with the material (engagements repo,
// tests/engagement-guards.test.ts, where it now also carries a sanity
// assertion). What remains here is a POINTER test that refuses to pass
// silently: if the engagements repo is not resolvable it skips with a named
// reason rather than asserting nothing, and if correspondence ever reappears
// in this tree it fails, because client material landing back in ss-console
// is itself the regression.
describe('engagement structure: Law 2 has something to gate on', () => {
  const slugs = readdirSync(CUSTOMERS_ROOT).filter(
    (name) => !name.startsWith('_') && statSync(join(CUSTOMERS_ROOT, name)).isDirectory()
  )

  it('finds customer slugs (sanity)', () => {
    expect(slugs.length).toBeGreaterThan(0)
  })

  // Every shape of client material that moved out, not just correspondence/.
  //
  // The first cut of this test guarded correspondence/ alone, and an
  // agreements/ directory (the DPA and Confidentiality Addendum drafted in
  // #2031 by a parallel session, mid-split) walked straight past it. It was
  // caught by a rebase conflict, which is luck, not a control. While both
  // repos can physically hold engagement paths, a concurrent session can put
  // client material in the wrong one; this is what notices.
  //
  // Deliberately shape-based rather than a whitelist: pilot-smokeball carries
  // operational seed code and smd carries our own dogfood onboarding notes,
  // neither of which is client material.
  const CLIENT_MATERIAL = [
    { name: 'dossier.md', why: 'engagement dossier' },
    { name: 'correspondence', why: 'client correspondence archive' },
    { name: 'agreements', why: 'client agreements' },
  ]

  it('no client material has reappeared in ss-console', () => {
    const strays: string[] = []
    for (const slug of slugs) {
      for (const { name, why } of CLIENT_MATERIAL) {
        if (existsSync(join(CUSTOMERS_ROOT, slug, name))) {
          strays.push(`operator/customers/${slug}/${name} (${why})`)
        }
      }
      const clientDocs = existsSync(join(CUSTOMERS_ROOT, slug))
        ? readdirSync(join(CUSTOMERS_ROOT, slug)).filter((f) => /^CLIENT-.*\.md$/.test(f))
        : []
      strays.push(...clientDocs.map((f) => `operator/customers/${slug}/${f} (client document)`))
    }
    expect(
      strays,
      `client material belongs in the private venturecrane/engagements repo, not here:\n` +
        strays.join('\n')
    ).toEqual([])
  })

  // ctx.skip() rather than a console.warn + return: vitest's default reporter
  // SWALLOWS console output, so warn-and-return is indistinguishable from a
  // pass. Skipping moves the test out of the passed count and the reason rides
  // in the test NAME. A check that could not run must never look like one that
  // succeeded -- that is the whole failure class this split exists to remove.
  it(
    ENGAGEMENTS_GUARD_FILE_PRESENT
      ? 'the correspondence-implies-dossier rule is enforced in the engagements repo'
      : `NOT RUN: engagements repo not resolvable at ${ENGAGEMENTS_DIR}; the rule is enforced by that repo's own CI, which this does NOT verify`,
    (ctx) => {
      if (!ENGAGEMENTS_GUARD_FILE_PRESENT) {
        ctx.skip()
        return
      }
      expect(readFileSync(ENGAGEMENTS_GUARD_FILE, 'utf-8')).toContain(
        'every engagement with a correspondence/ archive has a dossier.md'
      )
    }
  )
})

describe('mechanisms under review are falsifiable', () => {
  it('finds the mechanisms block (sanity)', () => {
    expect(mechanisms.length).toBeGreaterThan(0)
  })

  it('each mechanism has a resolving file, a success criterion, and a review date', () => {
    const violations: string[] = []
    for (const m of mechanisms) {
      if (!existsSync(resolve(m.file))) violations.push(`${m.id}: file "${m.file}" does not exist`)
      if (typeof m.success_criterion !== 'string' || m.success_criterion.trim().length === 0)
        violations.push(`${m.id}: no success_criterion`)
      const review =
        m.review instanceof Date ? m.review.toISOString().slice(0, 10) : String(m.review ?? '')
      if (!/^\d{4}-\d{2}-\d{2}$/.test(review))
        violations.push(`${m.id}: review "${review}" not a date`)
      if (typeof m.on_failure !== 'string' || m.on_failure.trim().length === 0)
        violations.push(`${m.id}: no on_failure disposition`)
    }
    expect(violations).toEqual([])
  })
})

/**
 * The venture's repository set is declared, so "don't switch repos" has
 * something to check against.
 *
 * WHY. On 2026-08-25 an agent retired a seat across git, D1, R2 and Fly, then
 * stopped at the overlay's mirrored contract fixture and asked permission to
 * continue. The SOS directive says "never switch repos or ventures without
 * explicit Captain approval"; CLAUDE.md declared `venturecrane/engagements` as a
 * sibling repo of this venture and never declared the overlay. With no declared
 * repo set, the conservative reading of the directive wins and the agent stops
 * at a repo boundary that is internal to the venture. The Captain reports having
 * explained this many times -- which is the signal that it was never written
 * down, not that it was forgotten.
 *
 * This is a guard rather than a note because the failure is silent: an agent
 * that stops does not error, it just asks, and the cost lands on the Captain one
 * conversation at a time.
 */
describe('venture repositories are declared in CLAUDE.md', () => {
  const claudeMd = readFileSync(resolve('CLAUDE.md'), 'utf8')

  // Every repo this venture spans. Adding one here without adding it to
  // CLAUDE.md fails, which is the point.
  const VENTURE_REPOS = [
    'venturecrane/ss-console',
    'venturecrane/hermes-smd-overlay',
    'venturecrane/engagements',
  ]

  for (const repo of VENTURE_REPOS) {
    it(`names ${repo}`, () => {
      expect(claudeMd).toContain(repo)
    })
  }

  it('states that moving between them is not a venture switch', () => {
    // The directive an agent is weighing says "repos or ventures". CLAUDE.md has
    // to answer the question that phrasing raises, not merely list the repos.
    expect(claudeMd).toMatch(/NOT a repo switch/i)
    expect(claudeMd).toMatch(/different venture/i)
  })
})
