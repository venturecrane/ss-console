/**
 * Initiation-card -> router coverage gate (#2222).
 *
 * The runtime fact this encodes: on the ONLY channel a firm actually uses to
 * talk to its Operator (inbound email), the core pre-loads the routed skill's
 * body into the user message and nothing else. The skills index is absent, and
 * `skill_view` is not on the webhook tool surface (live probe, pilot-smokeball,
 * 2026-08-11: 15 tools, no `skill_view`). So `matter-inbox-router/SKILL.md` is
 * the only skill text the model reads on an email turn.
 *
 * That is how the depth-2 introduce row shipped unmapped. The card told the
 * firm to say "Walk me through what you'll do each day and week."; the phrase
 * appeared nowhere in the router body; the model improvised a fluent roster.
 * `tests/initiation-card.test.ts` pins schema, `backed_by` resolution, and seat
 * binding, and none of those could see the gap.
 *
 * The property: every unlocked card row promising an OPERATOR-DIRECTED skill
 * must have its phrasing present in the router body.
 *
 * WHY `SKILL.md` ONLY, never `references/*.md`. The reference files are LISTED
 * by name in the skill body; they are not loaded on the email channel. Matching
 * against them would green exactly the state that produced the incident:
 * `references/routing-rubric.md` already carries the depth-1 phrasing, so a gate
 * that counted references would have passed on an unmapped depth-2 row too. A
 * gate that passes on the state that produced the incident measured nothing.
 *
 * SCOPING PREDICATE (the part that has to be right, or the gate is noise). A row
 * is in scope iff all of:
 *   - its stage is not `locked`,
 *   - `backed_by !== 'core-dialogue'`,
 *   - the backing skill's frontmatter has `metadata.smd.vertical === 'neutral'`.
 *
 * The third clause is principled, not curve-fitted: a `neutral` product skill is
 * one the sender invokes BY NAMING IT, so the naming has to be in the router's
 * text. A vertical (law-firm, marketing-agency) skill is reached by the router
 * CLASSIFYING the message, and classification is already covered by the class
 * table and the rubric. If you are adding a card row and are unsure which side
 * of the line it falls on: does the client's sentence name a procedure the
 * Operator runs on itself, or describe firm work? The first is in scope.
 *
 * ESCAPE HATCH. A row may carry `router_exempt: <reason>` (>= 20 characters).
 * The exemption is reviewable in the diff, which is the point: it keeps the gate
 * from being deleted the first time it is inconvenient.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { parse as parseYaml } from 'yaml'

// Constant paths only - no interpolated resolve() anywhere in this suite.
const CARD_PATHS = {
  'ashton-price': resolve('operator/customers/ashton-price/initiation-card.yaml'),
  'pilot-smokeball': resolve('operator/customers/pilot-smokeball/initiation-card.yaml'),
} as const

const ROUTER_SKILL_PATH = resolve('operator/skills/matter-inbox-router/SKILL.md')
const SKILLS_DIR = resolve('operator/skills')

type Slug = keyof typeof CARD_PATHS
const SEATS = Object.keys(CARD_PATHS) as Slug[]

/** A normalized phrase shorter than this is not a mappable trigger: a
 *  substring hit that short says more about English than about routing.
 *  Four, not five: "Run your self-test." normalizes to four words, and a
 *  floor that exempts a real card row means the gate never guards it. The
 *  false-match risk of a four-word substring is accepted so the gate can
 *  catch a future edit that drops the self-test phrasing from the router. */
const MIN_PHRASE_WORDS = 4

/** Minimum length of a `router_exempt` reason. A one-word reason is a deletion
 *  wearing the escape hatch's clothes. */
const MIN_EXEMPT_REASON = 20

interface CardCommand {
  say: string
  backed_by: string
  router_exempt?: string
}

interface CardStage {
  id: string
  commands: CardCommand[]
  locked?: string
}

interface Card {
  stages: CardStage[]
}

/**
 * Both the card `say:` and the router body pass through this, so a curly
 * apostrophe in one and a straight one in the other still match.
 *
 * 1. drop bracketed placeholders ("[a matter you pick]") - they are the
 *    client's blank to fill, never routing text;
 * 2. fold curly quotes and apostrophes to ASCII;
 * 3. lowercase, reduce everything outside [a-z0-9] to a single space, trim.
 */
function normalize(text: string): string {
  return text
    .replace(/\[[^\]]*\]/g, ' ')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function wordCount(normalized: string): number {
  return normalized ? normalized.split(' ').length : 0
}

/**
 * slug -> metadata.smd.vertical, for every skill that has a body. Slugs come
 * from the directory listing, never from card input, so no path is built out of
 * data this suite reads.
 */
function loadVerticals(): Map<string, string> {
  const map = new Map<string, string>()
  for (const slug of readdirSync(SKILLS_DIR)) {
    const dir = join(SKILLS_DIR, slug)
    if (!statSync(dir).isDirectory()) continue
    const file = join(dir, 'SKILL.md')
    if (!existsSync(file)) continue
    try {
      const match = readFileSync(file, 'utf8').match(/^---\r?\n([\s\S]*?)\r?\n---/)
      if (!match) continue
      const fm = parseYaml(match[1]) as { metadata?: { smd?: { vertical?: unknown } } }
      const vertical = fm?.metadata?.smd?.vertical
      if (typeof vertical === 'string') map.set(slug, vertical.trim())
    } catch {
      // A skill whose frontmatter will not parse is the lede gate's finding,
      // not this one's. Absent from the map means absent from scope.
    }
  }
  return map
}

const VERTICALS = loadVerticals()
const ROUTER_BODY = normalize(readFileSync(ROUTER_SKILL_PATH, 'utf8'))

interface ScopedRow {
  stage: string
  say: string
  backedBy: string
  exempt?: string
}

function scopedRows(slug: Slug): ScopedRow[] {
  const card = parseYaml(readFileSync(CARD_PATHS[slug], 'utf-8')) as Card
  const rows: ScopedRow[] = []
  for (const stage of card.stages) {
    if (stage.locked) continue
    for (const cmd of stage.commands) {
      if (cmd.backed_by === 'core-dialogue') continue
      if (VERTICALS.get(cmd.backed_by) !== 'neutral') continue
      rows.push({
        stage: stage.id,
        say: cmd.say,
        backedBy: cmd.backed_by,
        exempt: cmd.router_exempt,
      })
    }
  }
  return rows
}

describe('the neutral-vertical predicate selects the Operator product skills', () => {
  it('resolves to exactly the skills a sender invokes by naming them', () => {
    const neutral = [...VERTICALS.entries()]
      .filter(([, vertical]) => vertical === 'neutral')
      .map(([slug]) => slug)
      .sort()
    expect(neutral).toEqual([
      'connector-auth-check',
      // ss-console#3423: "how do we work together?" names the Operator itself.
      'operator-help',
      'operator-introduce',
      'operator-self-initiation',
      'operator-self-test',
    ])
  })
})

describe.each(SEATS)('initiation card -> router coverage: %s', (slug) => {
  const rows = scopedRows(slug)

  it('has operator-directed rows to check (the predicate did not silently empty)', () => {
    expect(rows.length).toBeGreaterThan(0)
  })

  for (const row of rows) {
    it(`"${row.say}" is routable on the email channel`, () => {
      if (row.exempt !== undefined) {
        expect(
          row.exempt.trim().length,
          `${slug} stage ${row.stage}: router_exempt on "${row.say}" needs a reason of at least ` +
            `${MIN_EXEMPT_REASON} characters saying why the router body cannot carry this phrasing`
        ).toBeGreaterThanOrEqual(MIN_EXEMPT_REASON)
        return
      }

      const phrase = normalize(row.say)
      expect(
        wordCount(phrase),
        `${slug} stage ${row.stage}: "${row.say}" normalizes to ${wordCount(phrase)} word(s) ` +
          `("${phrase}"), under the ${MIN_PHRASE_WORDS}-word floor this gate trusts. ` +
          `Phrasing too short to map: reword the card row, or exempt it with router_exempt.`
      ).toBeGreaterThanOrEqual(MIN_PHRASE_WORDS)

      expect(
        ROUTER_BODY.includes(phrase),
        `${slug} stage ${row.stage}: the card tells the firm to say "${row.say}" ` +
          `(backed by ${row.backedBy}), but that phrasing is absent from ` +
          `operator/skills/matter-inbox-router/SKILL.md, which is the ONLY skill text ` +
          `loaded on an inbound-email turn. Add the phrasing to the router body itself ` +
          `(references/*.md do not count: they are listed by name, not loaded).`
      ).toBe(true)
    })
  }
})
