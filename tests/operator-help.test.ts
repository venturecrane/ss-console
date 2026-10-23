import { existsSync, readFileSync } from 'node:fs'
import { parse } from 'yaml'
import { describe, expect, it } from 'vitest'

/**
 * The Operator as its own help system (ss-console#3423, increment 1).
 *
 * Anyone at a firm can email "how do we work together?" and get a guide-quality
 * answer. Three things make that answer true rather than fluent, and each is
 * pinned here:
 *
 *   1. The `operator-help` skill exists, is well formed, and carries the
 *      SMD-authored guide and tips it reads at turn time.
 *   2. Every request-type skill carries a `metadata.smd.help` block, which is
 *      the ONLY list of requests the help answer may offer (the seat reads it
 *      through `operator_seat_facts` depth "help"). Each example is a copyable
 *      imperative request, never a how-to question, and its words match its own
 *      routing class's tell, so the request it teaches routes where it should.
 *   3. The router has a Help class with the tie-break in both directions: a
 *      question about HOW to ask submits nothing; an imperative request is never
 *      Help.
 */

const SKILLS = 'operator/skills'
const ROUTER = `${SKILLS}/matter-inbox-router/SKILL.md`
const RUBRIC = `${SKILLS}/matter-inbox-router/references/routing-rubric.md`
const HELP = `${SKILLS}/operator-help`
const EM_DASH = String.fromCharCode(0x2014)

const read = (p: string) => readFileSync(p, 'utf8')
const flat = (s: string) => s.replace(/\s+/g, ' ')

type Front = {
  name?: unknown
  description?: unknown
  version?: unknown
  metadata?: {
    hermes?: { tags?: unknown }
    smd?: {
      vertical?: unknown
      weight?: unknown
      action_class?: unknown
      content_ceiling?: unknown
      connectors?: unknown
      help?: { what?: unknown; example?: unknown; admin_only?: unknown }
    }
  }
}

function frontmatter(path: string): Front {
  const m = read(path).match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!m) throw new Error(`${path}: no frontmatter`)
  return parse(m[1]) as Front
}

/**
 * Every request-type skill, its admin reservation, the router class it routes
 * through (the class table's first column) and a word its example shares with
 * that class's tell. Email filing has no skill of its own: the router itself
 * files the email, so its block lives on the router.
 */
const REQUEST_SKILLS: Record<string, { admin: boolean; klass: string; tell: string }> = {
  'medical-chronology-maintainer': {
    admin: true,
    klass: 'Chronology package request',
    tell: 'chronology',
  },
  'demand-letter-drafter': { admin: true, klass: 'Demand request', tell: 'demand' },
  'document-drafter': {
    admin: true,
    klass: 'Document drafting request',
    tell: 'mediation brief',
  },
  'litigation-status': {
    admin: true,
    klass: 'Litigation status request',
    tell: 'litigation status list',
  },
  'file-work-requests': { admin: false, klass: 'File work request', tell: 'rep letters' },
  'combined-post-intake': { admin: false, klass: 'Combined post intake', tell: 'post' },
  'vendor-invoice-intake': { admin: false, klass: 'Vendor invoice intake', tell: 'invoice' },
  'matter-inbox-router': { admin: false, klass: 'Email filing request', tell: 'email' },
}

/** Requests that name no single matter, so need no XXXXXX placeholder. */
const NO_MATTER_PLACEHOLDER = new Set([
  'litigation-status',
  'combined-post-intake',
  'vendor-invoice-intake',
])

/** One row of the router's class table, by its first cell. */
function tableRow(klass: string): string {
  const row = read(ROUTER)
    .split('\n')
    .find((l) => l.startsWith(`| ${klass} `))
  if (!row) throw new Error(`no class table row for ${klass}`)
  return flat(row)
}

/** One class's bullet in the router's step 4, from its bold marker to the next. */
function bullet(marker: string): string {
  const body = read(ROUTER)
  const at = body.indexOf(`- **${marker}**`)
  if (at < 0) throw new Error(`no bullet ${marker}`)
  const next = body.indexOf('\n   - **', at + 1)
  return flat(body.slice(at, next < 0 ? undefined : next))
}

describe('operator-help: the skill', () => {
  const fm = frontmatter(`${HELP}/SKILL.md`)

  it('has frontmatter shaped like its sibling product skills', () => {
    expect(fm.name).toBe('operator-help')
    expect(typeof fm.version).toBe('string')
    expect(Array.isArray(fm.metadata?.hermes?.tags)).toBe(true)
    expect(fm.metadata?.smd?.vertical).toBe('neutral')
    expect(fm.metadata?.smd?.weight).toBe('light')
    expect(fm.metadata?.smd?.action_class).toBe('read + one reply to the requester')
    expect(typeof fm.metadata?.smd?.content_ceiling).toBe('string')
    expect(fm.metadata?.smd?.connectors).toEqual([])
    // The help skill offers requests; it is not one.
    expect(fm.metadata?.smd?.help).toBeUndefined()
  })

  it('opens its description with a lede that survives the 57-character index cut', () => {
    const desc = String(fm.description).trim()
    const lede = desc.split(/(?<=[.!?])\s/)[0]
    expect(lede.length).toBeLessThanOrEqual(57)
  })

  it('ships the guide and the tips it reads, from the volume first, then the image', () => {
    const body = flat(read(`${HELP}/SKILL.md`))
    expect(body).toContain('`operator_seat_facts` with `depth: "help"`')
    expect(body).toContain('/opt/data/skills/operator-help/references/guide.md')
    expect(body).toContain('/app/skills/operator-help/references/guide.md')
    expect(existsSync(`${HELP}/references/guide.md`)).toBe(true)
    expect(existsSync(`${HELP}/references/tips.yaml`)).toBe(true)
  })

  it('offers only what the seat lists for this sender, and never its machinery or a time', () => {
    const body = flat(read(`${HELP}/SKILL.md`))
    expect(body).toContain('`sender_may_ask` is true')
    expect(body).toContain('List **only** those.')
    expect(body).toContain('Never name a mode, a tool, a skill, a store')
    expect(body).toContain('Never promise a time of any kind')
    expect(body).toContain('addressed **only** to the sender')
    expect(body).toContain('A question about how to ask **submits nothing**.')
  })

  it('carries tips keyed by stable ids, one per struggle kind, in plain sentences', () => {
    const doc = parse(read(`${HELP}/references/tips.yaml`)) as {
      tips: { id: string; when: string; text: string }[]
    }
    const ids = doc.tips.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toEqual(
      expect.arrayContaining([
        'held_identity_check',
        'repeat_ask',
        'vague_request',
        'wrong_matter',
        'wants_specific_document',
        'wants_changes',
        'not_trained_yet',
      ])
    )
    for (const tip of doc.tips) {
      expect(tip.text, tip.id).not.toContain(EM_DASH)
      expect(tip.text.length, tip.id).toBeGreaterThan(20)
      expect(tip.text, tip.id).not.toMatch(/\b(today|tomorrow|within|by end of|hours?)\b/i)
    }
  })

  it('keeps the guide free of em dashes, real matter numbers and timing', () => {
    const guide = read(`${HELP}/references/guide.md`)
    expect(guide).not.toContain(EM_DASH)
    expect(guide).toContain('XXXXXX')
    expect(guide).not.toMatch(/matter \d{3,}/i)
    expect(guide).not.toMatch(/\b(within|by tomorrow|same day)\b/i)
  })
})

describe('operator-help: the requests each skill invites', () => {
  for (const [slug, want] of Object.entries(REQUEST_SKILLS)) {
    describe(slug, () => {
      const help = frontmatter(`${SKILLS}/${slug}/SKILL.md`).metadata?.smd?.help

      it('carries a complete help block with the right reservation', () => {
        expect(help, `${slug} has no metadata.smd.help`).toBeDefined()
        expect(typeof help?.what).toBe('string')
        expect(typeof help?.example).toBe('string')
        expect(help?.admin_only).toBe(want.admin)
      })

      it('gives a copyable imperative example, never a how-to question', () => {
        const example = String(help?.example)
        expect(example).not.toContain(EM_DASH)
        expect(String(help?.what)).not.toContain(EM_DASH)
        expect(example).not.toMatch(/\bhow (do|should|can) (i|we)\b/i)
        expect(example.trim().endsWith('?')).toBe(false)
        if (!NO_MATTER_PLACEHOLDER.has(slug)) expect(example).toContain('XXXXXX')
        expect(example).not.toMatch(/\d{4,}/)
      })

      it("shares a word with its own routing class's tell", () => {
        const example = String(help?.example).toLowerCase()
        expect(example).toContain(want.tell)
        expect(tableRow(want.klass).toLowerCase()).toContain(want.tell)
      })
    })
  }

  it('reserves to administrators exactly the classes the router reserves', () => {
    for (const [slug, want] of Object.entries(REQUEST_SKILLS)) {
      const row = tableRow(want.klass)
      expect(row.includes('admin-reserved'), `${slug}: ${want.klass}`).toBe(want.admin)
    }
  })
})

describe('operator-help: the router Help class', () => {
  it('is in the class table and the rubric, routed to operator-help', () => {
    expect(tableRow('Help request')).toContain('`operator-help`')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('**help request**')
    expect(rubric).toContain('/app/skills/operator-help/SKILL.md')
  })

  it('reads the skill at runtime paths and says what to do when it will not load', () => {
    const b = bullet('Help request')
    expect(b).toContain('/opt/data/skills/operator-help/SKILL.md')
    expect(b).toContain('/app/skills/operator-help/SKILL.md')
    expect(b).toContain('If you cannot read the skill file this turn')
    expect(b).toContain('`depth: "help"`')
    expect(b).toContain('Not admin-reserved')
  })

  it('pins the tie-break in both directions, in both texts', () => {
    const b = bullet('Help request')
    expect(b).toContain('a question about HOW to ask for work submits nothing and is this class')
    expect(b).toContain('("how do I get a demand on 12345?" queues no demand)')
    expect(b).toContain(
      'an imperative request for work ("draft the demand on 12345") is never this class'
    )
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain(
      '13. **Help versus every request class, and versus the introduction.**'
    )
    expect(rubric).toContain('"how do I get a demand on 12345?" queues no demand')
    expect(rubric).toContain('An imperative request for work ("draft the demand on 12345"')
  })

  it('keeps "who are you" with the introduction and "how do I" with Help', () => {
    expect(bullet('Help request')).toContain(
      '"who are you", "what can you see" and "what will you do each day and week" stay the Operator-directed skill request class'
    )
    expect(bullet('Operator-directed skill request')).toContain(
      'is not an introduction: it is the **Help request** class below'
    )
  })

  it("never lets the demand class's own examples read as Help", () => {
    // The falsifier for the tie-break: every demand example in the router is
    // imperative. A how-to phrasing added there would put the two classes in
    // conflict on the same words.
    expect(bullet('Demand request')).not.toMatch(/how do i/i)
  })
})
